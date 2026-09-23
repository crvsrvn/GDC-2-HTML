"""阶段 5 附属产物：与 README.md 同源的数据 → 项目根目录下的 <演讲原标题>.html。

样式与脚本取自 assets/html-style.html（html-style 插件模板，原样复制，不改写）：右侧大纲、主题跟随。
视频：hls.js 播放 GDC 的 HLS 流（直接加载 video_quality 对应档位的子清单，默认 1080p），每个小节/小段留一个 16:9 预览位：
  - 预览图：打开页面后，一个不显示的截帧 <video> 在后台依次定位到各预览位的秒数，截帧存为 JPEG 放进预览位；
    视口内未截的预览位优先，其余按文档顺序。截过的预览图在页面关闭前一直保留，不再重复下载；
  - 播放：全文共用一个可见播放器，点击时间戳或预览位时移过去并从该秒开始播放；
    播放中预览位滚出视口时，播放器浮到左下角继续播放，暂停后回到原位。
HTML 是自包含的单文件（只依赖 CDN 上的字体、hls.js 与视频流），直接双击打开即可，不需要本地服务；
放在 output/<项目>/ 根目录，每次运行覆盖为最新一次 run。
"""
import html
import json
import re
from datetime import datetime
from pathlib import Path

from . import render
from .llm import Chapter, Section
from .resolve import VideoInfo
from .subtitles import fmt_clock

_TEMPLATE = Path(__file__).with_name("assets") / "html-style.html"
_HLS_JS = "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.6.15/hls.min.js"

# 页面特有样式：英文副标题、时间戳行、视频预览位与共享播放器；只用 --hs-* 令牌取色（视频底色除外）
_PAGE_STYLE = """<style>
.hs-doc .gd-en { display: block; margin-top: 2px; font-size: .68em; font-weight: 400; color: var(--hs-muted); }
.hs-toc .gd-en { display: none; }
.hs-doc .gd-ts { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; font-size: 14px; }
.hs-doc .gd-ts a { font-weight: 700; text-decoration: none; }
.hs-doc .gd-cue { color: var(--hs-faint); font-family: var(--hs-mono); font-size: 12.5px; }
.hs-doc .gd-quote-en { color: var(--hs-muted); font-style: italic; }
.gd-slot { position: relative; aspect-ratio: 16 / 9; max-width: 100%; margin: 12px 0 22px; display: grid; place-items: center;
  background: var(--hs-code-bg); border: 1px solid var(--hs-line); border-radius: 6px; overflow: hidden; }
.gd-poster { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.gd-slot-play { position: relative; display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; font: 700 14px/1 var(--hs-font);
  color: var(--hs-accent); background: var(--hs-surface); border: 1px solid var(--hs-line); border-radius: 999px; cursor: pointer; }
.gd-slot-play::before { content: ""; border-left: 10px solid currentColor; border-block: 6px solid transparent; }
.gd-slot.has-player .gd-slot-play { visibility: hidden; }
.gd-player { position: absolute; inset: 0; background: #000; }
.gd-player video { display: block; width: 100%; height: 100%; }
.gd-player.is-float { position: fixed; inset: auto auto calc(env(safe-area-inset-bottom, 0px) + 16px) 16px; z-index: 30;
  width: min(360px, calc(100vw - 32px)); aspect-ratio: 16 / 9; border-radius: 6px; overflow: hidden; box-shadow: var(--hs-shadow); }
.gd-player-err { position: absolute; inset: 0; display: grid; place-content: center; margin: 0; padding: 16px; text-align: center;
  color: var(--hs-text); background: var(--hs-code-bg); font-size: 14px; }
.gd-player-err[hidden] { display: none; }
</style>"""

