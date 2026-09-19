# 更新日志

## 0.6.2-linux（2026-09-19）

### 修复

- **WPS 改名/无扩展名文档不自动粘贴**：统一套件窗口 `wpsoffice` 此前只认扩展名和
  「文字文稿N」等默认名，用户改成「期末报告」、英文 Document/Untitled，或
  「文档1 / 未命名」时分类为 None，热键只留剪贴板。现认英文 Sheet/Workbook、
  Presentation、Document/Untitled，以及「未命名」「文档N」；排除表格/演示后，
  非裸 `WPS Office` 且标题以 ` - WPS Office` 结尾则兜底为文字
- **混合 Markdown 抽不出表**：AI 复制常见「标题 + 段落 + 表格」，表格解析此前
  遇到前置非表格行就返回 None。现跳过前置文字，取第一张 GFM 表，空行结束当前表
- **多次热键把转换结果再当源**：第一次转换会覆盖剪贴板，再按 `Ctrl+Shift+B`
  会把 DOCX/表格结果当 Markdown 再转一遍。现把原文和流程标记进剪贴板：同一窗口
  直接再次粘贴；换到表格/文字则从原文重转。复制新内容后标记消失，行为与以前相同
- **表格窗口但剪贴板不是表**：热键走表格流时若抽不出 GFM 表，不再报失败，
  改为按文档流转换并粘贴

### 测试

- 106 → 115 项：改名/英文空白文档标题、混合 Markdown 抽第一张表、热键复用转换结果、表格非表回退

## 0.6.1-linux（2026-09-19）

### 修复

- **AppImage 真机崩溃**：打包时 PyInstaller 把 Fedora 构建机的
  `libxkbcommon.so.0` 收进 bundle，运行时与系统 `libxkbcommon-x11`/
  `libxcb` 混载，Qt xcb 键盘路径一被触发（0.6.0 新增的粘贴后
  Ctrl+End）就段错误——表现为按热键后 GUI 消失、只弹崩溃报告。
  现在构建时剥离全部捆绑的 X11/xcb/xkb 库，统一使用目标系统库栈
- **WPS 新建空白文档不自动粘贴**：WPS Linux 无标题文档的窗口标题是
  「文字文稿N / 演示文稿N / 表格N」（无扩展名），分类逻辑此前只认
  扩展名和「wps文字」等关键字，导致按热键后误判为"非 WPS 窗口"而
  留在剪贴板。现按 表格N/工作簿 → 演示文稿N → 文字文稿N 顺序识别，
  真机 10 组标题形态全部通过
- KWin 焦点查询新增 `PASTEMD_DEBUG=1` 诊断日志（排障用）

### 测试

- 105 → 106 项：新增 WPS 真实空白文档标题形态的回归锁定

## 0.6.0-linux（2026-09-18）

### 新增

- **按转换类型配置 Pandoc 过滤器**（`pandoc_filters_by_conversion`，对齐上游）：
  设置页为 MD→DOCX、HTML→DOCX、MD→Markdown、HTML→Markdown、MD→LaTeX 等
  8 种转换分别指定过滤器，Mermaid 图表等按流程单独挂载，互不影响
- **保留生成的 DOCX 文件**（`keep_file` + `save_dir`，对齐上游）：原生剪贴板
  流程可在粘贴之外把 DOCX 落盘；保存目录带时间戳文件名，目录不可用时自动
  回退缓存；设置页新增开关和目录选择，托盘新增「打开 DOCX 保存目录」
- **粘贴后光标移到文档末尾**（`move_cursor_to_end`，默认开启，对齐上游）：
  粘贴完成后发送 Ctrl+End，连续粘贴按顺序追加内容；可在设置页关闭

### 修复

- **公式还原修复**：`semantic-html.lua` 的 `MATH_CLASSES` 被同名局部变量
  遮蔽，只携带 `math-inline`/`math-block` 类（不带 `math` 类）的公式 span
  此前不会还原为原生公式，现合并四类 class 统一命中
- **应用规则健壮性**：标题正则可包含 `|`（此前会被错误拆分）；手工编辑
  配置导致的非法类型规则（`class` 为数字等）整条忽略，不再抛异常或意外
  放宽匹配；字符串型 `window_patterns` 回填不再损坏
- **发版保障**：Release 流水线新增 metainfo 版本条目硬校验，缺失即失败
- **`--demo --table`**：不再静默忽略 demo，改用内置示例表格且不读系统剪贴板
- **表格 TSV**：单元格内 LaTeX 命令的反斜杠（如 `\Delta`）不再被吞掉；
  制表符替换为空格防止串列
- CLI 依赖检查与 GUI 统一走 `PASTEMD_PANDOC_BIN` 解析

### 测试

- 91 → 105 项：行内代码/围栏 `$` 保护、`~~~` 围栏、裸分隔行、TSV 转义、
  规则边界、分类型过滤器选路、保留文件、Ctrl+End 键序列等回归锁定

