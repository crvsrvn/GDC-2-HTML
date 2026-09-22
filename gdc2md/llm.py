"""阶段 3：DeepSeek 清洗英文字幕、分小节、拆段落块、翻译为中文，再归组为大章节并生成概述。"""
import hashlib
import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable

import requests

from .subtitles import Cue, fmt_clock

PROMPT_VERSION = 4
Log = Callable[[str], None]


@dataclass
class Section:
    """小节：连续的一段 cue，内容拆成若干段落块（blocks: [{"en","zh"}]）。"""
    title_en: str
    title_zh: str
    start_idx: int
    end_idx: int
    start_sec: float
    blocks: list[dict] = field(default_factory=list)
    fallback: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Section":
        d = dict(d)
        if "blocks" not in d:  # 兼容旧格式（整段 en/zh）
            d["blocks"] = [{"en": d.pop("en", ""), "zh": d.pop("zh", "")}]
        return cls(**d)


@dataclass
class Chapter:
    """大章节：连续的若干小节（按 sections 下标 [first, last]）。"""
    title_en: str
    title_zh: str
    first: int
    last: int

    def to_dict(self) -> dict:
        return asdict(self)


class OutputTruncated(RuntimeError):
    """模型输出被 max_tokens 截断（finish_reason == length）。"""


class DeepSeek:
    def __init__(self, api_key: str, cfg: dict, debug_dir: Path | None = None):
        """所有请求参数（模型、思考模式、max_tokens、temperature）都取自全局配置 cfg。"""
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.thinking = {"type": cfg["thinking"]}
        if cfg["thinking"] == "enabled":
            self.thinking["reasoning_effort"] = cfg["reasoning_effort"]
        self.max_tokens = int(cfg["max_tokens"])
        self.temperature = float(cfg["temperature"])
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.debug_dir = debug_dir
        self.total_tokens = 0

    def verify(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self.headers, timeout=15)
        except requests.RequestException as e:
            return False, f"网络错误: {e}"
        if r.status_code == 200:
            return True, "OK"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"

    def chat_json(self, system: str, user: str) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "thinking": self.thinking,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        last = ""
        for attempt in range(3):
            try:
                r = requests.post(f"{self.base_url}/chat/completions", headers=self.headers, json=body, timeout=(30, 600))
            except requests.RequestException as e:
                last = str(e)
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"DeepSeek HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            self.total_tokens += data.get("usage", {}).get("total_tokens", 0)
            choice = data["choices"][0]
            finish = choice.get("finish_reason")
            content = choice["message"].get("content") or ""
            if finish == "length":
                raise OutputTruncated(f"输出超过 max_tokens={self.max_tokens}")
            try:
                return json.loads(_strip_fence(content), strict=False)
            except json.JSONDecodeError as e:
                last = f"返回内容不是合法 JSON（finish_reason={finish}，{len(content)} 字符，{e.msg}）"
                self._dump_bad(content, last)
        raise RuntimeError(f"DeepSeek 调用失败: {last}")

    def _dump_bad(self, content: str, reason: str) -> None:
        if not self.debug_dir:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path = self.debug_dir / f"bad_{int(time.time() * 1000)}.txt"
        path.write_text(f"# {reason}\n{content}", encoding="utf-8")


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        s = s.rsplit("```", 1)[0]
    return s.strip()


# ---------- 分块 ----------

def chunk_cues(cues: list[Cue], target_sec: float, min_tail_sec: float = 90) -> list[list[Cue]]:
    """按目标时长切块；到达目标后在最近的停顿处（cue 间隔 ≥ 0.8s）切开，超时 60s 强制切。"""
    chunks: list[list[Cue]] = []
    cur: list[Cue] = []
    for i, c in enumerate(cues):
        cur.append(c)
        elapsed = c.end - cur[0].start
        gap = cues[i + 1].start - c.end if i + 1 < len(cues) else 999
        if elapsed >= target_sec and (gap >= 0.8 or elapsed >= target_sec + 60):
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and cur[-1].end - cur[0].start < min_tail_sec:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    return chunks


# ---------- 提示词 ----------

