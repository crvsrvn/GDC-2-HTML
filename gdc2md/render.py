"""阶段 5：chapters / sections + 元数据 + 截图 → README.md 与 player.html。

文档结构：# 标题 → 元数据 / 概述 → ## 大章节 → ### 小节（时间戳链接 + 截图 + 段落块）。
"""
import html
from datetime import datetime
from pathlib import Path

from . import config
from .llm import Chapter, Section
from .resolve import VideoInfo
from .subtitles import fmt_clock


def render_markdown(info: VideoInfo, chapters: list[Chapter], sections: list[Section], summary: dict,
                    images: list[Path | None], out_dir: Path, vtt_rel: str, cfg: dict) -> Path:
    player = _player_base(out_dir, cfg)
    title_zh = summary.get("title_zh")
    lines = [f"# {title_zh or info.title}", ""] + ([f"*{info.title}*", ""] if title_zh else [])
    lines += _meta_block(info, summary, player)
    lines += _summary_block(summary)
    for ci, ch in enumerate(chapters, 1):
        lines += [f"## {ci}. {_heading(ch)}", ""]
        for si, s in enumerate(sections[ch.first:ch.last + 1], 1):
            idx = ch.first + si - 1
            lines += _section_block(f"{ci}.{si}", s, images[idx], out_dir, player, cfg["image_width"])
    lines += ["---", "", f"原始字幕：`{vtt_rel}`；本文由 GDC2md 生成于 {datetime.now():%Y-%m-%d %H:%M}。", ""]
    path = out_dir / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _player_base(out_dir: Path, cfg: dict) -> str:
    """player.html 的链接前缀。相对链接会被 Markdown 编辑器当本地文件打开，file:// 在部分查看器里不算链接，
    默认用本地 HTTP 服务地址（服务根目录 = cfg["out_dir"]）。"""
    style = cfg["link_style"]
    if style == "http":
        rel = out_dir.resolve().relative_to(config.out_dir(cfg)).as_posix()
        return f"{cfg['http_base'].rstrip('/')}/{rel}/player.html"
    if style == "file":
        return out_dir.resolve().as_uri() + "/player.html"
    return "player.html"


def _heading(x: Chapter | Section) -> str:
    return f"{x.title_zh} / {x.title_en}" if x.title_zh else x.title_en


def _zh_en(zh: str, en: str) -> str:
    return f"{zh}（{en}）" if zh and zh != en and en not in zh else (zh or en)


def _meta_block(info: VideoInfo, summary: dict, player: str) -> list[str]:
    speakers = summary.get("speakers_zh") or info.speakers
    companies = summary.get("companies_zh") or info.companies
    rows = [
        ("讲者", ", ".join(speakers)),
        ("公司", ", ".join(dict.fromkeys(companies))),
        ("会议", info.conference),
        ("Track", _zh_en(summary.get("track_zh", ""), info.track)),
        ("原页", f"<{info.vault_url}>"),
        ("播放器", f"[player.html]({player})（本地，支持时间跳转） · [官方播放器]({info.player_url()})"),
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


def _section_block(num: str, s: Section, image: Path | None, out_dir: Path, player: str, width: str) -> list[str]:
    t = int(s.start_sec)
    out = [f"### {num} {_heading(s)}", "",
           f"[▶ {fmt_clock(s.start_sec)}]({player}?t={t}) · cue #{s.start_idx}–#{s.end_idx}", ""]
    if image:
        out += [f'<img src="{image.relative_to(out_dir).as_posix()}" alt="{fmt_clock(s.start_sec)}" width="{width}">', ""]
    if s.fallback:
        out += ["> ⚠ 此节 AI 处理失败，以下为未清洗的原始字幕。", ""]
    zh = [b["zh"] for b in s.blocks if b.get("zh")]
    en = [b["en"] for b in s.blocks if b.get("en")]
    if zh:
        out += _paragraphs(zh) + ["<details><summary>English (cleaned)</summary>", ""] + _paragraphs(en) + ["</details>", ""]
    else:
        out += _paragraphs(en)
    return out


def _paragraphs(blocks: list[str]) -> list[str]:
    out = []
    for b in blocks:
        out += [b, ""]
    return out


_PLAYER_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{title}</title>
<link href="https://vjs.zencdn.net/7.20.3/video-js.css" rel="stylesheet">
<style>body{{margin:0;background:#111;color:#ddd;font-family:sans-serif}}
h1{{font-size:16px;margin:8px 12px}} .video-js{{width:100vw;height:calc(100vh - 40px)}}</style>
</head><body>
<h1>{title}</h1>
<video-js id="v" class="video-js vjs-big-play-centered" controls preload="auto"></video-js>
<script src="https://vjs.zencdn.net/7.20.3/video.min.js"></script>
<script>
  // 用法：player.html?t=秒 或 player.html#t=秒，加载完成后定位到指定时间
  var q = new URLSearchParams(location.search).get('t') || new URLSearchParams(location.hash.slice(1)).get('t');
  var t = parseFloat(q || '0') || 0;
  var p = videojs('v', {{playbackRates: [0.75, 1, 1.25, 1.5, 2]}});
  p.src({{src: {src!r}, type: 'application/x-mpegURL'}});
  p.one('loadedmetadata', function () {{ if (t > 0) p.currentTime(t); p.play(); }});
</script>
</body></html>
"""


def render_player(info: VideoInfo, out_dir: Path) -> Path:
    path = out_dir / "player.html"
    path.write_text(_PLAYER_HTML.format(title=html.escape(info.title), src=info.master_url), encoding="utf-8")
    return path
