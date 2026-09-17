# PasteMD Linux：Fedora + KDE Wayland + WPS

Linux 版现已提供 WPS 原生 DOCX 剪贴板、图形界面、系统托盘、KDE 全局热键和焦点保护自动粘贴，沿用 AGPL-3.0。
已在 Fedora、KDE Wayland 和 WPS Linux 的 `.docx` 文档中验证可编辑公式。

> **文档必须使用 `.docx` 格式。** WPS 的 `.wps` 格式会把粘贴的公式变成图片；另存为 `.docx` 不会修复已经图片化的公式，需要从原内容重新转换和粘贴。

## 安装

先安装 WPS Linux 版，再安装转换与剪贴板依赖：

```bash
sudo dnf install pandoc wl-clipboard python3-pyside6 python3-dbus python3-gobject libX11 libXtst
```

需要 Wayland 会话中的 XWayland（`DISPLAY`）。使用系统 `/usr/bin/python3`，确保能导入系统安装的 PySide6。
当前方案不依赖 LibreOffice 或 WPS RPC 插件。PySide6 提供界面和 XWayland 剪贴板，D-Bus 与 GLib 用于 KDE 全局热键，X11/XTest 用于检查 WPS 焦点并发送粘贴按键。

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
/usr/bin/python3 ~/PasteMD-Linux/scripts/pastemd-linux.py --trigger
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

## 应用扩展规则（按窗口切换粘贴格式）

设置页下方三个文本框分别对应「粘贴 Markdown / LaTeX / HTML 文本」流程。
每行一条规则，格式为 `显示名 | WM_CLASS | 窗口标题正则`（后两列可留空）：

```text
语雀 | yuque | 语雀
腾讯文档 || docs\.qq\.com
```

热键触发时若前台窗口命中某条规则，剪贴板会改为写入对应格式的纯文本
（网页来源会先用 Pandoc 转换），随后照常自动粘贴。WM_CLASS 用 token
精确匹配（`et` 不会误伤 `netease`）；标题正则用 `re.search` 匹配。
配置键 `extensible_workflows` 与上游同名同构（Linux 分支不含 file 工作流）。

命令行可以单独验证文本链路：`--as md`、`--as latex`、`--as html`。

## 智能表格粘贴（实验，适配 WPS 表格）

热键触发时若前台是 WPS 表格（WM_CLASS 为 `et`/`ket`），PasteMD 会改走表格流程：

1. 正常复制 Markdown 表格，或包含表格的网页内容。
2. 在 WPS 表格中把光标放到目标位置，按热键。
3. PasteMD 解析表格（纯文本优先；只有 HTML 时用 Pandoc 转成 Markdown 再解析），
   以 `text/html` 表格加 `text/plain` TSV 兜底写入剪贴板，并自动粘贴。

表格首行渲染为加粗底色表头，单元格支持加粗、斜体、删除线、行内代码和超链接。
前台是 WPS 文字或其他窗口时仍是文档流程，行为不变。开关在设置页
"WPS 表格窗口自动改用表格粘贴"，对应配置键 `enable_excel`（与上游同名）。

命令行也可以单独验证表格链路：

```bash
# 复制一个 Markdown 表格后执行，随后在 WPS 表格中 Ctrl+V
python3 scripts/pastemd-wayland.py --table
```

## 转换增强（对齐上游 PasteMD）

以下选项与上游 RICHQAQ/PasteMD 的 `config.json` 键名一致，可在设置页调整，或直接编辑
`${XDG_CONFIG_HOME:-~/.config}/pastemd-linux/settings.json`：