SYSTEM_CLEAN = """你是游戏开发领域的技术编辑与译者。输入是一段 GDC 技术演讲的自动生成英文字幕，按 cue 编号排列并附时间戳。请完成：

1. 按讲述主题的变化把这段字幕划分为若干小节（每节通常 2~6 分钟；如果整段都是同一主题，可以只有 1 节）。
2. 为每节生成简洁、信息量足的英文标题和中文标题。
3. 每节内容再按微主题拆成若干段落块（字段 blocks，通常 3~8 块，每块 2~5 句），每块是一个完整的小论点、步骤或例子；不要把整节堆成一大段。
4. 每块的英文正文（字段 en）：逐句保留讲者原意与全部技术细节、数字、术语、产品名和例子；只删除口头禅（um / uh / you know / like / so / right? / okay 等）、无意义重复、口误改口、寒暄和过渡废话；把被切碎的字幕合并为通顺的句子；修正明显的语音识别错词（尤其是术语拼写）。禁止概括、禁止省略内容、禁止添加原文没有的信息，保持第一人称。
5. 每块的中文译文（字段 zh）：面向游戏开发者，术语按业界惯例处理——常用术语保留英文或写成"中文（English）"，例如 draw call、frame time、LOD、shader、ECS、Burst、profiler 等；语句通顺、技术准确、不要逐词直译。
6. 如果这段的开头明显延续上一节（会告诉你上一节标题），把第一节的 continues 设为 true，其标题可留空。

只输出严格 JSON，不要 markdown 代码块，格式：
{"sections":[{"continues":false,"title_en":"","title_zh":"","start":起始cue编号,"end":结束cue编号,"blocks":[{"en":"...","zh":"..."}]}]}
硬性约束：各节 [start,end] 必须按顺序连续、无重叠、无缝隙，合起来恰好覆盖输入给出的 cue 编号范围；start/end 为整数；每节 blocks 非空且每块 en 非空。"""

SYSTEM_CHAPTERS = """你是游戏开发领域的技术编辑。下面是一场 GDC 演讲按顺序排列的小节列表（编号、起始时间、英文标题 / 中文标题）。请把相邻的小节归组为若干大章节（通常 3~7 章，节数很少时可以只有 1~2 章），每章是一个完整的主题阶段（如背景、问题分析、方案、案例、结果与总结），并为每章生成简洁的英文标题和中文标题。
只输出严格 JSON：{"chapters":[{"title_en":"","title_zh":"","first":起始小节编号,"last":结束小节编号}]}
硬性约束：各章 [first,last] 按顺序连续、无重叠、无缝隙，合起来恰好覆盖全部小节编号；first/last 为整数。"""

SYSTEM_SUMMARY = """你是游戏开发领域的技术编辑与译者。根据一场 GDC 演讲的标题、官方简介、讲者、公司、Track 和章节结构，用中文输出：
1. title_zh：演讲标题中译（游戏名用官方中文名，如有"Presented by X"译为"由 X 呈现"）。
2. overview_zh：官方简介中译，面向游戏开发者，术语按业界惯例。
3. speakers_zh：讲者列表，与输入顺序、数量一一对应；人名保留原文（不要音译）。
4. companies_zh：公司列表，与输入顺序、数量一一对应；有通用中文名的写"中文名（English）"，否则保留原文。
5. track_zh：Track/Format 中译。
6. summary_zh：3~5 句话的整体概述（讲了什么、核心方法、结论）。
7. takeaways_zh：3~6 条对游戏开发者最有价值的要点，每条一句话。
只输出严格 JSON：{"title_zh":"","overview_zh":"","speakers_zh":[],"companies_zh":[],"track_zh":"","summary_zh":"","takeaways_zh":[]}"""


def _chunk_prompt(chunk: list[Cue], prev_title: str, extra: str = "") -> str:
    lines = [f"#{c.idx} [{fmt_clock(c.start)}] {c.text}" for c in chunk]
    head = (f"上一节标题：{prev_title or '（无，这是演讲开头）'}\n"
            f"本段 cue 编号范围：[{chunk[0].idx}, {chunk[-1].idx}]\n{extra}\n字幕：\n")
    return head + "\n".join(lines)


