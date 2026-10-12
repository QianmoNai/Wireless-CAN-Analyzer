# CAN Scope Panel 16

`main/board_ui_font_data.h` 是固定界面文案所用的 16×16 单色字形子集，派生自 Adobe 思源黑体简体中文 Regular。派生字库名为 CAN Scope Panel 16；字形数据遵循相邻的 SIL Open Font License 1.1，其他固件代码保留项目原有许可。

仅重新生成字形时需要 Pillow 和原始 `SourceHanSansCN-Regular.otf`；正常主机测试和固件构建直接使用已提交的 C 字形数据，无字体或网络依赖：

```bash
python3 scripts/generate-panel-font.py --font /path/to/SourceHanSansCN-Regular.otf
```

生成器扫描 `board_ui_render.c` 中的字符串，固定 16 像素、左上对齐和灰度阈值 100；若字体微调把末笔挤到第 17 行，会移去顶部空行，完整保留笔画。生成文件记录源字体 SHA256。新增中文文案后重新生成，并检查 `tests/test_panel_font.py` 与本机页面预览。

原始字体与许可：[Adobe Source Han Sans](https://github.com/adobe-fonts/source-han-sans)、[OFL 1.1](https://github.com/adobe-fonts/source-han-sans/blob/master/LICENSE.txt)。分发这个派生字库时请携带 `OFL-SourceHanSans.txt`。
