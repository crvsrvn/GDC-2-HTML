"""GDC2md 控制台入口。

用法：
  python gdc2md.py                       # 交互菜单
  python gdc2md.py <vault_url> [--from clean]   # 直接处理
"""
import argparse
import sys
from getpass import getpass

from gdc2md import config, pipeline, serve
from gdc2md.llm import DeepSeek


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="GDC Vault 视频 → 字幕 → Markdown 文档")
    ap.add_argument("url", nargs="?", help="GDC Vault 播放页 URL；或 serve = 常驻托管输出目录的本地 HTTP 服务")
    ap.add_argument("--from", dest="start", default="resolve", choices=pipeline.STAGES, help="（调试用）从指定阶段续跑，默认从头自动执行全部阶段")
    args = ap.parse_args()

    cfg = config.load_config()
    if args.url == "serve":
        serve.serve_forever(config.out_dir(cfg), serve.port_of(cfg["http_base"]))
        return
    if args.url:
        process(args.url, cfg, args.start)
        return
    menu(cfg)


def menu(cfg: dict) -> None:
    actions = {"1": lambda: process(input("GDC Vault URL: ").strip(), cfg, "resolve"),
               "2": lambda: manage_key(cfg),
               "3": lambda: settings(cfg)}
    while True:
        print(f"\n==== GDC2md ====   {serve_status(cfg)}\n 1) 处理 GDC Vault 视频\n 2) 管理 DeepSeek API Key\n 3) 设置\n 0) 退出")
        choice = input("> ").strip()
        if choice == "0":
            return
        action = actions.get(choice)
        if not action:
            continue
        try:
            action()
        except (RuntimeError, ValueError, OSError) as e:
            print(f"错误：{e}")


_server = None


def serve_status(cfg: dict) -> str:
    """控制台运行期间托管输出目录，文档里的 http 链接才可点；端口被占则提示。"""
    global _server
    if cfg["link_style"] != "http":
        return ""
    port = serve.port_of(cfg["http_base"])
    if _server is None or _server.server_address[1] != port:
        if _server:
            serve.stop(_server)
        _server = serve.start_background(config.out_dir(cfg), port)
    return f"[本地服务 {cfg['http_base']} 运行中]" if _server else f"[端口 {port} 被占用，文档链接可能由另一实例提供]"


def process(url: str, cfg: dict, start: str) -> None:
    if not url:
        return
    key = ensure_key(cfg)
    pipeline.run(url, cfg, key, start)


def ensure_key(cfg: dict) -> str:
    key = config.load_api_key()
    while not key:
        print("首次运行：请输入 DeepSeek API Key（输入不回显）")
        key = input_key(cfg)
    return key


def input_key(cfg: dict) -> str | None:
    key = getpass("API Key: ").strip()
    if not key:
        return None
    ok, msg = DeepSeek(key, cfg).verify()
    if not ok:
        print(f"校验失败：{msg}")
        if input("仍然保存？(y/N) ").strip().lower() != "y":
            return None
    config.save_api_key(key)
    print("已加密保存到 secrets.bin")
    return key


def manage_key(cfg: dict) -> None:
    key = config.load_api_key()
    print(f"\n当前 Key：{config.mask_key(key) if key else '（未设置）'}")
    print(" 1) 新增/修改\n 2) 删除\n 3) 重新校验\n 0) 返回")
    choice = input("> ").strip()
    if choice == "1":
        input_key(cfg)
    elif choice == "2" and key:
        config.delete_api_key()
        print("已删除")
    elif choice == "3" and key:
        ok, msg = DeepSeek(key, cfg).verify()
        print("有效" if ok else f"无效：{msg}")


def settings(cfg: dict) -> None:
    keys = list(config.DEFAULTS)
    while True:
        print()
        for i, k in enumerate(keys, 1):
            hint = f"   [{' / '.join(config.CHOICES[k])}]" if k in config.CHOICES else ""
            print(f" {i}) {k} = {cfg[k]}{hint}")
        print(" 0) 返回")
        choice = input("> ").strip()
        if choice == "0":
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(keys):
            continue
        k = keys[int(choice) - 1]
        val = input(f"{k} 新值（回车取消）: ").strip()
        if not val:
            continue
        try:
            cfg[k] = config.parse_value(k, val)
        except ValueError as e:
            print(f"无效：{e}")
            continue
        config.save_config(cfg)


if __name__ == "__main__":
    main()
