"""带重试的 HTTP GET。"""
import time

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

_session = requests.Session()
_session.headers["User-Agent"] = UA


def get_bytes(url: str, timeout: float = 20, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下载失败 {url}: {last}")


def get_text(url: str, timeout: float = 20, retries: int = 3) -> str:
    return get_bytes(url, timeout, retries).decode("utf-8", errors="replace")
