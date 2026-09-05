# Fedora + Wayland + WPS（实验性入口）

2026-09-05：新增 Linux 命令行入口，沿用 AGPL-3.0。上游完整托盘程序尚未适配 Linux。
此入口直接调用 Pandoc，尚未接入上游的网页公式预处理、配置、过滤器和样式修复。

## 安装与运行

先安装 WPS Linux 版，然后安装系统依赖：

```bash
sudo dnf install pandoc wl-clipboard
```

在仓库目录运行（仅需 Python 3 标准库）：

```bash
python3 scripts/pastemd-wayland.py
```

先复制 Markdown 或网页正文，执行命令，再切回 WPS 按 Ctrl+V。
默认优先读取 HTML，没有 HTML 时读取纯文本并按 Markdown 转换。
如果复制按钮同时携带 HTML，但你希望按 Markdown 处理，使用 `--input markdown`。
成功后剪贴板会被替换成 HTML；不自动恢复原内容。

## 快捷键

在桌面设置的自定义快捷键中绑定 Ctrl+Shift+B，命令使用实际绝对路径，例如：

```text
/usr/bin/python3 /home/pxx/桌面/pastemd/PasteMD/scripts/pastemd-wayland.py
```

由桌面环境管理快捷键。转换完成后手动粘贴，目前没有模拟按键、窗口识别或自动插入。
不要连续触发快捷键而不重新复制原文，否则会再次转换已经生成的 HTML。

## DOCX 与公式

```bash
python3 scripts/pastemd-wayland.py --docx
python3 scripts/pastemd-wayland.py --input markdown --open
```

`--docx` 保留剪贴板，保存 DOCX 并输出路径；`--open` 还会调用 WPS 打开。
文件保存在 `${XDG_CACHE_HOME:-~/.cache}/pastemd`，需要自行清理。
DOCX 模式用于验证 Pandoc 生成的公式和表格，打开的是生成的新文件，未插入原文档。
网页 AI 公式恢复、HTML/MathML 在 WPS 的粘贴效果尚未实机验证，不能保证无损。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_wayland_cli.py'
```

自动测试覆盖格式选择、失败时不改剪贴板和 DOCX 输出流程。
发布前还需要在 Fedora Wayland + WPS 实测中文、列表、表格、公式和网页 HTML。