- `enable_latex_replacements`：修复 AI 公式常见的 `\kern` 等语法（默认开）。
- `fix_single_dollar_block`：把单独成行的 `$` 修复为 `$$` 块级公式，并收紧 `$ x $` 间距（默认开）。
- `markdown_hard_line_breaks`：Markdown 内单个换行视为硬换行（默认关）。
- `keep_original_formula`：公式以 `$…$` 原始文本插入而不是原生公式（默认关；开启时原生公式校验自动跳过）。
- `md_disable_first_para_indent` / `html_disable_first_para_indent`：禁用 Pandoc 的首段缩进样式，统一为正文样式（默认开）。
- `horizontal_rule_style`：`default` 保留 Pandoc 横线，`paragraph_border` 转为 WPS/Word 段落边框线。
- `docx_auto_table_layout`：按内容自动调整表格列宽（实验，默认关）。
- `enable_excel`：热键时前台是 WPS 表格（`et`/`ket`）则自动改用表格粘贴流程（默认开，见"智能表格粘贴"）。
- `reference_docx`：Pandoc 参考文档模板路径，套用自定义字体、页边距和样式。
- `pandoc_request_headers`：抓取远程图片时的请求头（默认带浏览器 User-Agent）。
- `pandoc_filters`：追加自定义 Pandoc 过滤器（`.lua` 或可执行文件），作用于解析阶段。

## 实现与限制

- Pandoc 将网页/Markdown 解析成文档结构，清理页面偏移、隐藏 span/div 包装以及重复 KaTeX 展示层。
- 生成包含 OMML 原生公式的 DOCX，并校验公式数量；转换失败或公式数减少时不写剪贴板。
- 实际检查 WPS 原生复制样本发现，其 `Kingsoft WPS 9.0 Format` 内容为 DOCX ZIP。PySide6 通过 XWayland 向这个格式写入完整 DOCX，同时提供纯文本。
- 不提供 HTML 或 RTF 图片回退，避免 WPS 优先选择它们而丢失公式编辑能力。
- 远程图片由 Pandoc 在转换时抓取并嵌入 DOCX；无网络或图片失效时会被替换为文字说明，完成提示会注明丢失数量。Flatpak 沙箱需要 `--share=network` 权限（清单已包含）。
- 焦点检测在 Plasma Wayland 下走 KWin 脚本接口（XWayland 的 X 焦点只见 1×1 代理窗口）：
  查询激活窗口的标题和 WM_CLASS 来识别 WPS 文字/表格/演示，Flatpak 清单需要
  `--talk-name=org.kde.KWin` 与 `--own-name=io.github.GMagisk9527.PasteMDLinux`（已包含）。
  非 KDE 会话自动退回 X11 焦点查询。新版 WPS 各套件窗口类统一为 `wpsoffice`，
  依赖 `_NET_WM_NAME` 标题中的扩展名（`.docx`/`.et` 等）区分套件。

清理网页包装会丢弃其颜色、字号和页面布局。未接入上游所有网页公式恢复、图片与样式修复逻辑。
`.wps` 格式、复杂公式、图片、版式和不同 WPS 版本仍需验证。剪贴板使用的是观测到的 WPS 原生格式，未来版本可能改变。
界面暂仅中文；上游的 en/ja 多语言资源尚未移植。

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

## 上游与许可证

本仓库派生自 [RICHQAQ/PasteMD](https://github.com/RICHQAQ/PasteMD)，保留原作者、历史贡献者和 GNU AGPL-3.0 许可证。Linux 适配的来源与版权说明见 [NOTICE.md](../NOTICE.md)，系统组件见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

### 打包体积

Flatpak 基于 PySide BaseApp 构建，清理阶段通过其自带脚本移除未使用的
QtWebEngine 和 NumPy，并去掉绑定生成器、LLVM/Clang 和 PyOpenGL。
保留 PySide/Shiboken 运行库、Pandoc、D-Bus 和剪贴板依赖。
修改清理规则后应执行 `--package-self-test` 和离屏 GUI 冒烟检查。
Flatpak 安装时所需的共享 KDE runtime 不包含在 `.flatpak` 文件体积中。

AppImage 使用 Zstandard 19 级压缩和最多 4 个压缩线程。Pandoc 与 Qt
运行库占主要空间，提高压缩等级的收益有限，但会增加构建时间。

AppImage 的自定义 PyInstaller hooks 在依赖扫描前筛选 Qt 插件：保留
GIF/JPEG/ICO/SVG/WebP（PNG 由 Qt 内置支持）、Fcitx/IBus 输入法及
桌面平台插件；不带入 PDF/RAW/HEIF/AVIF 等额外图像解码插件、虚拟键盘、
GTK/KDE 平台主题和 Breeze 样式。界面使用 Qt 默认样式，外观可能与系统
主题不同。这些规则仅影响界面插件，不裁剪 Pandoc 的 DOCX 转换功能。
