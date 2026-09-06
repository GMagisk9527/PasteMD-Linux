# 为 PasteMD Linux 做贡献

感谢你参与这个 Linux Fork。提交改动前，请确认它适用于 Fedora KDE Wayland 与 WPS Linux，并保持原项目的作者信息和 GNU AGPL-3.0 许可证。

## 开发环境

```bash
sudo dnf install pandoc wl-clipboard python3-pyside6 python3-dbus python3-gobject libX11 libXtst
git clone https://github.com/GMagisk9527/PasteMD-Linux.git
cd PasteMD-Linux
```

运行图形界面：

```bash
python3 scripts/pastemd-linux.py
```

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_wayland_cli.py'
python3 -m unittest discover -s tests -p 'test_linux_desktop.py'
```

涉及剪贴板或公式的改动，还应在 WPS 的 `.docx` 文档中手动检查分数、根式、上下标和矩阵是否可编辑。

## 提交要求

- 说明触发问题的操作和修改后的行为。
- 列出实际运行过的测试与 Fedora、KDE、WPS 版本。
- 不删除或改写原作者、历史贡献者和第三方组件的版权声明。
- 贡献内容按本仓库的 GNU AGPL-3.0 许可证发布。

通用或 Windows/macOS 改动应优先提交到[上游仓库](https://github.com/RICHQAQ/PasteMD)。
