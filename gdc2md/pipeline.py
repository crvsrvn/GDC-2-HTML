"""五阶段编排：resolve → fetch → clean → capture → render，每阶段落盘，可从任一阶段续跑。

目录布局（任何一次运行都不覆盖、不删除旧产物）：
  output/<playId>-<slug>/
    meta.json, raw/, cache/        跨次运行复用
    runs/<YYYYMMDD-HHMMSS>/        每次运行独立：sections.json, img/, player.html, README.md
--from 续跑时，新 run 目录缺的中间产物（sections.json / img）从最近一次 run 复制过来。
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import capture, config, llm, render, resolve, subtitles

STAGES = ["resolve", "fetch", "clean", "capture", "render"]
Log = Callable[[str], None]


def run(vault_url: str, cfg: dict, api_key: str, start_stage: str = "resolve", log: Log = print) -> Path:
    if start_stage not in STAGES:
        raise ValueError(f"未知阶段 {start_stage}，可选: {', '.join(STAGES)}")
    begin = STAGES.index(start_stage)
    out_dir = _out_dir(vault_url, cfg)
    run_dir = out_dir / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    prev_run = _latest_run(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log(f"本次输出目录：{run_dir}")

    info = _stage_resolve(vault_url, out_dir, begin <= 0, log)
    lang = cfg["subtitle_lang"]
    cues = _stage_fetch(info, out_dir, lang, begin <= 1, log)
    sections, chapters, summary = _stage_clean(info, cues, out_dir, run_dir, prev_run, cfg, api_key, begin <= 2, log)
    images = _stage_capture(info, sections, run_dir, prev_run, cfg, begin <= 3, log)

    log("[5/5] 生成 Markdown 与播放器 ...")
    render.render_player(info, run_dir)
    md = render.render_markdown(info, chapters, sections, summary, images, run_dir, f"../../raw/{lang}.vtt", cfg)
    log(f"完成：{md}")
    return md


def _stage_resolve(vault_url: str, out_dir: Path, do: bool, log: Log) -> resolve.VideoInfo:
    meta_path = out_dir / "meta.json"
    if not do:
        return resolve.VideoInfo.from_dict(_load(meta_path))
    log("[1/5] 解析页面与播放清单 ...")
    info = resolve.resolve(vault_url)
    _dump(meta_path, info.to_dict())
    log(f"  标题: {info.title}\n  字幕轨: {', '.join(t.lang for t in info.subtitles) or '无'}")
    return info


def _stage_fetch(info: resolve.VideoInfo, out_dir: Path, lang: str, do: bool, log: Log) -> list[subtitles.Cue]:
    cues_path = out_dir / "raw" / "cues.json"
    if not do:
        return [subtitles.Cue(**c) for c in _load(cues_path)]
    log("[2/5] 下载字幕 ...")
    cues = subtitles.fetch_track(resolve.pick_track(info, lang).url)
    (out_dir / "raw").mkdir(exist_ok=True)
    subtitles.write_vtt(cues, out_dir / "raw" / f"{lang}.vtt")
    cues_path.write_text(json.dumps([c.to_dict() for c in cues], ensure_ascii=False), encoding="utf-8")
    log(f"  {len(cues)} 条 cue，时长 {subtitles.fmt_clock(cues[-1].end)}")
    return cues


def _stage_clean(info, cues, out_dir: Path, run_dir: Path, prev_run: Path | None, cfg: dict, api_key: str, do: bool, log: Log):
    sections_path = run_dir / "sections.json"
    if not do:
        _copy_from_prev(prev_run, "sections.json", run_dir)
        data = _load(sections_path)
        sections = [llm.Section.from_dict(s) for s in data["sections"]]
        chapters = [llm.Chapter(**c) for c in data.get("chapters", [])] or [llm.Chapter("Full Talk", "全文", 0, len(sections) - 1)]
        return sections, chapters, data.get("summary", {})
    log(f"[3/5] DeepSeek 清洗 / 分章 / 翻译（模型 {cfg['model']}，thinking={cfg['thinking']}"
        f"{'/' + cfg['reasoning_effort'] if cfg['thinking'] == 'enabled' else ''}，max_tokens={cfg['max_tokens']}）...")
    client = llm.DeepSeek(api_key, cfg, out_dir / "cache")
    sections = llm.clean_and_translate(client, cues, cfg["chunk_seconds"], out_dir / "cache", log)
    chapters = llm.group_chapters(client, sections, log)
    summary = llm.summarize(client, info, chapters, sections)
    _dump(sections_path, {"summary": summary, "chapters": [c.to_dict() for c in chapters],
                          "sections": [s.to_dict() for s in sections]})
    return sections, chapters, summary


def _stage_capture(info, sections, run_dir: Path, prev_run: Path | None, cfg: dict, do: bool, log: Log) -> list[Path | None]:
    if not do:
        _copy_from_prev(prev_run, "img", run_dir)
        return [p if p.exists() else None for p in (run_dir / "img" / f"{n:03d}.jpg" for n in range(1, len(sections) + 1))]
    log("[4/5] 截图 ...")
    stream = resolve.pick_variant(info, int(cfg["video_quality"]))
    return capture.capture_frames(stream, [s.start_sec for s in sections], run_dir / "img", log)


def _out_dir(vault_url: str, cfg: dict) -> Path:
    play_id = re.search(r"/play/(\d+)", vault_url)
    if not play_id:
        raise ValueError(f"不是 GDC Vault 播放页 URL: {vault_url}")
    base = config.out_dir(cfg)
    existing = [p for p in base.glob(f"{play_id.group(1)}-*") if p.is_dir()] if base.exists() else []
    if existing:
        return existing[0]
    slug = re.sub(r"[^a-z0-9]+", "-", vault_url.rsplit("/", 1)[-1].lower()).strip("-")[:60]
    return base / f"{play_id.group(1)}-{slug}"


def _latest_run(out_dir: Path) -> Path | None:
    """最近一次 run 目录；兼容旧版布局（产物直接放在 out_dir 下）。"""
    runs = sorted(p for p in (out_dir / "runs").glob("*") if p.is_dir()) if (out_dir / "runs").exists() else []
    if runs:
        return runs[-1]
    return out_dir if (out_dir / "sections.json").exists() else None


def _copy_from_prev(prev_run: Path | None, name: str, run_dir: Path) -> None:
    src = prev_run / name if prev_run else None
    if not src or not src.exists():
        raise RuntimeError(f"没有可复用的上一次产物 {name}，请从更早的阶段重新运行")
    if src.is_dir():
        shutil.copytree(src, run_dir / name, dirs_exist_ok=True)
    else:
        shutil.copy2(src, run_dir / name)


def _load(path: Path) -> dict | list:
    if not path.exists():
        raise RuntimeError(f"缺少中间产物 {path}，请从更早的阶段重新运行")
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
