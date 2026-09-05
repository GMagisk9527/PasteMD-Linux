# Fedora + Wayland + WPS（实验性入口）

2026-09-05：新增 Linux 命令行入口和原生公式 RTF 剪贴板链路，沿用 AGPL-3.0。
上游完整托盘程序尚未适配 Linux。WPS 的最终粘贴显示与编辑能力仍需实机确认。

## 安装

先安装 WPS Linux 版，再安装转换与剪贴板依赖：

```bash
sudo dnf install pandoc wl-clipboard libreoffice-writer libreoffice-math python3-pyside6
```

需要 Wayland 会话中的 XWayland（`DISPLAY`）。使用系统 `/usr/bin/python3`，确保能导入系统安装的 PySide6。
仅有 LibreOffice Core 不够，需要 Writer 的文档导入/导出过滤器以及 Math 组件。

## 直接 Ctrl+V

1. 复制 Markdown 或 AI 网页正文。
2. 在仓库目录执行 `python3 scripts/pastemd-wayland.py`。
3. 等待“公式富文本已就绪”的通知或终端提示，切回 WPS 按 Ctrl+V。

默认写入 RTF 剪贴板，不打开中间文档。转换成功后原剪贴板会被替换，不自动恢复。
再次转换前重新复制来源。剪贴板服务在后台保有转换结果，复制其他内容后自动退出。

默认优先读取 HTML，没有 HTML 时读取纯文本并按 Markdown 转换。
希望强制使用复制按钮产生的 Markdown 时，使用 `--input markdown`。
支持 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 数学分隔符。

## 内置样本

不需要预先复制任何内容，运行：

```bash
python3 scripts/pastemd-wayland.py --demo
```

然后在空白 WPS 文档中 Ctrl+V。样本包含中文、粗体、行内公式、分数、三次根式、求和、矩阵和表格。
检查显示效果，并点击公式确认能编辑分子等内容。RTF 也包含兼容预览图，单凭看到公式不能证明可编辑。

## 快捷键

在桌面设置的自定义快捷键中绑定 Ctrl+Shift+B，命令使用实际绝对路径，例如：

```text
/usr/bin/python3 /home/pxx/桌面/pastemd/PasteMD/scripts/pastemd-wayland.py
```

复制来源后按 Ctrl+Shift+B，等待完成，再按 Ctrl+V。快捷键由桌面管理，不模拟按键或自动检测窗口。

## DOCX 备选模式

```bash
# 仅保存 DOCX 并输出路径，保留剪贴板
python3 scripts/pastemd-wayland.py --docx

# 保存 DOCX 并用 WPS 打开，保留剪贴板
python3 scripts/pastemd-wayland.py --open

# 显式指定 RTF 剪贴板，与默认行为相同
python3 scripts/pastemd-wayland.py --clipboard
```

DOCX 保存在 `${XDG_CACHE_HOME:-~/.cache}/pastemd`，需要自行清理。
RTF 转换的中间文件与独立 LibreOffice 配置存放在临时目录，转换结束自动清理。

## 实现与限制

- Pandoc 将网页/Markdown 解析成文档结构，清理页面偏移、隐藏 span/div 包装以及重复 KaTeX 展示层。
- 将文档转换为带 OMML 原生公式的 DOCX，再由独立配置的 LibreOffice 后台导出 RTF。不会复用已打开的 LibreOffice 文档会话。
- 校验 RTF 保留的原生公式数量，转换失败或公式数减少时不写剪贴板。
- PySide6 通过 XWayland 同时提供 WPS 使用的 `Rich Text Format` 原生格式、`text/rtf`、`text/richtext` 和纯文本。不会同时提供容易被 WPS 优先读取的 HTML。

这与第一版的 HTML/MathML 剪贴板不同。第一版会把 DeepSeek 隐藏布局带入结果，HTML 也无法保证在 WPS 中转成原生公式。
清理网页包装会丢弃其颜色、字号和页面布局。未接入上游所有网页公式恢复、图片与样式修复逻辑。
LibreOffice 转换需要额外时间；复杂公式、图片、版式和不同 WPS 版本仍需验证。

## 验证

```bash
# 单元测试与 Pandoc 测试
python3 -m unittest discover -s tests -p 'test_wayland_cli.py'

# 加上 LibreOffice 实际往返转换测试
PASTEMD_TEST_OFFICE=1 python3 -m unittest discover -s tests -p 'test_wayland_cli.py'
```

集成测试检查 DOCX → RTF → DOCX 后仍含原生公式、分数、根式、求和与表格。
这证明转换结构保留，不替代 WPS 的 Ctrl+V 实测。

参考：[Qt 剪贴板生命周期](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QClipboard.html)、[LibreOffice 命令行参数](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)。
