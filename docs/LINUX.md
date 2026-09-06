# PasteMD Linux：Fedora + KDE Wayland + WPS

Linux 版现已提供 WPS 原生 DOCX 剪贴板、图形界面、系统托盘、KDE 全局热键和焦点保护自动粘贴，沿用 AGPL-3.0。
已在 Fedora、KDE Wayland 和 WPS Linux 的 `.docx` 文档中验证可编辑公式。

> **文档必须使用 `.docx` 格式。** WPS 的 `.wps` 格式会把粘贴的公式变成图片；另存为 `.docx` 不会修复已经图片化的公式，需要从原内容重新转换和粘贴。

## 安装

先安装 WPS Linux 版，再安装转换与剪贴板依赖：

```bash
sudo dnf install pandoc wl-clipboard python3-pyside6
```

需要 Wayland 会话中的 XWayland（`DISPLAY`）。使用系统 `/usr/bin/python3`，确保能导入系统安装的 PySide6。
当前方案不依赖 LibreOffice 或 WPS RPC 插件。

## 推荐用法：热键转换并粘贴

首次启动图形界面：

```bash
python3 scripts/pastemd-linux.py
```

随后按以下步骤使用：

1. 在网页或 AI 应用中复制 Markdown 或正文。
2. 回到 WPS 的 `.docx` 文档，把光标放在需要插入的位置。
3. 按 `Ctrl+Shift+B`。
4. 等待 PasteMD 完成转换并自动粘贴。

关闭主窗口后程序继续驻留系统托盘。可以在“设置”页修改快捷键、关闭自动粘贴或开启登录自启。

## 命令行手动转换

1. 在 WPS 中打开或新建 `.docx` 文档。原文件为 `.wps` 时先另存为 `.docx`，再重新粘贴。
2. 复制 Markdown 或 AI 网页正文。
3. 在仓库目录执行 `python3 scripts/pastemd-wayland.py`。
4. 等待“公式富文本已就绪”的通知或终端提示，切回 WPS 按 Ctrl+V。

默认写入 WPS 原生剪贴板，不打开中间文档。转换成功后原剪贴板会被替换，不自动恢复。
再次转换前重新复制来源。剪贴板服务在后台保有转换结果，复制其他内容后自动退出。

默认优先读取 HTML，没有 HTML 时读取纯文本并按 Markdown 转换。
希望强制使用复制按钮产生的 Markdown 时，使用 `--input markdown`。
支持 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 数学分隔符。

## 图形界面和系统集成

界面提供输入格式选择、公式测试、DOCX 备选输出、快捷键配置、自动粘贴、登录自启和运行记录。默认快捷键是 `Ctrl+Shift+B`。

自动粘贴会核对转换前后的 WPS 窗口及文档标题，并等待快捷键松开；焦点发生变化时只准备剪贴板，不会向其他窗口发送按键。
关闭主窗口后程序驻留系统托盘，可从托盘再次打开或退出。

将 PasteMD Linux 添加到 KDE 应用菜单：

```bash
python3 scripts/pastemd-linux.py --install
```

也可以在设置页点击“添加到应用菜单”。登录自启可在设置页开启。
设置保存在 `${XDG_CONFIG_HOME:-~/.config}/pastemd-linux/settings.json`。

如果桌面不是 KDE，程序仍可手动转换；可在桌面系统设置中把下列命令绑定为全局快捷键：

```text
/usr/bin/python3 /home/pxx/桌面/pastemd/PasteMD/scripts/pastemd-linux.py --trigger
```

## 内置样本

不需要预先复制任何内容，运行：

```bash
python3 scripts/pastemd-wayland.py --demo
```

然后在 WPS 的空白 `.docx` 文档中 Ctrl+V。样本包含中文、粗体、行内公式、分数、三次根式、求和、矩阵和表格。
检查显示效果，并点击公式确认能编辑分子等内容。

## DOCX 备选模式

```bash
# 仅保存 DOCX 并输出路径，保留剪贴板
python3 scripts/pastemd-wayland.py --docx

# 保存 DOCX 并用 WPS 打开，保留剪贴板
python3 scripts/pastemd-wayland.py --open

# 显式指定原生剪贴板，与默认行为相同
python3 scripts/pastemd-wayland.py --clipboard
```

保存的 DOCX 位于 `${XDG_CACHE_HOME:-~/.cache}/pastemd`，需要自行清理。
默认剪贴板模式在内存中生成、传递 DOCX，不保存中间文档。

## 实现与限制

- Pandoc 将网页/Markdown 解析成文档结构，清理页面偏移、隐藏 span/div 包装以及重复 KaTeX 展示层。
- 生成包含 OMML 原生公式的 DOCX，并校验公式数量；转换失败或公式数减少时不写剪贴板。
- 实际检查 WPS 原生复制样本发现，其 `Kingsoft WPS 9.0 Format` 内容为 DOCX ZIP。PySide6 通过 XWayland 向这个格式写入完整 DOCX，同时提供纯文本。
- 不提供 HTML 或 RTF 图片回退，避免 WPS 优先选择它们而丢失公式编辑能力。

清理网页包装会丢弃其颜色、字号和页面布局。未接入上游所有网页公式恢复、图片与样式修复逻辑。
`.wps` 格式、复杂公式、图片、版式和不同 WPS 版本仍需验证。剪贴板使用的是观测到的 WPS 原生格式，未来版本可能改变。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_wayland_cli.py'
```

测试实际生成原生剪贴板 DOCX，检查行内/块级公式、分数、根式、求和、矩阵及表格，且公式没有被替换成图片。
同时覆盖转换失败不写剪贴板、DOCX 临时文件清理、网页隐藏布局和四种数学分隔符。
测试结果不能代表所有 WPS 版本与目标文档格式。

桌面界面测试：

```bash
python3 -m unittest discover -s tests -p 'test_linux_desktop.py'
```

参考：[Qt 剪贴板生命周期](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QClipboard.html)、[Pandoc 数学公式输出](https://pandoc.org/MANUAL.html#math)。
