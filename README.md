# PasteMD Linux

PasteMD Linux 用于在 Fedora KDE Wayland 中，把 Markdown 或 AI 网页正文转换后粘贴到 WPS。公式通过 WPS 原生 DOCX 剪贴板传递，在 `.docx` 文档中可以继续编辑。

> 本项目是 [RICHQAQ/PasteMD](https://github.com/RICHQAQ/PasteMD) 的 Linux 专用 Fork。原项目由 RICHQAQ 及其贡献者开发；本仓库保留原许可证、提交历史和版权归属。Linux 适配由 GMagisk9527 维护。更多信息见 [NOTICE.md](NOTICE.md)。

## 功能

- Fedora、KDE Plasma、Wayland 和 WPS Linux 适配
- PySide6 图形界面和系统托盘
- KDE 全局快捷键，默认为 `Ctrl+Shift+B`
- 转换完成后自动向原 WPS 窗口发送 `Ctrl+V`
- 粘贴前核对窗口和文档焦点，避免内容进入其他应用
- 支持 Markdown、网页 HTML、行内公式、块级公式、表格和常用 LaTeX 结构
- 可生成并打开独立 DOCX 文件

## 安装

先安装 WPS Linux 版，然后在 Fedora 中安装运行依赖：

```bash
sudo dnf install pandoc wl-clipboard python3-pyside6 python3-dbus python3-gobject libX11 libXtst
```

克隆这个 Fork：

```bash
git clone https://github.com/GMagisk9527/PasteMD-Linux.git
cd PasteMD-Linux
```

启动图形界面：

```bash
python3 scripts/pastemd-linux.py
```

添加到 KDE 应用菜单：

```bash
python3 scripts/pastemd-linux.py --install
```

## 使用

1. 在网页或 AI 应用中复制 Markdown 或正文。
2. 回到 WPS 的 `.docx` 文档，把光标放到插入位置。
3. 按 `Ctrl+Shift+B`。
4. 等待转换和自动粘贴完成。

关闭主窗口后程序会驻留系统托盘。快捷键、自动粘贴、通知和登录自启可在“设置”页调整。

> WPS 的 `.wps` 文档会把粘贴的公式变成图片。需要可编辑公式时，请新建或另存为 `.docx`，然后从原始内容重新转换和粘贴。已经变成图片的公式不会因另存格式而恢复。

## 命令行

转换当前剪贴板，随后手动在 WPS 中按 `Ctrl+V`：

```bash
python3 scripts/pastemd-wayland.py
```

生成内置公式样本：

```bash
python3 scripts/pastemd-wayland.py --demo
```

生成并打开 DOCX：

```bash
python3 scripts/pastemd-wayland.py --open
```

完整参数和故障排查见 [docs/LINUX.md](docs/LINUX.md)。

## 测试

```bash
python3 -m unittest discover -s tests -p 'test_wayland_cli.py'
python3 -m unittest discover -s tests -p 'test_linux_desktop.py'
```

## 项目结构

```text
pastemd/linux/              Linux 转换、界面、热键和自动粘贴实现
scripts/pastemd-linux.py    图形界面入口
scripts/pastemd-wayland.py  命令行兼容入口
tests/                      Linux 版自动化测试
docs/LINUX.md               完整使用和实现说明
```

## 许可证与致谢

本项目继续使用原项目的 [GNU AGPL-3.0](LICENSE) 许可证。原作者、历史贡献者和第三方软件的权利不因 Fork 或目录精简而改变。

- 上游项目：[RICHQAQ/PasteMD](https://github.com/RICHQAQ/PasteMD)
- Linux Fork：[GMagisk9527/PasteMD-Linux](https://github.com/GMagisk9527/PasteMD-Linux)
- 第三方组件：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
