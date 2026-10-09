[中文](./README.md) | [English](./README.en.md)

### GDC2md

- 把 GDC Vault 的演讲视频转成中文阅读文档：AI 清洗英文字幕、分章节并翻译，标题、简介、讲者等也一并翻译
    - 生成单文件 HTML 阅读版：右侧大纲、内嵌视频，点时间戳或预览图即从该处播放；同时生成纯文本 Markdown
    - AI 结果按内容缓存，重新生成文档不重复计费
- 支持 Windows；需要 DeepSeek API Key；会员视频需要提供已登录会员账号的浏览器 Cookie

### 安装（供 AI 执行）

1. 需要 Python 3.10+，并安装依赖：`pip install requests`。
2. 运行 `GDC2md.cmd` 打开交互菜单，或 `GDC2md.cmd <GDC Vault 播放页 URL>` 直接转换。
3. 首次运行会提示输入 DeepSeek API Key（用 Windows DPAPI 加密保存到 `secrets.bin`）。让用户自己输入，不要代填。
4. 产物在 `output/<playId>-<slug>/<演讲原标题>.html`；设置（输出目录、模型、字幕语言、视频清晰度等）在菜单 3 里改。
5. 会员视频：在菜单 4 粘贴浏览器里已登录 GDC Vault 的 Cookie（菜单里有复制方法，DPAPI 加密保存到 `cookie.bin`）。让用户自己粘贴，不要代填。