# ---------- 小节：清洗 / 拆块 / 翻译 ----------

def clean_and_translate(client: DeepSeek, cues: list[Cue], chunk_seconds: float, cache_dir: Path, log: Log) -> list[Section]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    chunks = chunk_cues(cues, chunk_seconds)
    log(f"字幕共 {len(cues)} 条 cue，切为 {len(chunks)} 块送 DeepSeek")
    sections: list[Section] = []
    for n, chunk in enumerate(chunks, 1):
        prev_title = sections[-1].title_en if sections else ""
        log(f"  [{n}/{len(chunks)}] {fmt_clock(chunk[0].start)} ~ {fmt_clock(chunk[-1].end)} ...")
        raw = _cached(cache_dir, chunk, prev_title, lambda: _process_chunk(client, chunk, prev_title, log))
        _merge_into(sections, raw, cues)
    log(f"分节完成：{len(sections)} 节，累计 tokens {client.total_tokens}")
    return sections


def _cached(cache_dir: Path, chunk: list[Cue], prev_title: str, compute: Callable[[], list[dict]]) -> list[dict]:
    payload = f"{PROMPT_VERSION}|{prev_title}|" + "\n".join(f"{c.idx}|{c.text}" for c in chunk)
    key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    path = cache_dir / f"clean_{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    result = compute()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def _process_chunk(client: DeepSeek, chunk: list[Cue], prev_title: str, log: Log) -> list[dict]:
    lo, hi = chunk[0].idx, chunk[-1].idx
    extra = ""
    for attempt in range(2):
        try:
            data = client.chat_json(SYSTEM_CLEAN, _chunk_prompt(chunk, prev_title, extra))
        except OutputTruncated as e:
            return _split_and_retry(client, chunk, prev_title, log, str(e))
        secs = data.get("sections") if isinstance(data, dict) else None
        err = _validate_sections(secs, lo, hi)
        if not err:
            return secs
        log(f"    分节结果不合规（{err}），重试 {attempt + 1}/2")
        extra = f"注意：上一次输出有误——{err}。请严格遵守约束重新输出。"
    log("    仍不合规，本块退化为单节原文")
    return _fallback(chunk)


def _split_and_retry(client: DeepSeek, chunk: list[Cue], prev_title: str, log: Log, reason: str) -> list[dict]:
    """输出被截断：把块对半拆开分别处理；太小拆不动就降级。"""
    if len(chunk) < 20:
        log(f"    {reason}，块已很小无法再拆，退化为单节原文")
        return _fallback(chunk)
    mid = len(chunk) // 2
    log(f"    {reason}，拆为两半分别处理")
    first = _process_chunk(client, chunk[:mid], prev_title, log)
    second = _process_chunk(client, chunk[mid:], first[-1].get("title_en") or prev_title, log)
    return first + second


def _fallback(chunk: list[Cue]) -> list[dict]:
    return [{"continues": False, "title_en": f"Segment {fmt_clock(chunk[0].start)}", "title_zh": "",
             "start": chunk[0].idx, "end": chunk[-1].idx,
             "blocks": [{"en": " ".join(c.text for c in chunk), "zh": ""}], "fallback": True}]


def _validate_sections(secs, lo: int, hi: int) -> str:
    if not isinstance(secs, list) or not secs:
        return "sections 为空"
    expect = lo
    for s in secs:
        try:
            a, b = int(s["start"]), int(s["end"])
        except (KeyError, TypeError, ValueError):
            return "start/end 缺失或不是整数"
        if a != expect:
            return f"小节起点 {a} 与预期 {expect} 不连续"
        if b < a:
            return f"小节 [{a},{b}] 终点小于起点"
        blocks = s.get("blocks")
        if not isinstance(blocks, list) or not blocks or any(not str(b_.get("en", "")).strip() for b_ in blocks if isinstance(b_, dict)):
            return f"小节 [{a},{b}] 的 blocks 为空或含空 en"
        expect = b + 1
    if expect != hi + 1:
        return f"最后一节终点 {expect - 1} 未覆盖到 {hi}"
    return ""


