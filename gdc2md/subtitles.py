"""阶段 2：HLS WebVTT 字幕轨 → 去重合并的 cue 列表。"""
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urljoin

from .httpclient import get_text

_TIMING = re.compile(r"(\d{1,2}:)?(\d{2}):(\d{2})\.(\d{3})")


@dataclass
class Cue:
    idx: int
    start: float
    end: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_track(playlist_url: str, workers: int = 8) -> list[Cue]:
    playlist = get_text(playlist_url)
    seg_urls = [urljoin(playlist_url, ln.strip()) for ln in playlist.splitlines()
                if ln.strip() and not ln.startswith("#")]
    if not seg_urls:
        raise RuntimeError("字幕子清单为空")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        segments = list(pool.map(get_text, seg_urls))
    return merge_segments(segments)


def merge_segments(segments: list[str]) -> list[Cue]:
    """HLS 分片边界处同一 cue 会重复出现，按 (start, end, text) 去重。"""
    seen: set[tuple] = set()
    raw: list[tuple[float, float, str]] = []
    for seg in segments:
        for start, end, text in parse_vtt(seg):
            key = (round(start, 3), round(end, 3), text)
            if key not in seen:
                seen.add(key)
                raw.append((start, end, text))
    raw.sort(key=lambda c: (c[0], c[1]))
    return [Cue(i, s, e, t) for i, (s, e, t) in enumerate(raw)]


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        timing_at = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if timing_at < 0:
            continue
        times = _TIMING.findall(lines[timing_at])
        if len(times) < 2:
            continue
        body = " ".join(re.sub(r"<[^>]+>", "", ln).strip() for ln in lines[timing_at + 1:]).strip()
        if body:
            cues.append((_to_sec(times[0]), _to_sec(times[1]), body))
    return cues


def _to_sec(m: tuple[str, str, str, str]) -> float:
    h = int(m[0].rstrip(":")) if m[0] else 0
    return round(h * 3600 + int(m[1]) * 60 + int(m[2]) + int(m[3]) / 1000, 3)


def fmt_vtt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fmt_clock(sec: float) -> str:
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def write_vtt(cues: list[Cue], path: Path) -> None:
    out = ["WEBVTT", ""]
    for c in cues:
        out += [str(c.idx), f"{fmt_vtt_ts(c.start)} --> {fmt_vtt_ts(c.end)}", c.text, ""]
    path.write_text("\n".join(out), encoding="utf-8")
