# GDC2md

GDC Vault 视频 → 原始字幕 → 两级章节的 Markdown 文档：AI 归组的**大章节** → AI 分出的**小节**（时间戳跳转链接 + 截图）→ 小节内按微主题拆成的**段落块**（中文译文 + 折叠的清洗英文）。标题、简介、讲者、公司、Track 也一并中译。

## 依赖

- Windows（API Key 用 DPAPI 加密，绑定当前 Windows 用户）
- Python 3.10+，需要 `requests`
- ffmpeg 在 PATH 中（截图用）
- DeepSeek API Key

## 使用

```
GDC2md.cmd                                  # 交互菜单
GDC2md.cmd https://gdcvault.com/play/1036050/-Kingdom-Come-Deliverance-II
GDC2md.cmd <url> --from clean               # （调试用）从指定阶段续跑：resolve / fetch / clean / capture / render
GDC2md.cmd serve                            # 常驻托管输出目录的本地 HTTP 服务（文档里的跳转链接依赖它）
```

输入 URL 后自动串行执行 resolve → fetch → clean → capture → render 五个阶段，任一阶段出错即停止并报错。

首次运行会提示输入 DeepSeek API Key，校验后加密保存到同目录 `secrets.bin`；菜单 2 可查看/修改/删除。
菜单 3 可改输出目录、模型（默认 `deepseek-flash`）、字幕语言（默认 `eng`）、截图分辨率、送 AI 的分块时长。

## 产物

每次运行都写入新的 `runs/<时间戳>/` 目录，不会覆盖或删除以前的产物：

```
output/<playId>-<slug>/            （output 相对工具目录，可在设置里改）
├── meta.json        页面元数据与 HLS 清单地址        ┐
├── raw/eng.vtt      合并后的原始字幕                 │ 跨次运行复用
├── raw/cues.json    结构化 cue                       │
├── cache/           AI 调用缓存（按内容哈希，重跑不重复计费）┘
└── runs/20260922-141818/
    ├── README.md    最终文档
    ├── player.html  本地播放器，README 中的时间戳链接指向 http://127.0.0.1:8765/<目录>/runs/<时间戳>/player.html?t=秒
    ├── img/NNN.jpg  每个小节开头截图
    └── sections.json  AI 结果：summary（头部中译/概述）、chapters（大章节）、sections（小节与段落块）
```

`--from clean/capture/render` 续跑时，新 run 目录缺的 `sections.json` / `img` 会从最近一次 run 复制过来。

## 说明

- 只支持免费公开的 GDC Vault 视频；需要登录的会员内容会报"未找到播放器 iframe"。
- 文档里的时间戳链接默认是 `http://127.0.0.1:8765/<目录>/player.html?t=秒`（`link_style=http`）：控制台菜单打开期间自动托管 `out_dir`，关掉控制台后可用 `GDC2md.cmd serve` 常驻。`file`（绝对 file:// URL）和 `relative`（相对路径）在部分 Markdown 查看器里不被识别为链接或会直接打开文件。
- `player.html` 直接播放 GDC 的 HLS 流，需要联网。
- 截图宽度由 `image_width` 控制（默认 `50%`）。改任何影响文档的设置后，用 `--from render` 重生成文档即可，不会重新调用 AI。
- `sections.json` 里 `fallback: true` 的章节表示 AI 两次输出都不合规，文档中会以原始字幕呈现并标注。