# 预览截帧 + 共享播放器
_PLAYER_SCRIPT = """<script>
(() => {
  const SRC = %s;
  const slots = [...document.querySelectorAll('.gd-slot')];
  if (!slots.length) return;
  const ratios = new Map();  // 预览位 → 可见比例

  // 挂载 HLS 流：hls.js 优先，Safari 原生 HLS 兜底
  const attach = (video, config, onFail) => {
    if (window.Hls && Hls.isSupported()) {
      const hls = new Hls(config);
      hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) onFail(); });
      hls.loadSource(SRC);
      hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = SRC;
    } else {
      onFail();
    }
  };

  // ---------- 预览截帧：一个不显示的 <video> 逐个定位、截帧，存为 JPEG 放进预览位 ----------
  const cap = document.createElement('video');
  cap.muted = true;
  cap.playsInline = true;
  const canvas = document.createElement('canvas');
  const done = new Set(), tries = new Map();
  let capOk = true, busy = false;

  // 下一个要截的预览位：视口内最显眼的未截预览位优先，否则按文档顺序
  const nextSlot = () => {
    const todo = s => !done.has(s) && (tries.get(s) || 0) < 2;
    let best = null, top = 0;
    ratios.forEach((r, s) => { if (r > top && todo(s)) { best = s; top = r; } });
    return best || slots.find(todo);
  };
  const grab = slot => new Promise((resolve, reject) => {
    const onSeeked = () => {
      clearTimeout(timer);
      try {
        canvas.width = cap.videoWidth;
        canvas.height = cap.videoHeight;
        canvas.getContext('2d').drawImage(cap, 0, 0);
        canvas.toBlob(blob => {
          if (!blob) return reject(new Error('empty frame'));
          const img = new Image();
          img.className = 'gd-poster';
          img.alt = '';
          img.src = URL.createObjectURL(blob);
          slot.prepend(img);
          resolve();
        }, 'image/jpeg', 0.85);
      } catch (e) {
        reject(e);  // SecurityError：画布被跨域污染，无法截帧
      }
    };
    // 超时要解除监听，否则迟到的 seeked 会把画面放进下一个预览位
    const timer = setTimeout(() => { cap.removeEventListener('seeked', onSeeked); reject(new Error('timeout')); }, 20000);
    cap.addEventListener('seeked', onSeeked, { once: true });
    cap.currentTime = +slot.dataset.t;
  });
  const pump = async () => {
    if (busy) return;
    busy = true;
    for (let s = nextSlot(); capOk && s; s = nextSlot()) {
      try {
        await grab(s);
        done.add(s);
      } catch (e) {
        if (e.name === 'SecurityError') capOk = false;
        tries.set(s, (tries.get(s) || 0) + 1);
      }
    }
    busy = false;
  };
  // 截帧只需当前一帧：缓冲约 2 秒、不保留已播部分，每次定位只下载 1 个分片
  attach(cap, { maxBufferLength: 2, maxMaxBufferLength: 4, backBufferLength: 0 }, () => { capOk = false; });
  cap.addEventListener('loadedmetadata', pump, { once: true });

  // ---------- 共享播放器：点击时移到对应预览位，从该秒开始播放 ----------
  const box = document.createElement('div');
  box.className = 'gd-player';
  box.innerHTML = '<video controls playsinline preload="none"></video><p class="gd-player-err" hidden>'
    + '视频无法在当前环境加载（例如 claude.ai Artifact 禁止连接外部视频源）。请在本地浏览器打开本页，或使用信息栏里的官方播放器。</p>';
  const video = box.querySelector('video');
  const fail = () => { box.querySelector('.gd-player-err').hidden = false; };
  let host = null, loaded = false, pendingT = null;

  const load = () => {
    if (loaded) return;
    loaded = true;
    video.addEventListener('error', fail);
    attach(video, { maxBufferLength: 20 }, fail);
    // 元数据就绪前设置的时间点，就绪后再应用
    video.addEventListener('loadedmetadata', () => { if (pendingT !== null) video.currentTime = pendingT; pendingT = null; });
  };
  const dock = slot => {
    if (host !== slot) {
      host?.classList.remove('has-player');
      slot.classList.add('has-player');
      slot.append(box);
      host = slot;
    }
    box.classList.remove('is-float');
    load();
    if (video.readyState) video.currentTime = +slot.dataset.t; else pendingT = +slot.dataset.t;
    video.play().catch(() => {});
  };

  // 可见比例：决定截帧优先级；播放中宿主预览位离开视口则浮到角落
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => ratios.set(e.target, e.intersectionRatio));
    if (host) box.classList.toggle('is-float', !video.paused && !(ratios.get(host) > 0));
  }, { threshold: [0, 0.5, 1] });
  slots.forEach(s => io.observe(s));
  video.addEventListener('pause', () => box.classList.remove('is-float'));

  document.addEventListener('click', e => {
    const ts = e.target.closest('[data-t]');
    if (!ts || e.target.closest('.gd-player')) return;
    const slot = ts.classList.contains('gd-slot') ? ts : document.getElementById(ts.dataset.slot);
    if (slot) dock(slot);
  });
})();
</script>"""


