[中文](./README.md) | [English](./README.en.md)

### GDC2md

- Turns free, public GDC Vault talk videos into Chinese reading documents: AI cleans the English subtitles, splits them into chapters and translates them, along with the title, abstract, speakers and more
    - Produces a single-file HTML reading version with a right-side outline and an embedded video; clicking a timestamp or preview image plays from that point. A plain-text Markdown version is generated as well
    - AI results are cached by content, so regenerating a document is not billed again
- Supports Windows; requires a DeepSeek API key; member-only videos that need a login are not supported

### Installation (for AI)

1. Requires Python 3.10+ and the dependency: `pip install requests`.
2. Run `GDC2md.cmd` for the interactive menu, or `GDC2md.cmd <GDC Vault player page URL>` to convert directly.
3. The first run prompts for a DeepSeek API key (encrypted with Windows DPAPI into `secrets.bin`). Let the user enter it; do not fill it in for them.
4. Output goes to `output/<playId>-<slug>/<original talk title>.html`; settings (output directory, model, subtitle language, video quality, etc.) are under menu 3.