def _clean_blocks(raw_blocks) -> list[dict]:
    return [{"en": str(b.get("en", "")).strip(), "zh": str(b.get("zh", "")).strip()}
            for b in raw_blocks if isinstance(b, dict) and str(b.get("en", "")).strip()]


def _merge_into(sections: list[Section], raw: list[dict], cues: list[Cue]) -> None:
    for s in raw:
        blocks = _clean_blocks(s.get("blocks", []))
        if s.get("continues") and sections and not s.get("fallback"):
            sections[-1].end_idx = int(s["end"])
            sections[-1].blocks.extend(blocks)
            continue
        start = int(s["start"])
        sections.append(Section(
            title_en=str(s.get("title_en") or "").strip() or f"Section {len(sections) + 1}",
            title_zh=str(s.get("title_zh") or "").strip(),
            start_idx=start, end_idx=int(s["end"]), start_sec=cues[start].start,
            blocks=blocks, fallback=bool(s.get("fallback", False)),
        ))


# ---------- 大章节归组 ----------

def group_chapters(client: DeepSeek, sections: list[Section], log: Log) -> list[Chapter]:
    listing = "\n".join(f"{i}. [{fmt_clock(s.start_sec)}] {s.title_en} / {s.title_zh}" for i, s in enumerate(sections))
    extra = ""
    for attempt in range(2):
        data = client.chat_json(SYSTEM_CHAPTERS, f"小节编号范围 [0, {len(sections) - 1}]\n{extra}\n{listing}")
        raw = data.get("chapters") if isinstance(data, dict) else None
        err = _validate_chapters(raw, len(sections))
        if not err:
            chapters = [Chapter(str(c.get("title_en", "")).strip(), str(c.get("title_zh", "")).strip(), int(c["first"]), int(c["last"]))
                        for c in raw]
            log(f"归组完成：{len(chapters)} 章")
            return chapters
        log(f"    归组结果不合规（{err}），重试 {attempt + 1}/2")
        extra = f"注意：上一次输出有误——{err}。请严格遵守约束重新输出。"
    log("    归组失败，退化为单章")
    return [Chapter("Full Talk", "全文", 0, len(sections) - 1)]


def _validate_chapters(raw, n: int) -> str:
    if not isinstance(raw, list) or not raw:
        return "chapters 为空"
    expect = 0
    for c in raw:
        try:
            a, b = int(c["first"]), int(c["last"])
        except (KeyError, TypeError, ValueError):
            return "first/last 缺失或不是整数"
        if a != expect or b < a:
            return f"章节 [{a},{b}] 与预期起点 {expect} 不连续"
        expect = b + 1
    return "" if expect == n else f"最后一章终点 {expect - 1} 未覆盖到 {n - 1}"


# ---------- 概述与头部中译 ----------

def summarize(client: DeepSeek, info, chapters: list[Chapter], sections: list[Section]) -> dict:
    outline = []
    for c in chapters:
        outline.append(f"- {c.title_en} / {c.title_zh}")
        outline += [f"  - {s.title_en} / {s.title_zh}" for s in sections[c.first:c.last + 1]]
    user = (f"标题：{info.title}\n官方简介：{info.overview}\n讲者：{json.dumps(info.speakers, ensure_ascii=False)}\n"
            f"公司：{json.dumps(info.companies, ensure_ascii=False)}\nTrack：{info.track}\n章节结构：\n" + "\n".join(outline))
    data = client.chat_json(SYSTEM_SUMMARY, user)
    out = {k: str(data.get(k, "")) for k in ("title_zh", "overview_zh", "track_zh", "summary_zh")}
    for k, src in (("speakers_zh", info.speakers), ("companies_zh", info.companies)):
        vals = [str(x) for x in data.get(k, [])]
        out[k] = vals if len(vals) == len(src) else list(src)  # 数量对不上就退回原文
    out["takeaways_zh"] = [str(x) for x in data.get("takeaways_zh", [])]
    return out
