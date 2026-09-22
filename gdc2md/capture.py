"""阶段 4：用 ffmpeg 直接从 HLS 流抽取指定时间点的截图。"""
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

MIN_JPEG_BYTES = 6000  # 小于此值基本是黑帧/空帧


def ensure_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("未找到 ffmpeg，请安装并加入 PATH")
    return exe


def capture_frames(stream_url: str, timestamps: list[float], img_dir: Path, log: Callable[[str], None]) -> list[Path]:
    ffmpeg = ensure_ffmpeg()
    img_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(ffmpeg, stream_url, t, img_dir / f"{n:03d}.jpg") for n, t in enumerate(timestamps, 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda j: _grab_with_retry(*j), jobs))
    ok = sum(1 for r in results if r)
    log(f"截图完成：{ok}/{len(jobs)} 张")
    return results


def _grab_with_retry(ffmpeg: str, url: str, t: float, out: Path) -> Path | None:
    # 章节起点 +1.5s 避开切换黑帧；失败再往后挪 3s 试一次
    for offset in (1.5, 4.5):
        if _grab(ffmpeg, url, t + offset, out):
            return out
    return None


def _grab(ffmpeg: str, url: str, t: float, out: Path) -> bool:
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", url,
           "-frames:v", "1", "-q:v", "3", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=180, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return out.exists() and out.stat().st_size >= MIN_JPEG_BYTES
