# Fedora + Wayland + WPS（实验性入口）

2026-09-05：新增并修正 Linux 命令行入口，沿用 AGPL-3.0。上游完整托盘程序尚未适配 Linux。

## 安装与运行

先安装 WPS Linux 版，然后安装系统依赖（脚本仅需 Python 3 标准库）：

```bash
sudo dnf install pandoc wl-clipboard
```

重新从来源复制 Markdown 或网页正文，在仓库目录运行：

```bash
python3 scripts/pastemd-wayland.py
```

默认生成 DOCX 并用 WPS 打开，保留原剪贴板。
在生成的文档里确认公式，再选中需要的内容复制到目标 WPS 文档。
这是当前的过渡方案，尚未实现直接插入原文档光标位置；WPS 内复制公式仍需实机验证。

默认优先读取 HTML，没有 HTML 时读取纯文本并按 Markdown 转换。
如果复制按钮同时携带 HTML，但你希望按 Markdown 处理，使用 `--input markdown`。
支持 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 数学分隔符。

## 本次公式修复

第一版输出携带网页隐藏样式的 HTML，DeepSeek 的 KaTeX MathML 被包在裁切为 1px 的元素中，
还携带大尺寸留白与页面偏移。现通过 Pandoc 文档结构移除 span/div 包装和重复 KaTeX 展示层，
保留标题、强调、列表、表格和 Math 节点，再生成 DOCX 原生 OMML 公式。
移除网页包装也会丢弃这些元素上的颜色、字号和布局，这是当前采用文档默认样式的取舍。

自动测试验证了同类 DeepSeek 隐藏公式结构能够生成 DOCX 分数公式、中文及表格，
未验证所有网页 AI 公式格式、图片和 WPS 最终显示；没有接入上游全部预处理与样式修复。

## 快捷键

在桌面设置的自定义快捷键中绑定 Ctrl+Shift+B，命令使用实际绝对路径，例如：

```text
/usr/bin/python3 /home/pxx/桌面/pastemd/PasteMD/scripts/pastemd-wayland.py
```

由桌面环境管理快捷键。执行后打开生成的 DOCX，没有模拟按键或窗口识别。

## 其他模式

```bash
# 仅保存 DOCX 并输出路径，不打开 WPS
python3 scripts/pastemd-wayland.py --docx

# 显式打开 DOCX，与默认行为相同
python3 scripts/pastemd-wayland.py --open

# 实验性 HTML 剪贴板模式，随后自行 Ctrl+V
python3 scripts/pastemd-wayland.py --clipboard
```

`--clipboard` 输出 HTML 片段，不添加网页样式表和 PasteMD 标题。
它会替换剪贴板且不自动恢复；WPS 对 HTML/MathML 的支持不保证，可能丢失公式或粘贴为源码。
含公式内容请使用默认 DOCX 模式。切换模式前重新复制来源，避免再次转换上次输出。

文件保存在 `${XDG_CACHE_HOME:-~/.cache}/pastemd`，需要自行清理。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_wayland_cli.py'
```

测试包括失败时保留剪贴板、DOCX 清理、网页隐藏样式与重复公式展示层、四种数学分隔符。
安装 Pandoc 后会实际生成 DOCX 并检查原生公式与表格结构。