## 0.5.1-linux（2026-09-18）

### 修复

- **行内代码与围栏代码块内的 `$` 不再被改写**：`$  ls  $` 这类 shell
  变量、价格等普通文本此前会被误压缩成"公式"样式，现在
  `\`…\``片段与 ```/~~~ 围栏内整体原样保留，真正的行内公式
  `$  x^2  $` 修复行为不变
- **`~~~` 围栏正确识别**：normalize 不再把围栏内内容当普通段落插空行，
  块内出现 ``` 也不再错误翻转围栏状态；``` 围栏行为不变
- **无前导竖线的 GFM 表格分隔行**（`---|---|---`）不再被当成水平线拆断
  表格；裸 `---` 仍按水平线处理
- **Markdown 直通路径尊重设置**：md→md 文本流此前忽略
  `fix_single_dollar_block` 开关，现与 pandoc 路径行为一致
- **表格 TSV 防串列**：单元格含制表符时替换为空格，避免粘贴到 WPS
  表格后错列
- **表格转义语义修正**：`\|` 为字面竖线，`\\` 为字面反斜杠（其后
  竖线是分隔符），双反斜杠不再把两列并成一列
- **GUI 统一 pandoc 解析**：界面内五处硬编码 `pandoc` 改走
  `PASTEMD_PANDOC_BIN`，与 CLI/测试一致

### 测试

- 新增 5 个回归测试（96 项全绿）：行内代码/围栏 `$` 保护、`~~~` 围栏、
  裸分隔行、TSV 制表符、转义语义与直通设置透传

## 0.5.0-linux（2026-09-18）

### 新增

- **无规则窗口行为可配**（`no_app_action`）：焦点窗口未匹配任何应用规则时，
  可选「留在剪贴板手动粘贴（默认）/ 仍按文档流粘贴 / 每次询问」；询问
  对话框支持一键"为此窗口建规则"，衔接设置页规则编辑框
- **托盘规则入口**：托盘菜单实时探测焦点窗口，非 WPS 窗口直接给
  「为「窗口标题」建规则…」，点一下写进规则并打开设置页确认
- **代码块高亮配色可配**（`code_highlight_style`）：Tango（默认，与此前
  行为一致）/ Pygments / Kate / Espresso / Zenburn / 单色 / 关闭，对应
  docx writer 的 `--highlight-style`

### 工程改进（对用户透明）

- 发版走 GitHub Actions：推 tag 自动构建 AppImage + Flatpak 双包并创建
  Release（发布说明从 CHANGELOG 对应小节提取，缺小节即失败）；已用
  rc1-rc3 完成实弹演练
- CI 增加 ruff + shellcheck 静态检查、Lua 黄金 fixtures（锁定过滤器
  行为，防 pandoc 升级回归，仅认官方 pandoc 3.7.0.2）
- 版本号单一来源（`pastemd/__init__.py`），打包脚本自动派生
- 真机冒烟套件收编进 `scripts/smoke/`

## 0.4.0-linux（2026-09-17）

### 新增

- **应用规则拾取器**：设置页每个流程新增「从窗口拾取…」，列出当前运行的
  窗口（KDE Wayland 走 KWin 枚举，X11 会话走 XQueryTree 降级），双击即
  生成 `显示名 | class |` 规则行，不再需要手动查 WM_CLASS；另附常用应用
  预设（语雀/Notion/Typora/Obsidian/飞书）
- **pre-wrap 换行还原**：`white-space:pre-wrap` 块（聊天记录、代码展示区）
  的源码换行从"渲染成空格"还原为硬换行——docx 落 `w:br`、Markdown 落
  gfm 硬换行；class 型 pre-wrap 经样式表解析识别；设置页可关闭
- **Obsidian 公式恢复**：`span.math`/`div.math`（math-inline/math-block）
  包裹的 LaTeX 文本还原为原生公式节点，兼容单/双反斜杠与 `$…$` 定界符

### 修复

- KWin 服务名回退规则：DBus 名字组件不允许以数字开头，修复 GUI 与 CLI
  并存时第二实例 KWin 焦点检测完全失效的存量问题

### 说明

- 调查过用 wl-copy 从 Wayland 原生端写剪贴板以消除 XWayland 桥的
  latin-1 `text/plain` 合成变体（中文显示 `?` 的来源）：实测本机 KWin
  不向 X11 客户端代发 Wayland 端写入（连 Klipper 自身写入 X11 也读不
  到），而 WPS/语雀/Chrome 均为 X11 客户端，故保留 X11 服务进程方案；
  数据本体无损（`text/plain;charset=utf-8` 变体正确）。

## 0.3.1-linux（2026-09-17）

### 修复

- **Markdown 文本粘贴质量**（`--as md`、应用扩展规则 md 流程）：
  - 输出改用 `gfm-raw_html --wrap none`：剥离 pandoc 转不干净的残留
    HTML 标签（语雀/Notion 里会显示为字面乱码），并取消 72 列硬折行
  - gfm writer 的 GitLab 数学定界符 `` $`…` `` 统一还原为兼容性更好的
    `$…$`，公式粘进语雀/Notion 可直接识别
- **任务列表保护**：剪贴板里的 `[x]`/`[ ]` 在转 Markdown 前用占位符
  保护，不再被转义成 `\[x]`（仅文本粘贴流程启用，DOCX 流程不受影响）
- **热键防抖**：对齐上游 `FIRE_DEBOUNCE_SEC`，0.5 秒内的重复触发直接
  忽略，避免转换刚结束时的连按造成重复粘贴

### 说明

- 上游的 `+raw_tex` markdown 阅读器扩展未启用：对 `$$…$$` 内的
  对齐/矩阵环境（AI 输出的常见形态）无额外收益，反而会让裸 LaTeX
  环境在 DOCX 流程中被静默丢弃；维持现状（字面文本，可人工核对）。

## 0.3.0-linux（2026-09-17）

### 新增

- **网页语义恢复（对齐上游 HTML 预处理）**：
  - AI 页面公式节点（`data-math-source` / `copy-text` 属性）直接还原为
    原生公式，覆盖元宝等国内 AI 站点的"渲染层损坏"剪贴板；
  - WPS/Excel 复制的表格把样式写在 `<style>` 的 class 里，Pandoc 不读
    样式表，现在会解析并还原 class 级加粗/斜体
    （`html_formatting.css_font_to_semantic`，默认开）；
  - 表格首行全加粗时提升为真表头（`html_formatting.bold_first_row_to_header`，
    实验，默认关）；
  - `.svg` 位图引用提前剔除，不再误报图片丢失。
- **Markdown 规范化（移植上游 md_normalizer）**：标题/代码块/表格/列表/
  引用之间的缺失空行自动补齐，AI 输出的粘连段落不再被并成一段。
- **智能输入识别（移植上游 html_analyzer 思路）**：剪贴板同时带
  `text/html` 与 `text/plain` 时，若 HTML 只是内联样式包装而纯文本带足
  Markdown 特征，自动改走 Markdown 流程——从 VSCode 等编辑器复制时
  语法标记不再丢失。
- 删除线 `<s>/<strike>` 由 Pandoc 原生支持（上游的
  `html_formatting.strikethrough_to_del` 在本分支为无操作，保留键名兼容）。

## 0.2.0-linux（2026-09-17）

### 新增

- **转换增强（对齐上游）**：LaTeX 语法修复（`\kern` 等）、单 `$` 公式块修复、
  Markdown 硬换行、禁用 Pandoc 首段缩进样式、水平线转段落边框线（实验）、
  表格列宽自适应（实验）、自定义样式模板（reference.docx）、自定义 Pandoc
  过滤器与请求头。
- **智能表格粘贴（实验）**：前台是 WPS 表格时，热键自动改走「Markdown 表格 →
  HTML 表格」流程，表头加粗底色、单元格支持行内格式；TSV 兜底。
- **应用扩展规则**：可为指定窗口（WM_CLASS / 标题正则）把粘贴模式换成
  Markdown、LaTeX 或 HTML 纯文本，适配语雀、Notion、腾讯文档等编辑器。
  设置页新增规则编辑，配置键 `extensible_workflows` 与上游同名同构。
- **CLI**：`--table` 验证表格链路；`--as md|latex|html` 验证文本格式链路。
- **Plasma Wayland 焦点检测**：XWayland 的 X 焦点只见 1×1 代理窗口，
  改经 KWin 脚本接口查询激活窗口，自动粘贴与窗口识别首次在该平台完整可用；
  非 KDE 会话自动退回 X11 查询。

### 修复

- 新版 WPS Office 各套件窗口类统一为 `wpsoffice`，改由 `_NET_WM_NAME`
  标题扩展名区分文字/表格/演示；旧类名（wps/et/wpp）仍然支持。
- `wpscloudsvr` 等辅助窗口不再被误判为 WPS 文字。
- KWin 脚本实例二次运行不可靠，改为每次查询重新加载。
- 单列表格分隔符 `|---|` 无法解析（上游同款问题）。

### 已知限制

- 界面暂仅中文（上游有 en/ja/zh 资源，多语言界面留待后续版本）。
- `file` 可扩展工作流（上游支持）在 Linux 分支未实现。
- WPS 表格粘贴依赖其 `text/html` 支持；不同版本表现可能不同。

## 0.1.2-linux

- 首个公开发布的 Linux 分支版本：Fedora KDE Wayland + WPS 适配、
  PySide6 图形界面、KDE 全局快捷键、焦点校验自动粘贴、原生公式 DOCX。
