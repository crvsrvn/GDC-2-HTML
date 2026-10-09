"""阶段 1：GDC Vault 页面 → 元数据 + HLS 主清单 + 字幕轨。"""
import html
import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin

from .httpclient import get_text

BLAZE_ORIGIN = "https://gdcvault.blazestreaming.com"
# 播放器脚本在 aliases.json 无记录时使用的旧版地址格式
LEGACY_TAIL = "/38549d9a185b445888c6fbc2e2e792e9/50413c5b8a95441e919f0a1579606e47/index.m3u8"


@dataclass
class Track:
    lang: str
    name: str
    url: str


@dataclass
class VideoInfo:
    vault_url: str
    play_id: str
    title: str = ""
    overview: str = ""
    speakers: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    track: str = ""
    conference: str = ""
    blaze_id: str = ""
    master_url: str = ""
    variants: dict[int, str] = field(default_factory=dict)  # 视频高度 → 子清单 URL
    subtitles: list[Track] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VideoInfo":
        d = dict(d)
        d["variants"] = {int(k): v for k, v in d["variants"].items()}
        d["subtitles"] = [Track(**t) for t in d["subtitles"]]
        return cls(**d)

    def player_url(self) -> str:
        return f"{BLAZE_ORIGIN}/?id={self.blaze_id}"


def resolve(vault_url: str) -> VideoInfo:
    m = re.search(r"/play/(\d+)", vault_url)
    if not m:
        raise ValueError(f"不是 GDC Vault 播放页 URL: {vault_url}")
    info = VideoInfo(vault_url=vault_url, play_id=m.group(1))

    page = get_text(vault_url)
    if 'id="form_login_page"' in page:  # 未登录访问会员视频时被重定向到 /login
        raise RuntimeError("该视频需要登录：请在主菜单 4 设置 GDC Vault Cookie；已设置过则可能已过期，请重新复制")
    _parse_metadata(page, info)

    m = re.search(r"blazestreaming\.com/\?id=([0-9a-f]{32})", page)
    if not m:
        raise RuntimeError("页面中未找到播放器 iframe，可能是当前账号无权观看或页面结构已变化")
    info.blaze_id = m.group(1)
    info.master_url = _lookup_playback_url(info.blaze_id)
    _parse_master(get_text(info.master_url), info.master_url, info)
    return info


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _dd_after(page: str, label: str) -> str:
    m = re.search(r"<strong>" + re.escape(label) + r"</strong>\s*</dt>\s*<dd[^>]*>(.*?)</dd>", page, re.S)
    return m.group(1) if m else ""


def _split_list(raw: str) -> list[str]:
    # 页面用逗号 + 大段空白分隔多个人名/公司名；公司名本身可含 ", Inc."
    return [_clean(x) for x in re.split(r",\s{2,}", raw) if _clean(x)]


def _parse_metadata(page: str, info: VideoInfo) -> None:
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    info.title = _clean(m.group(1)) if m else info.play_id
    m = re.search(r"<h3>Overview:</h3>\s*</dt>\s*<dd>\s*<p[^>]*>(.*?)</p>", page, re.S)
    info.overview = _clean(m.group(1)) if m else ""
    info.speakers = _split_list(_dd_after(page, "Speaker(s):"))
    info.companies = _split_list(_dd_after(page, "Company Name(s):"))
    info.track = _clean(_dd_after(page, "Track / Format:"))
    m = re.search(r"'conferenceName':\s*'([^']*)'", page)
    info.conference = m.group(1) if m else ""


def _lookup_playback_url(blaze_id: str) -> str:
    import json
    try:
        aliases = json.loads(get_text(f"{BLAZE_ORIGIN}/aliases.json")).get("aliases", {})
        url = aliases.get(blaze_id, {}).get("playbackUrl")
        if url:
            return url
    except (RuntimeError, ValueError):
        pass
    return f"https://cdn-a.blazestreaming.com/out/v1/{blaze_id}{LEGACY_TAIL}"


def _attrs(line: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line)}


def _parse_master(text: str, master_url: str, info: VideoInfo) -> None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF:"):
            a = _attrs(ln)
            height = int(a.get("RESOLUTION", "0x0").split("x")[1])
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                info.variants[height] = urljoin(master_url, lines[i + 1])
        elif ln.startswith("#EXT-X-MEDIA:") and "TYPE=SUBTITLES" in ln:
            a = _attrs(ln)
            if "URI" in a:
                info.subtitles.append(Track(a.get("LANGUAGE", ""), a.get("NAME", ""), urljoin(master_url, a["URI"])))
    if not info.variants:
        raise RuntimeError("主清单中没有视频流")


def pick_variant(info: VideoInfo, quality: int) -> str:
    """选不高于 quality 的最高档；都高于则取最低档。"""
    heights = sorted(info.variants)
    ok = [h for h in heights if h <= quality]
    return info.variants[ok[-1] if ok else heights[0]]


def pick_track(info: VideoInfo, lang: str) -> Track:
    for t in info.subtitles:
        if t.lang == lang:
            return t
    have = ", ".join(f"{t.lang}({t.name})" for t in info.subtitles) or "无"
    raise RuntimeError(f"没有语言为 {lang} 的字幕轨，可用: {have}")
