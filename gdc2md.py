"""GDC2md 控制台入口。

用法：
  python gdc2md.py                       # 交互菜单
  python gdc2md.py <vault_url> [--from clean]   # 直接处理
  python gdc2md.py html                  # 为已有项目重新生成根目录的 HTML 文档
"""
import argparse
import sys
from getpass import getpass

from gdc2md import config, httpclient, pipeline
from gdc2md.llm import DeepSeek

COOKIE_HELP = """\
复制 Cookie 的方法（Chrome，括号内为中文界面名称）：
 1. 在已登录的 Chrome 中打开要处理的视频页
 2. 按 F12 打开开发者工具，切到 Network（网络）面板，按 F5 刷新页面
 3. 点请求列表第一条（即视频页本身，可用 Doc（文档）筛选）
 4. 右侧 Headers（标头）→ Request Headers（请求标头）→ 右键 cookie 这一行 → Copy value（复制值）
 注意：不要用控制台的 document.cookie，它缺少登录会话 PHPSESSID
粘贴：在下方 Cookie: 提示处单击右键粘贴（内容不回显），然后回车"""

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="GDC Vault 视频 → 字幕 → Markdown / HTML 文档")
    ap.add_argument("url", nargs="?", help="GDC Vault 播放页 URL；或 html = 用各项目最近一次 run 重新生成根目录的 HTML 文档")
    ap.add_argument("--from", dest="start", default="resolve", choices=pipeline.STAGES, help="（调试用）从指定阶段续跑，默认从头自动执行全部阶段")
    args = ap.parse_args()

    cfg = config.load_config()
    if args.url == "html":
        pipeline.render_all_html(cfg)
        return
    if args.url:
        process(args.url, cfg, args.start)
        return
    menu(cfg)


def menu(cfg: dict) -> None:
    actions = {"1": lambda: process(input("GDC Vault URL: ").strip(), cfg, "resolve"),
               "2": lambda: manage_key(cfg),
               "3": lambda: settings(cfg),
               "4": manage_cookie}
    while True:
        print("\n==== GDC2md ====\n 1) 处理 GDC Vault 视频\n 2) 管理 DeepSeek API Key\n 3) 设置\n"
              " 4) 管理 GDC Vault Cookie（会员视频用）\n 0) 退出")
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


def process(url: str, cfg: dict, start: str) -> None:
    if not url:
        return
    key = ensure_key(cfg)
    httpclient.set_vault_cookie(config.load_cookie())
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


def manage_cookie() -> None:
    cookie = config.load_cookie()
    print(f"\n当前 Cookie：{'已设置' if cookie else '（未设置）'}")
    print(" 1) 新增/修改\n 2) 删除\n 0) 返回")
    choice = input("> ").strip()
    if choice == "1":
        input_cookie()
    elif choice == "2" and cookie:
        config.delete_cookie()
        print("已删除")


def input_cookie() -> None:
    print(COOKIE_HELP)
    cookie = getpass("Cookie: ").strip()
    names = [name for name, _ in httpclient.parse_cookie(cookie)]
    if not names:
        print("未识别到 Cookie，未保存")
        return
    if "PHPSESSID" not in names:
        print("缺少登录会话 PHPSESSID，可能复制错了，请按上面第 4 步从 Network 面板复制")
        if input("仍然保存？(y/N) ").strip().lower() != "y":
            return
    config.save_cookie(cookie)
    print(f"已加密保存到 cookie.bin（{len(names)} 项）")


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
