"""本地静态 HTTP 服务：托管输出目录，让文档里的 http://127.0.0.1:port/... 链接可点。"""
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # 不刷屏
        pass


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # Windows 上 SO_REUSEADDR 会让第二个实例也绑定成功，关掉才能检测端口占用


def port_of(http_base: str) -> int:
    return urlparse(http_base).port or 80


def start_background(root: Path, port: int) -> _Server | None:
    """在后台线程起服务；端口被占（多半是另一个 GDC2md 实例）则返回 None。"""
    root.mkdir(parents=True, exist_ok=True)
    try:
        server = _Server(("127.0.0.1", port), partial(_QuietHandler, directory=str(root)))
    except OSError:
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server: _Server) -> None:
    server.shutdown()
    server.server_close()  # 只 shutdown 不释放端口


def serve_forever(root: Path, port: int) -> None:
    server = start_background(root, port)
    if not server:
        raise RuntimeError(f"端口 {port} 已被占用")
    print(f"托管 {root} 于 http://127.0.0.1:{port}/ ，Ctrl+C 退出")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stop(server)
