# Fedora + Wayland + WPS（实验性入口）

2026-09-05：新增 WPS 原生 DOCX 剪贴板链路，沿用 AGPL-3.0。
用户已在 Fedora Wayland + WPS 的 `.docx` 目标文档中确认粘贴正常；使用 `.wps` 格式时曾出现公式变成图片。
**目标文档请使用 `.docx` 格式。** 上游完整托盘功能尚未适配 Linux。

## 安装

先安装 WPS Linux 版，再安装转换与剪贴板依赖：

```bash
sudo dnf install pandoc wl-clipboard python3-pyside6
```

需要 Wayland 会话中的 XWayland（`DISPLAY`）。使用系统 `/usr/bin/python3`，确保能导入系统安装的 PySide6。
当前方案不依赖 LibreOffice 或 WPS RPC 插件。

## 直接 Ctrl+V

1. 在 WPS 中打开或新建 `.docx` 文档。原文件为 `.wps` 时先另存为 `.docx`，再重新粘贴。
2. 复制 Markdown 或 AI 网页正文。
3. 在仓库目录执行 `python3 scripts/pastemd-wayland.py`。
4. 等待“公式富文本已就绪”的通知或终端提示，切回 WPS 按 Ctrl+V。

已经变成图片的公式不会因为另存为 `.docx` 自动恢复，需要重新从原始内容转换、粘贴。

默认写入 WPS 原生剪贴板，不打开中间文档。转换成功后原剪贴板会被替换，不自动恢复。
再次转换前重新复制来源。剪贴板服务在后台保有转换结果，复制其他内容后自动退出。

默认优先读取 HTML，没有 HTML 时读取纯文本并按 Markdown 转换。
希望强制使用复制按钮产生的 Markdown 时，使用 `--input markdown`。
支持 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 数学分隔符。

## 内置样本

不需要预先复制任何内容，运行：

```bash
python3 scripts/pastemd-wayland.py --demo
```

然后在 WPS 的空白 `.docx` 文档中 Ctrl+V。样本包含中文、粗体、行内公式、分数、三次根式、求和、矩阵和表格。
检查显示效果，并点击公式确认能编辑分子等内容。

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

参考：[Qt 剪贴板生命周期](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QClipboard.html)、[Pandoc 数学公式输出](https://pandoc.org/MANUAL.html#math)。
