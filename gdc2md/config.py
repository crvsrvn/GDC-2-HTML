"""配置与密钥存储。

- config.json  : 非敏感配置（输出目录、模型等）
- secrets.bin  : DeepSeek API Key，使用 Windows DPAPI 加密（绑定当前 Windows 用户）
- cookie.bin   : GDC Vault 登录 Cookie（会员视频用），同样 DPAPI 加密
"""
import ctypes
import json
from ctypes import wintypes
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
SECRET_PATH = APP_DIR / "secrets.bin"
COOKIE_PATH = APP_DIR / "cookie.bin"

DEFAULTS = {
    "out_dir": "output",         # 相对工具目录；工具目录整体移动后无需改配置
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "thinking": "disabled",      # 思考模式：disabled / enabled
    "reasoning_effort": "high",  # 思考强度（仅 thinking=enabled 时生效）：none / low / high / max
    "max_tokens": 16000,         # 单次输出上限；思考模式下同时计入推理与正文
    "temperature": 1.0,
    "subtitle_lang": "eng",
    "video_quality": 1080,       # HTML 内嵌视频的档位（取不高于此值的最高档）
    "chunk_seconds": 360,
}

# 取值受限的配置项
CHOICES = {
    "thinking": ["disabled", "enabled"],
    "reasoning_effort": ["none", "low", "high", "max"],
}


def parse_value(key: str, raw: str):
    """按默认值类型转换用户输入；枚举项校验取值。"""
    if key in CHOICES:
        if raw not in CHOICES[key]:
            raise ValueError(f"{key} 只能是 {' / '.join(CHOICES[key])}")
        return raw
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "y", "yes")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if cfg["model"] == "deepseek-chat":  # 已退役的旧别名
        cfg["model"] = "deepseek-flash"
    return cfg


def out_dir(cfg: dict) -> Path:
    """输出根目录：相对路径按工具目录解析。"""
    return (APP_DIR / cfg["out_dir"]).resolve()


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- DPAPI ----------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    dst = _Blob()
    if not fn(ctypes.byref(src), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst)):
        raise OSError(f"DPAPI 调用失败: {ctypes.FormatError(ctypes.get_last_error())}")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)


def load_api_key() -> str | None:
    if not SECRET_PATH.exists():
        return None
    return _dpapi(SECRET_PATH.read_bytes(), protect=False).decode("utf-8")


def save_api_key(key: str) -> None:
    SECRET_PATH.write_bytes(_dpapi(key.strip().encode("utf-8"), protect=True))


def delete_api_key() -> None:
    if SECRET_PATH.exists():
        SECRET_PATH.unlink()


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def load_cookie() -> str | None:
    if not COOKIE_PATH.exists():
        return None
    return _dpapi(COOKIE_PATH.read_bytes(), protect=False).decode("utf-8")


def save_cookie(cookie: str) -> None:
    COOKIE_PATH.write_bytes(_dpapi(cookie.strip().encode("utf-8"), protect=True))


def delete_cookie() -> None:
    if COOKIE_PATH.exists():
        COOKIE_PATH.unlink()