def render_html(info: VideoInfo, chapters: list[Chapter], sections: list[Section], summary: dict,
                run_dir: Path, vtt_rel: str, video_src: str) -> Path:
    doc_dir = run_dir.parent.parent
    run_rel = run_dir.relative_to(doc_dir).as_posix()
    title = summary.get("title_zh") or info.title
    body = [f'<h1 id="top">{_e(title)}</h1>']
    if summary.get("title_zh"):
        body.append(f'<p class="hs-meta"><em>{_e(info.title)}</em></p>')
    body += _meta_block(info, summary)
    body += _summary_block(summary)
    for ci, ch in enumerate(chapters, 1):
        body.append(_heading("h2", f"c{ci}", f"{ci}.", ch.title_zh, ch.title_en))
        for si, s in enumerate(sections[ch.first:ch.last + 1], 1):
            body += _section_block(f"c{ci}-{si}", f"{ci}.{si}", s)
    body += ["<hr>", f'<p class="hs-meta">原始字幕：<code>{_e(vtt_rel)}</code>；'
                     f"本文由 GDC2md 生成于 {datetime.now():%Y-%m-%d %H:%M}，对应 <code>{_e(run_rel)}</code>。</p>"]
    path = doc_dir / _file_name(info.title)
    path.write_text(_page(title, "\n".join(body), video_src), encoding="utf-8")
    return path


