# 更新日志

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
