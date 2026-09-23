"""阶段 4：chapters / sections + 元数据 → README.md（纯文本版；带内嵌视频的阅读版见 htmldoc）。

文档结构：# 标题 → 元数据 / 概述 → ## 大章节 → ### 小节（时间戳 + 段落块）。
"""
from datetime import datetime
from pathlib import Path

from .llm import Chapter, Section
from .resolve import VideoInfo
from .subtitles import fmt_clock


def render_markdown(info: VideoInfo, chapters: list[Chapter], sections: list[Section], summary: dict,
                    out_dir: Path, vtt_rel: str) -> Path:
    title_zh = summary.get("title_zh")
    lines = [f"# {title_zh or info.title}", ""] + ([f"*{info.title}*", ""] if title_zh else [])
    lines += _meta_block(info, summary)
    lines += _summary_block(summary)
    for ci, ch in enumerate(chapters, 1):
        lines += [f"## {ci}. {_heading(ch)}", ""]
        for si, s in enumerate(sections[ch.first:ch.last + 1], 1):
            lines += _section_block(f"{ci}.{si}", s)
    lines += ["---", "", f"原始字幕：`{vtt_rel}`；本文由 GDC2md 生成于 {datetime.now():%Y-%m-%d %H:%M}。", ""]
    path = out_dir / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _heading(x: Chapter | Section) -> str:
    return f"{x.title_zh} / {x.title_en}" if x.title_zh else x.title_en


def _zh_en(zh: str, en: str) -> str:
    return f"{zh}（{en}）" if zh and zh != en and en not in zh else (zh or en)


def _meta_block(info: VideoInfo, summary: dict) -> list[str]:
    speakers = summary.get("speakers_zh") or info.speakers
    companies = summary.get("companies_zh") or info.companies
    rows = [
        ("讲者", ", ".join(speakers)),
        ("公司", ", ".join(dict.fromkeys(companies))),
        ("会议", info.conference),
        ("Track", _zh_en(summary.get("track_zh", ""), info.track)),
        ("原页", f"<{info.vault_url}>"),
        ("播放器", f"[官方播放器]({info.player_url()})（内嵌视频见同项目根目录下的 HTML 版）"),
    ]
    out = [f"- **{k}**：{v}" for k, v in rows if v]
    overview_zh = summary.get("overview_zh")
    if overview_zh:
        out += ["", f"> {overview_zh}", ">", f"> *{info.overview}*"]
    elif info.overview:
        out += ["", f"> {info.overview}"]
    return out + [""]


def _summary_block(summary: dict) -> list[str]:
    if not summary.get("summary_zh"):
        return []
    out = ["## 概述", "", summary["summary_zh"], ""]
    if summary.get("takeaways_zh"):
        out += ["**要点**", ""] + [f"- {t}" for t in summary["takeaways_zh"]] + [""]
    return out


def _section_block(num: str, s: Section) -> list[str]:
    out = [f"### {num} {_heading(s)}", "",
           f"`{fmt_clock(s.start_sec)}` · cue #{s.start_idx}–#{s.end_idx}", ""]
    if s.fallback:
        out += ["> ⚠ 此节 AI 处理失败，以下为未清洗的原始字幕。", ""]
    if not s.groups:
        return out + _body(s.blocks)
    for gi, g in enumerate(s.groups, 1):
        title = f"{g['title_zh']} / {g['title_en']}" if g["title_zh"] else g["title_en"]
        out += [f"#### {num}.{gi} {title}", "", f"`{fmt_clock(g.get('start_sec', s.start_sec))}`", ""]
        out += _body(s.blocks[g["first"]:g["last"] + 1])
    return out


def _body(blocks: list[dict]) -> list[str]:
    zh = [b["zh"] for b in blocks if b.get("zh")]
    en = [b["en"] for b in blocks if b.get("en")]
    if zh:
        return _paragraphs(zh) + ["<details><summary>English (cleaned)</summary>", ""] + _paragraphs(en) + ["</details>", ""]
    return _paragraphs(en)


def _paragraphs(blocks: list[str]) -> list[str]:
    out = []
    for b in blocks:
        out += [b, ""]
    return out