def _file_name(title: str) -> str:
    """演讲原标题作文件名：去掉 Windows 文件名非法字符，限制长度。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip(" .")[:150]
    return f"{name or 'GDC'}.html"


def _page(title: str, main: str, video_src: str) -> str:
    t = _TEMPLATE.read_text(encoding="utf-8")
    fonts = "\n".join(re.findall(r'^<link [^>]*>$', t, re.M))
    style = re.search(r'<style id="hs-style">.*?</style>', t, re.S).group(0)
    top = re.search(r'<a class="hs-top".*?</a>', t, re.S).group(0)
    script = re.search(r'<script id="hs-script">.*?</script>', t, re.S).group(0)
    player = _PLAYER_SCRIPT % json.dumps(video_src)
    return (f'<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
            f"<title>{_e(title)}</title>\n{fonts}\n{style}\n{_PAGE_STYLE}\n</head>\n<body>\n"
            f'<main class="hs-doc" data-toc-levels="2,3,4">\n{main}\n</main>\n{top}\n{script}\n'
            f'<script src="{_HLS_JS}"></script>\n{player}\n</body>\n</html>\n')


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def _heading(tag: str, hid: str, num: str, title_zh: str, title_en: str) -> str:
    en = f'<span class="gd-en">{_e(title_en)}</span>' if title_zh and title_zh != title_en else ""
    return (f'<{tag} id="{hid}"><span class="hs-num">{num}</span>{_e(title_zh or title_en)}{en}'
            f'<a class="hs-anchor" href="#{hid}" aria-label="本节链接">#</a></{tag}>')


def _meta_block(info: VideoInfo, summary: dict) -> list[str]:
    speakers = summary.get("speakers_zh") or info.speakers
    companies = summary.get("companies_zh") or info.companies
    link = lambda url, text: f'<a href="{_e(url)}">{_e(text)}</a>'
    rows = [
        ("讲者", _e(", ".join(speakers))),
        ("公司", _e(", ".join(dict.fromkeys(companies)))),
        ("会议", _e(info.conference)),
        ("Track", _e(render._zh_en(summary.get("track_zh", ""), info.track))),
        ("原页", link(info.vault_url, info.vault_url)),
        ("视频", f"各小节内嵌预览，全文共用一个播放器；点击时间戳从该秒播放 · {link(info.player_url(), '官方播放器')}"),
    ]
    out = ['<blockquote class="hs-callout" data-kind="info">', '<p class="hs-callout-title">演讲信息</p>', '<dl class="hs-kv">']
    out += [f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows if v]
    out += ["</dl>", "</blockquote>"]
    overview_zh = summary.get("overview_zh")
    if overview_zh:
        out += [f"<blockquote><p>{_e(overview_zh)}</p>", f'<p class="gd-quote-en">{_e(info.overview)}</p></blockquote>']
    elif info.overview:
        out += [f"<blockquote><p>{_e(info.overview)}</p></blockquote>"]
    return out


def _summary_block(summary: dict) -> list[str]:
    if not summary.get("summary_zh"):
        return []
    out = ['<h2 id="overview"><span class="hs-num">0.</span>概述<a class="hs-anchor" href="#overview" aria-label="本节链接">#</a></h2>',
           f"<p>{_e(summary['summary_zh'])}</p>"]
    if summary.get("takeaways_zh"):
        out += ['<blockquote class="hs-callout" data-kind="key">', '<p class="hs-callout-title">要点</p>', "<ul>"]
        out += [f"<li>{_e(t)}</li>" for t in summary["takeaways_zh"]]
        out += ["</ul>", "</blockquote>"]
    return out


def _section_block(hid: str, num: str, s: Section) -> list[str]:
    """小节：有小段时每个小段一个 h4 + 时间戳 + 预览位，小节标题下的时间戳指向第一个小段的预览位。"""
    first_slot = f"{hid}-1" if s.groups else hid
    out = [_heading("h3", hid, num, s.title_zh, s.title_en),
           _timestamp(hid, first_slot, s.start_sec, f"cue #{s.start_idx}–#{s.end_idx}")]
    if not s.groups:
        out.append(_slot(hid, s.start_sec))
    if s.fallback:
        out += ['<blockquote class="hs-callout" data-kind="warn"><p class="hs-callout-title">注意</p>',
                "<p>此节 AI 处理失败，以下为未清洗的原始字幕。</p></blockquote>"]
    if not s.groups:
        return out + _body(s.blocks)
    for gi, g in enumerate(s.groups, 1):
        gid, t = f"{hid}-{gi}", g.get("start_sec", s.start_sec)
        out += [_heading("h4", gid, f"{num}.{gi}", g["title_zh"], g["title_en"]), _timestamp(gid, gid, t), _slot(gid, t)]
        out += _body(s.blocks[g["first"]:g["last"] + 1])
    return out


def _timestamp(hid: str, slot: str, sec: float, note: str = "") -> str:
    """时间戳链接：跳到标题，并由播放器脚本移到 slot 预览位从该秒播放。"""
    cue = f'<span class="gd-cue">{_e(note)}</span>' if note else ""
    return (f'<p class="gd-ts"><a href="#{hid}" data-t="{int(sec)}" data-slot="v-{slot}"><i class="hs-ico" data-icon="clock"></i> '
            f"{fmt_clock(sec)}</a>{cue}</p>")


def _slot(hid: str, sec: float) -> str:
    return (f'<div class="gd-slot" id="v-{hid}" data-t="{int(sec)}">'
            f'<button type="button" class="gd-slot-play">播放 {fmt_clock(sec)}</button></div>')


def _body(blocks: list[dict]) -> list[str]:
    """中文段落 + 折叠的清洗英文；无中文时直接显示英文。"""
    zh = [b["zh"] for b in blocks if b.get("zh")]
    en = [b["en"] for b in blocks if b.get("en")]
    if zh:
        return _paragraphs(zh) + ["<details><summary>English (cleaned)</summary>"] + _paragraphs(en) + ["</details>"]
    return _paragraphs(en)


def _paragraphs(blocks: list[str]) -> list[str]:
    return [f"<p>{_e(b)}</p>" for b in blocks]
