"""Native Qt desktop UI for Fedora KDE/Wayland."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

from PySide6.QtCore import (QEvent, QLockFile, QTimer, Qt, QThread, Signal, QUrl)
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QKeySequenceEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget,
    QVBoxLayout, QWidget, QMenu)

from . import cli
from .hotkey import KdeHotkey
from .kwin import KWinFocus
from .settings import (ROOT, DEFAULTS, load_settings, save_settings, autostart_path, set_autostart, install_launcher)
from .x11 import X11Paste
from ..utils import apprules


CLIPBOARD_TOKEN_MIME = 'application/x-pastemd-conversion-id'


class ConversionWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, input_format, demo=False, open_docx=False, options=None, flow='doc', parent=None):
        super().__init__(parent)
        self.input_format, self.demo, self.open_docx = input_format, demo, open_docx
        self.options = options or {}
        self.flow = flow

    def run(self):
        try:
            if self.flow == 'table':
                payload, rows = cli.table_clipboard_payload(cli.read_table_source())
                token = uuid.uuid4().hex.encode('ascii')
                payload[CLIPBOARD_TOKEN_MIME] = token
                cli.set_clipboard_payload(payload)
                self.completed.emit({'clipboard': True, 'token': token, 'rows': rows})
                return
            if self.flow in cli.TEXT_FORMAT_LABELS:
                if self.demo:
                    source, reader = cli.DEMO_MARKDOWN.encode(), 'markdown' + cli.MATH_EXTENSIONS
                else:
                    source, reader = cli.read_clipboard(self.input_format)
                if not source.strip():
                    raise RuntimeError('剪贴板内容为空，请先复制 Markdown 或网页正文。')
                document = cli.prepare_document(source, reader, self.options,
                                                protect_task_lists=(self.flow == 'md'),
                                                conversion=cli.conversion_type(reader, self.flow))
                payload = cli.text_clipboard_payload(source, document, reader, self.flow,
                                                     self.options)
                token = uuid.uuid4().hex.encode('ascii')
                payload[CLIPBOARD_TOKEN_MIME] = token
                cli.set_clipboard_payload(payload)
                self.completed.emit({'clipboard': True, 'token': token, 'text_flow': self.flow})
                return
            if self.demo:
                source, reader = cli.DEMO_MARKDOWN.encode(), 'markdown' + cli.MATH_EXTENSIONS
            else:
                source, reader = cli.read_clipboard(self.input_format)
            if not source.strip():
                raise RuntimeError('剪贴板内容为空，请先复制 Markdown 或网页正文。')
            document = cli.prepare_document(source, reader, self.options,
                                            conversion=cli.conversion_type(reader, 'docx'))
            if self.open_docx:
                name = cli.docx_output_path(self.options)
                try:
                    docx = cli.run([cli.pandoc_bin(), '--from', 'json'] + cli.docx_writer_args(self.options)
                                   + ['--to', 'docx', '--output', '-'], document)
                    docx = cli.finish_docx(docx, reader, self.options)
                    Path(name).write_bytes(docx)
                except Exception:
                    Path(name).unlink(missing_ok=True)
                    raise
                lost = cli.lost_image_count(document, docx)
                self.completed.emit({'path': name, 'lost_images': lost})
            else:
                plain = cli.run([cli.pandoc_bin(), '-f', 'json', '-t', 'plain'], document)
                payload = cli.native_clipboard_payload(document, plain, self.options, reader)
                token = uuid.uuid4().hex.encode('ascii')
                payload[CLIPBOARD_TOKEN_MIME] = token
                cli.set_clipboard_payload(payload)
                lost = cli.lost_image_count(document, payload['Kingsoft WPS 9.0 Format'])
                if self.options.get('keep_file'):
                    try:
                        keep_path = cli.docx_output_path(self.options)
                        Path(keep_path).write_bytes(payload['Kingsoft WPS 9.0 Format'])
                        self.completed.emit({'clipboard': True, 'token': token,
                                             'lost_images': lost,
                                             'keep_path': str(keep_path)})
                        return
                    except OSError as keep_error:
                        self.failed.emit(f'文件保留失败：{keep_error}')
                        return
                self.completed.emit({'clipboard': True, 'token': token, 'lost_images': lost})
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self, smoke=False):
        super().__init__()
        self.smoke = smoke
        self.worker = None
        self.paste_pending = False
        self._last_convert = 0.0
        self.pending_quit = False
        self.target = None
        self.want_paste = False
        self.clipboard_token = None
        self.lost_images = 0
        self.flow = 'doc'
        self.paste_deadline = 0
        self.x11 = None
        self.kwin = None
        self.hotkey = None
        self.tray = None
        self.closing = False
        self.startup_error = ''
        try:
            self.settings = load_settings()
        except RuntimeError as error:
            self.settings = dict(DEFAULTS)
            self.startup_error = str(error)
        self.setWindowTitle('PasteMD Linux')
        self.setWindowIcon(QIcon(str(ROOT / 'assets/icons/logo.png')))
        self.resize(760, 640)
        self.setMinimumSize(640, 570)
        self._build_ui()
        self._build_tray()
        self.paste_timer = QTimer(self)
        self.paste_timer.setInterval(50)
        self.paste_timer.timeout.connect(self._try_paste)
        self._refresh_dependencies()
        if not smoke:
            QTimer.singleShot(0, self._start_services)

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(18)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(48, 48))
        header.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel('PasteMD Linux')
        title.setStyleSheet('font-size: 25px; font-weight: 700;')
        titles.addWidget(title)
        titles.addWidget(QLabel('把 AI 内容和公式，粘贴到 WPS'))
        header.addLayout(titles)
        header.addStretch()
        self.status = QLabel('准备就绪')
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(220)
        header.addWidget(self.status)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        home = QWidget()
        home_layout = QVBoxLayout(home)
        home_layout.setContentsMargins(18, 20, 18, 16)
        home_layout.setSpacing(14)
        self.instructions = QLabel()
        self.instructions.setWordWrap(True)
        self.instructions.setStyleSheet('font-size: 16px; line-height: 1.5;')
        home_layout.addWidget(self.instructions)
        tip = QLabel('目标文档请使用 .docx。已有的 .wps 文档先另存为 .docx，再重新粘贴公式。')
        tip.setWordWrap(True)
        home_layout.addWidget(tip)
        form = QFormLayout()
        self.input_format = QComboBox()
        for text, value in [('自动识别', 'auto'), ('Markdown 文本', 'markdown'), ('网页 HTML', 'html')]:
            self.input_format.addItem(text, value)
        self.input_format.setCurrentIndex(self.input_format.findData(self.settings['input_format']))
        form.addRow('输入格式', self.input_format)
        home_layout.addLayout(form)
        actions = QHBoxLayout()
        self.convert_button = QPushButton('转换到剪贴板')
        self.convert_button.setMinimumHeight(40)
        self.convert_button.clicked.connect(lambda: self.convert())
        self.demo_button = QPushButton('测试公式')
        self.demo_button.clicked.connect(lambda: self.convert(demo=True))
        self.docx_button = QPushButton('生成并打开 DOCX')
        self.docx_button.clicked.connect(lambda: self.convert(open_docx=True))
        for button in (self.convert_button, self.demo_button, self.docx_button):
            actions.addWidget(button)
        home_layout.addLayout(actions)
        home_layout.addWidget(QLabel('手动转换后按 Ctrl+V；使用热键时可自动粘贴。'))
        self.dependencies = QLabel()
        self.dependencies.setWordWrap(True)
        home_layout.addWidget(self.dependencies)
        home_layout.addWidget(QLabel('运行记录'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(100)
        self.log.setPlaceholderText('转换结果和快捷键状态会显示在这里。')
        home_layout.addWidget(self.log, 1)
        self.tabs.addTab(home, '转换')

        settings_tab = QWidget()
        controls = QVBoxLayout(settings_tab)
        controls.setContentsMargins(18, 20, 18, 16)
        controls.setSpacing(16)
        settings_form = QFormLayout()
        self.hotkey_enabled = QCheckBox('启用全局快捷键')
        self.hotkey_enabled.setChecked(self.settings['hotkey_enabled'])
        settings_form.addRow(self.hotkey_enabled)
        self.key_edit = QKeySequenceEdit(QKeySequence(self.settings['hotkey']))
        self.key_edit.setMaximumSequenceLength(1)
        self.capture_widgets = [self.key_edit] + self.key_edit.findChildren(QWidget)
        for widget in self.capture_widgets:
            widget.installEventFilter(self)
        settings_form.addRow('转换快捷键', self.key_edit)
        self.auto_paste = QCheckBox('转换完成后自动粘贴到当前 WPS 文档')
        self.auto_paste.setChecked(self.settings['auto_paste'])
        settings_form.addRow(self.auto_paste)
        self.no_app_action = QComboBox()
        self.no_app_action.addItem('留在剪贴板，手动粘贴', 'clipboard')
        self.no_app_action.addItem('仍粘贴到当前窗口（按文档流）', 'convert_anyway')
        self.no_app_action.addItem('每次询问', 'ask')
        index = self.no_app_action.findData(self.settings.get('no_app_action', 'clipboard'))
        self.no_app_action.setCurrentIndex(max(0, index))
        settings_form.addRow('窗口未匹配规则时', self.no_app_action)
        self.delay = QSpinBox()
        self.delay.setRange(100, 2000)
        self.delay.setSingleStep(50)
        self.delay.setSuffix(' 毫秒')
        self.delay.setValue(self.settings['paste_delay_ms'])
        settings_form.addRow('粘贴前等待', self.delay)
        self.notifications = QCheckBox('显示完成和错误通知')
        self.notifications.setChecked(self.settings['notifications'])
        settings_form.addRow(self.notifications)
        self.autostart = QCheckBox('登录后启动并驻留托盘')
        self.autostart.setChecked(autostart_path().exists())
        settings_form.addRow(self.autostart)
        controls.addLayout(settings_form)
        enhance_form = QFormLayout()
        self.excel_enabled = QCheckBox('WPS 表格窗口自动改用表格粘贴（实验）')
        self.excel_enabled.setChecked(self.settings['enable_excel'])
        enhance_form.addRow(self.excel_enabled)
        self.keep_formula = QCheckBox('保留 LaTeX 原始公式（以 $…$ 文本插入）')
        self.keep_formula.setChecked(self.settings['keep_original_formula'])
        enhance_form.addRow(self.keep_formula)
        self.latex_fix = QCheckBox('修复 LaTeX 语法（如 \\kern 间距替换）')
        self.latex_fix.setChecked(self.settings['enable_latex_replacements'])
        enhance_form.addRow(self.latex_fix)
        self.dollar_fix = QCheckBox('修复单 $ 行构成的块级公式')
        self.dollar_fix.setChecked(self.settings['fix_single_dollar_block'])
        enhance_form.addRow(self.dollar_fix)
        self.hard_breaks = QCheckBox('Markdown 内单个换行视为硬换行')
        self.hard_breaks.setChecked(self.settings['markdown_hard_line_breaks'])
        enhance_form.addRow(self.hard_breaks)
        indent_row = QHBoxLayout()
        self.md_indent = QCheckBox('Markdown')
        self.md_indent.setChecked(self.settings['md_disable_first_para_indent'])
        self.html_indent = QCheckBox('网页 HTML')
        self.html_indent.setChecked(self.settings['html_disable_first_para_indent'])
        indent_row.addWidget(self.md_indent)
        indent_row.addWidget(self.html_indent)
        indent_row.addStretch()
        enhance_form.addRow('禁用首段缩进样式', indent_row)
        self.rule_style = QComboBox()
        for text, value in [('Pandoc 原生横线', 'default'), ('段落边框线', 'paragraph_border')]:
            self.rule_style.addItem(text, value)
        self.rule_style.setCurrentIndex(self.rule_style.findData(self.settings['horizontal_rule_style']))
        enhance_form.addRow('水平线样式', self.rule_style)
        self.auto_tables = QCheckBox('表格按内容自动调整列宽（实验）')
        self.auto_tables.setChecked(self.settings['docx_auto_table_layout'])
        enhance_form.addRow(self.auto_tables)
        self.highlight_style = QComboBox()
        for text, value in [('Tango（默认）', 'default'), ('Pygments', 'pygments'),
                            ('Kate', 'kate'), ('Espresso', 'espresso'),
                            ('Zenburn', 'zenburn'), ('单色', 'monochrome'),
                            ('关闭高亮', 'none')]:
            self.highlight_style.addItem(text, value)
        self.highlight_style.setCurrentIndex(
            self.highlight_style.findData(self.settings.get('code_highlight_style', 'default')))
        enhance_form.addRow('代码块高亮配色', self.highlight_style)
        formatting = self.settings['html_formatting']
        self.font_semantic = QCheckBox('恢复样式表中的加粗/斜体（WPS 表格、网页复制）')
        self.font_semantic.setChecked(formatting.get('css_font_to_semantic', True))
        enhance_form.addRow(self.font_semantic)
        self.bold_header = QCheckBox('表格首行全加粗时提升为表头（实验）')
        self.bold_header.setChecked(formatting.get('bold_first_row_to_header', False))
        enhance_form.addRow(self.bold_header)
        self.prewrap_newlines = QCheckBox('保留 pre-wrap 块的换行（聊天记录、代码块复制）')
        self.prewrap_newlines.setChecked(formatting.get('preserve_prewrap_newlines', True))
        enhance_form.addRow(self.prewrap_newlines)
        reference_row = QHBoxLayout()
        self.reference_edit = QLineEdit(str(self.settings['reference_docx'] or ''))
        self.reference_edit.setPlaceholderText('Pandoc 参考文档模板（.docx），留空使用默认样式')
        browse = QPushButton('选择…')
        browse.setMaximumWidth(72)
        browse.clicked.connect(self._pick_reference)
        clear_ref = QPushButton('清除')
        clear_ref.setMaximumWidth(60)
        clear_ref.clicked.connect(lambda: self.reference_edit.clear())
        reference_row.addWidget(self.reference_edit, 1)
        reference_row.addWidget(browse)
        reference_row.addWidget(clear_ref)
        enhance_form.addRow('样式模板', reference_row)
        self.filters_edit = QPlainTextEdit()
        self.filters_edit.setPlaceholderText('每行一个 Pandoc 过滤器路径（.lua 或可执行文件）')
        self.filters_edit.setPlainText('\n'.join(self.settings['pandoc_filters']))
        self.filters_edit.setMaximumHeight(64)
        enhance_form.addRow('自定义过滤器（全部流程）', self.filters_edit)
        by_conversion = self.settings.get('pandoc_filters_by_conversion') or {}
        self.filters_by_conversion_edits = {}
        conversion_labels = (
            ('md_to_docx', 'MD → DOCX'), ('html_to_docx', 'HTML → DOCX'),
            ('md_to_md', 'MD → Markdown'), ('html_to_md', 'HTML → Markdown'),
            ('md_to_latex', 'MD → LaTeX'), ('html_to_latex', 'HTML → LaTeX'),
            ('md_to_html', 'MD → HTML'), ('html_to_html', 'HTML → HTML'),
        )
        for key, label in conversion_labels:
            edit = QPlainTextEdit()
            edit.setPlaceholderText(f'{label}：每行一个过滤器路径（留空不启用）')
            edit.setPlainText('\n'.join(by_conversion.get(key) or []))
            edit.setMaximumHeight(44)
            enhance_form.addRow(f'过滤器（{label}）', edit)
            self.filters_by_conversion_edits[key] = edit
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setPlaceholderText('每行一条，例如 User-Agent: Mozilla/5.0 …（抓取远程图片时使用）')
        self.headers_edit.setPlainText('\n'.join(self.settings['pandoc_request_headers']))
        self.headers_edit.setMaximumHeight(64)
        enhance_form.addRow('请求头', self.headers_edit)
        file_form = QFormLayout()
        self.keep_file = QCheckBox('保留生成的 DOCX 文件（默认粘贴后即弃，仅存于缓存）')
        self.keep_file.setChecked(bool(self.settings.get('keep_file')))
        file_form.addRow(self.keep_file)
        save_row = QHBoxLayout()
        self.save_dir_edit = QLineEdit(str(self.settings.get('save_dir') or ''))
        self.save_dir_edit.setPlaceholderText('保存目录（留空使用 ~/.cache/pastemd）')
        browse_save = QPushButton('选择…')
        browse_save.setMaximumWidth(72)
        browse_save.clicked.connect(self._pick_save_dir)
        save_row.addWidget(self.save_dir_edit, 1)
        save_row.addWidget(browse_save)
        file_form.addRow('保存目录', save_row)
        controls.addLayout(file_form)
        controls.addLayout(enhance_form)
        rules_form = QFormLayout()
        self.workflow_edits = {}
        for key, label in (('md', '粘贴 Markdown 文本'),
                           ('latex', '粘贴 LaTeX 文本'),
                           ('html', '粘贴 HTML 文本')):
            saved = (self.settings.get('extensible_workflows') or {}).get(key, {})
            enabled = QCheckBox('启用')
            enabled.setChecked(bool(saved.get('enabled', True)))
            edit = QPlainTextEdit()
            edit.setPlaceholderText('每行一条：显示名 | WM_CLASS | 窗口标题正则')
            edit.setPlainText(apprules.format_rules(saved.get('apps', [])))
            edit.setMaximumHeight(56)
            row = QHBoxLayout()
            row.addWidget(enabled)
            pick = QPushButton('从窗口拾取…')
            pick.clicked.connect(lambda _=False, flow_key=key: self._pick_window_rule(flow_key))
            row.addWidget(pick)
            row.addWidget(edit, 1)
            rules_form.addRow(label, row)
            self.workflow_edits[key] = (enabled, edit)
        rules_note = QLabel('命中的窗口自动改粘贴文本格式，例如：语雀 | yuque | 语雀。'
                            'WM_CLASS 用 token 精确匹配；标题正则可留空。')
        rules_note.setWordWrap(True)
        rules_form.addRow(rules_note)
        controls.addLayout(rules_form)
        note = QLabel('自动粘贴仅在原 WPS 窗口仍有焦点、快捷键已经松开时执行。切换到其他应用后，内容会留在剪贴板供手动粘贴。')
        note.setWordWrap(True)
        controls.addWidget(note)
        self.hotkey_status = QLabel('快捷键尚未启用')
        self.hotkey_status.setWordWrap(True)
        controls.addWidget(self.hotkey_status)
        save = QPushButton('保存设置')
        save.clicked.connect(self.save)
        controls.addWidget(save)
        launcher = QPushButton('添加到应用菜单')
        launcher.clicked.connect(self._install_launcher)
        controls.addWidget(launcher)
        about = QPushButton('关于、原作者与许可证')
        about.clicked.connect(self._show_about)
        controls.addWidget(about)
        controls.addStretch()
        self.tabs.addTab(settings_tab, '设置')
        footer = QLabel('源自 RICHQAQ/PasteMD · GNU AGPL-3.0    |    关闭窗口后继续驻留托盘')
        footer.setWordWrap(True)
        layout.addWidget(footer)
        self.setCentralWidget(root)
        self._update_instructions()

    def _build_tray(self):
        if self.smoke or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('PasteMD Linux')
        menu = QMenu()
        show = menu.addAction('打开 PasteMD')
        show.triggered.connect(self.show_window)
        prepare = menu.addAction('转换到剪贴板')
        prepare.triggered.connect(lambda: self.convert())
        demo = menu.addAction('测试公式')
        demo.triggered.connect(lambda: self.convert(demo=True))
        open_save = menu.addAction('打开 DOCX 保存目录')
        open_save.triggered.connect(self._open_save_dir)
        self.tray_rule_action = menu.addAction('为此窗口建规则…')
        self.tray_rule_action.triggered.connect(self._tray_add_rule)
        self.tray_rule_action.setVisible(False)
        menu.aboutToShow.connect(self._refresh_tray_rule_action)
        menu.addSeparator()
        about = menu.addAction('关于与许可证')
        about.triggered.connect(self._show_about)
        menu.addSeparator()
        quit_action = menu.addAction('退出')
        quit_action.triggered.connect(self.request_quit)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _refresh_tray_rule_action(self):
        """托盘菜单打开时探测焦点窗口；WPS 内建窗口之外的都给建规则入口。"""
        if not getattr(self, 'tray_rule_action', None):
            return
        try:
            app = self.focused_app()
        except Exception:
            app = None
        if app and len(app) > 1 and app[2] not in ('writer', 'spreadsheet', 'presentation'):
            caption = str(app[1]).strip() or str(app[3] or '当前窗口')
            self.tray_rule_action.setText(f'为「{caption[:32]}」建规则…')
            self._tray_app = app
            self.tray_rule_action.setVisible(True)
        else:
            self.tray_rule_action.setVisible(False)

    def _tray_add_rule(self):
        app = getattr(self, '_tray_app', None)
        if app:
            self._add_rule_for_window(app)

    def _update_instructions(self):
        key = self.settings['hotkey']
        self.instructions.setText(f'① 复制 Markdown 或网页内容\n② 回到 WPS 的 .docx 文档\n③ 按 {key} 转换并粘贴')

    def _refresh_dependencies(self):
        pandoc = cli.pandoc_bin()
        missing = [name for name in (pandoc, 'wl-paste') if not shutil.which(name)]
        if importlib.util.find_spec('PySide6') is None:
            missing.append('python3-pyside6')
        self.dependencies.setText(
            '依赖已就绪'
            if not missing
            else '缺少依赖：' + '、'.join(missing)
            + '。请运行 sudo dnf install pandoc wl-clipboard python3-pyside6。'
        )

    def _start_services(self):
        try:
            self.x11 = X11Paste()
        except Exception as error:
            self.report(str(error))
        try:
            self.kwin = KWinFocus(classify=X11Paste.classify)
            if self.kwin.available:
                self.report('焦点检测：KWin（Plasma Wayland）')
        except Exception as error:
            self.kwin = None
            self.report('KWin 焦点查询不可用：' + str(error))
        try:
            self.hotkey = KdeHotkey(self)
            self.hotkey.activated.connect(lambda: self.convert(paste=True))
            if self.settings['hotkey_enabled']:
                self.hotkey.bind(self.settings['hotkey'])
                self.hotkey_status.setText('全局快捷键已启用：' + self.settings['hotkey'])
                self.report('已启用热键：' + self.settings['hotkey'])
        except Exception as error:
            self.hotkey_status.setText(str(error))
            self.report('热键未启用：' + str(error))
        if self.startup_error:
            self.report(self.startup_error)

    def eventFilter(self, watched, event):
        if watched in getattr(self, 'capture_widgets', []):
            if event.type() == QEvent.Type.FocusIn and self.hotkey and self.hotkey.bound:
                try:
                    self.hotkey.unbind()
                except Exception as error:
                    self.report('暂停热键失败：' + str(error))
            elif event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, self._resume_after_recording)
        return super().eventFilter(watched, event)

    def _resume_after_recording(self):
        if self.pending_quit or self.closing or QApplication.focusWidget() in self.capture_widgets or not self.settings['hotkey_enabled'] or self.smoke:
            return
        if self.hotkey and not self.hotkey.bound:
            try:
                self.hotkey.bind(self.settings['hotkey'])
            except Exception as error:
                self.report('热键恢复失败：' + str(error))

    def save(self):
        # 先以当前生效设置为底，避免遗漏未在界面出现的键。
        new = dict(self.settings)
        new.update({
            'hotkey': self.key_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            'hotkey_enabled': self.hotkey_enabled.isChecked(), 'auto_paste': self.auto_paste.isChecked(),
            'input_format': self.input_format.currentData(), 'paste_delay_ms': self.delay.value(),
            'notifications': self.notifications.isChecked(),
            'no_app_action': self.no_app_action.currentData(),
            'code_highlight_style': self.highlight_style.currentData(),
            'enable_excel': self.excel_enabled.isChecked(),
            'keep_original_formula': self.keep_formula.isChecked(),
            'enable_latex_replacements': self.latex_fix.isChecked(),
            'fix_single_dollar_block': self.dollar_fix.isChecked(),
            'markdown_hard_line_breaks': self.hard_breaks.isChecked(),
            'md_disable_first_para_indent': self.md_indent.isChecked(),
            'html_disable_first_para_indent': self.html_indent.isChecked(),
            'horizontal_rule_style': self.rule_style.currentData(),
            'docx_auto_table_layout': self.auto_tables.isChecked(),
            'html_formatting': {'css_font_to_semantic': self.font_semantic.isChecked(),
                                'bold_first_row_to_header': self.bold_header.isChecked(),
                                'preserve_prewrap_newlines': self.prewrap_newlines.isChecked()},
            'reference_docx': self.reference_edit.text().strip() or None,
            'keep_file': self.keep_file.isChecked(),
            'save_dir': self.save_dir_edit.text().strip() or None,
            'pandoc_filters': [line.strip() for line in self.filters_edit.toPlainText().splitlines()
                               if line.strip()],
            'pandoc_filters_by_conversion': {
                key: [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]
                for key, edit in self.filters_by_conversion_edits.items()
                if edit.toPlainText().strip()},
            'pandoc_request_headers': [line.strip() for line in self.headers_edit.toPlainText().splitlines()
                                       if line.strip()],
            'extensible_workflows': {
                key: {'enabled': enabled.isChecked(),
                      'apps': apprules.parse_rules(edit.toPlainText())}
                for key, (enabled, edit) in self.workflow_edits.items()}})
        old = dict(self.settings)
        old_autostart = autostart_path().exists()
        try:
            if new['hotkey_enabled']:
                KdeHotkey.key_value(new['hotkey'])
                if not self.smoke:
                    if self.hotkey is None:
                        self.hotkey = KdeHotkey(self)
                        self.hotkey.activated.connect(lambda: self.convert(paste=True))
                    self.hotkey.bind(new['hotkey'])
            elif self.hotkey:
                self.hotkey.unbind()
            save_settings(new)
            set_autostart(self.autostart.isChecked())
        except Exception as error:
            # Restore runtime and on-disk state if either settings file failed.
            if self.hotkey:
                try:
                    if old['hotkey_enabled']:
                        self.hotkey.bind(old['hotkey'])
                    else:
                        self.hotkey.unbind()
                except Exception:
                    pass
            try:
                save_settings(old)
                set_autostart(old_autostart)
            except Exception:
                pass
            self.report('设置未完整保存：' + str(error), notify=True)
            return
        self.settings = new
        self._update_instructions()
        self.hotkey_status.setText('全局快捷键：' + new['hotkey'] if new['hotkey_enabled'] else '全局快捷键已关闭')
        self.report('设置已保存')

    def report(self, text, notify=False):
        self.status.setText(text if len(text) <= 26 else text[:25] + '…')
        self.log.appendPlainText(time.strftime('%H:%M:%S  ') + text)
        if notify and self.settings['notifications'] and self.tray:
            self.tray.showMessage('PasteMD Linux', text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def focused_app(self):
        """Plasma Wayland 下 X 焦点只见代理窗口，优先问 KWin，再退回 X11。"""
        if self.kwin is not None and self.kwin.available:
            app = self.kwin.focused_app()
            if app:
                return app
        return self.x11.focused_app() if self.x11 else None

    def convert(self, paste=False, demo=False, open_docx=False):
        if self.pending_quit or self.closing:
            return
        # 防抖（对齐上游 FIRE_DEBOUNCE_SEC）：转换刚结束的短时间内连按热键不再触发
        now = time.monotonic()
        if now - self._last_convert < 0.5:
            return
        self._last_convert = now
        if self.worker or self.paste_pending:
            self.report('正在处理，请稍候。')
            return
        self.target = None
        self.flow = 'doc'
        paste_now = paste and self.settings['auto_paste']
        app = self.focused_app() if paste_now else None
        if paste_now and app is None:
            self.report('未找到获得焦点的 WPS 窗口，内容将留在剪贴板供手动粘贴。')
        if app:
            self.target = app
            kind = app[2]
            if kind == 'spreadsheet' and self.settings.get('enable_excel', True):
                self.flow = 'table'
            elif kind not in ('writer', 'spreadsheet', 'presentation'):
                # 非 WPS 窗口：按应用扩展规则决定是否改粘贴文本格式
                workflows = self.settings.get('extensible_workflows') or {}
                ext = apprules.active_flow(workflows, app[3] if len(app) > 3 else '',
                                           app[1] if len(app) > 1 else '')
                if ext:
                    self.flow = ext
                else:
                    action = self.settings.get('no_app_action', 'clipboard')
                    if action == 'ask':
                        choice = self._ask_no_app_action(app)
                        if choice == 'rule':
                            self.target = None
                            self.want_paste = False
                            self._add_rule_for_window(app)
                            return
                        action = choice or 'clipboard'
                    if action == 'convert_anyway':
                        # 保留 target 以自动粘贴，但未匹配窗口恒按文档流转换
                        self.flow = 'doc'
                        self.report('当前窗口未匹配规则，将按文档流粘贴。'
                                    '可在设置中修改该行为。')
                    else:
                        self.target = None
                        self.report('当前窗口没有匹配的粘贴规则，内容将留在剪贴板供手动粘贴。')
        self.want_paste = paste_now and self.target is not None
        self.worker = ConversionWorker(self.input_format.currentData(), demo, open_docx,
                                       options=dict(self.settings), flow=self.flow, parent=self)
        self.worker.completed.connect(self._converted)
        self.worker.failed.connect(lambda text: self.report('转换失败：' + text, notify=True))
        self.worker.finished.connect(self._worker_finished)
        self._set_busy(True)
        self.report('正在转换…')
        self.worker.start()

    def _set_busy(self, busy):
        for button in (self.convert_button, self.demo_button, self.docx_button):
            button.setEnabled(not busy)

    def _worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        if not self.paste_pending:
            self._set_busy(False)
        if self.pending_quit:
            self.request_quit()

    def _ask_no_app_action(self, app):
        """未匹配规则时的每次询问对话框；返回 'clipboard'/'convert_anyway'/'rule'/None。"""
        caption = app[1] if len(app) > 1 else ''
        box = QMessageBox(self)
        box.setWindowTitle('PasteMD Linux')
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f'窗口「{caption}」没有匹配的粘贴规则。')
        paste_button = box.addButton('仍粘贴（按文档流）', QMessageBox.ButtonRole.AcceptRole)
        clip_button = box.addButton('留在剪贴板', QMessageBox.ButtonRole.RejectRole)
        rule_button = box.addButton('为此窗口建规则…', QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(clip_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is rule_button:
            return 'rule'
        if clicked is paste_button:
            return 'convert_anyway'
        if clicked is clip_button:
            return 'clipboard'
        return None

    def _add_rule_for_window(self, app):
        """把当前窗口写进 md 流程的规则编辑框并打开设置页让用户确认流程。"""
        caption = str(app[1] if len(app) > 1 else '').replace('|', '/').strip()
        resource_class = str(app[3] if len(app) > 3 else '').replace('|', '/').strip()
        if not resource_class:
            self.report('无法识别窗口的 WM_CLASS，请改用设置页的「从窗口拾取…」。', notify=True)
            return
        name = caption or resource_class
        if self.workflow_edits:
            enabled, edit = next(iter(self.workflow_edits.values()))
            line = f'{name} | {resource_class} | '
            current = edit.toPlainText().rstrip('\n')
            edit.setPlainText((current + '\n' if current else '') + line)
        self.show_window()
        self.report(f'已把「{name}」加入规则（md 流程），请在设置页调整归属或标题正则后保存。',
                    notify=True)

    def _converted(self, result):
        if self.pending_quit or self.closing:
            return
        self.lost_images = result.get('lost_images') or 0
        lost_note = f'注意：{self.lost_images} 张图片未能嵌入。' if self.lost_images else ''
        if 'path' in result:
            try:
                cli.open_in_wps(result['path'])
                message = 'DOCX 已保存并交给 WPS 打开：' + result['path']
            except (OSError, RuntimeError) as error:
                message = str(error) + ' ' + result['path']
            self.report(message + lost_note, notify=True)
            return
        self.clipboard_token = result.get('token')
        keep_note = ''
        if result.get('keep_path'):
            keep_note = ' 已保留文件：' + result['keep_path']
        if self.want_paste and self.target:
            self.paste_pending = True
            self.paste_ready_at = time.monotonic() + self.settings['paste_delay_ms'] / 1000
            self.paste_deadline = self.paste_ready_at + 3
            self.paste_timer.start()
        elif 'text_flow' in result:
            self.report('已按' + cli.text_clipboard_label(result['text_flow'])
                        + '文本写入剪贴板，在目标应用中按 Ctrl+V。', notify=True)
        elif 'rows' in result:
            self.report(f"表格已就绪（{result['rows']} 行），请在 WPS 表格中按 Ctrl+V。", notify=True)
        else:
            self.report('转换完成，请在 WPS 的 .docx 文档中按 Ctrl+V。' + lost_note + keep_note, notify=True)

    def _try_paste(self):
        if time.monotonic() < self.paste_ready_at:
            return
        try:
            if self.focused_app() != self.target:
                raise RuntimeError('焦点已变化，内容已就绪，请在 WPS 中手动 Ctrl+V。')
            if self.x11.modifiers_held():
                if time.monotonic() < self.paste_deadline:
                    return
                raise RuntimeError('快捷键未松开，内容已就绪，请手动 Ctrl+V。')
            mime = QApplication.clipboard().mimeData()
            token_data = mime.data(CLIPBOARD_TOKEN_MIME) if mime is not None else None
            if not self.clipboard_token or token_data is None or bytes(token_data) != self.clipboard_token:
                raise RuntimeError('剪贴板已变化，已取消自动粘贴，请重新转换需要的内容。')
            self.x11.paste(self.target)
            if self.flow == 'table':
                self.report('已向 WPS 表格发送粘贴。', notify=True)
            elif self.flow in cli.TEXT_FORMAT_LABELS:
                self.report('已向目标应用发送' + cli.text_clipboard_label(self.flow)
                            + '文本粘贴。', notify=True)
            else:
                lost_note = f'注意：{self.lost_images} 张图片未能嵌入。' if self.lost_images else ''
                self.report('已向 WPS 发送粘贴，请使用 .docx 格式保留公式。' + lost_note, notify=True)
        except Exception as error:
            self.report(str(error), notify=True)
        self.paste_timer.stop()
        self.paste_pending = False
        self._set_busy(False)

    def _pick_window_rule(self, workflow_key):
        """列出运行中的窗口/常用预设，生成 `显示名 | class |` 规则行。"""
        candidates = []
        if self.kwin and self.kwin.available:
            try:
                candidates = self.kwin.list_windows()
            except Exception:
                candidates = []
        if not candidates and self.x11:
            candidates = self.x11.list_windows()
        seen, windows = set(), []
        for caption, resource_class in candidates:
            if (caption, resource_class) in seen:
                continue
            seen.add((caption, resource_class))
            windows.append((str(caption), str(resource_class)))
        dialog = QDialog(self)
        dialog.setWindowTitle('从运行中的窗口拾取规则')
        layout = QVBoxLayout(dialog)
        if windows:
            hint = QLabel('双击窗口行生成规则（默认按 class 匹配该应用的所有窗口）：')
            layout.addWidget(hint)
        else:
            layout.addWidget(QLabel('没有枚举到可用的窗口（需要 KDE Wayland 或 X11 会话）。'))
        window_list = QListWidget(dialog)
        for caption, resource_class in windows:
            item = QListWidgetItem(f'{caption}    [{resource_class}]')
            item.setData(Qt.UserRole, (caption, resource_class))
            window_list.addItem(item)
        layout.addWidget(window_list)
        layout.addWidget(QLabel('或添加常用应用预设（class 不确定时可拾取核对）：'))
        preset_row = QHBoxLayout()
        for name, resource_class in apprules.APP_PRESETS:
            button = QPushButton(name)
            button.clicked.connect(lambda _=False, picked=(name, resource_class):
                                   self._apply_window_pick(dialog, picked, workflow_key))
            preset_row.addWidget(button)
        layout.addLayout(preset_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def on_double_clicked(item):
            picked = item.data(Qt.UserRole)
            if picked:
                self._apply_window_pick(dialog, picked, workflow_key)
        window_list.itemDoubleClicked.connect(on_double_clicked)
        dialog.exec()

    def _apply_window_pick(self, dialog, picked, workflow_key):
        """把拾取结果写进对应流程的规则编辑框。"""
        name, resource_class = picked
        name = str(name).replace('|', '/').strip() or resource_class
        resource_class = str(resource_class).replace('|', '/').strip()
        if not resource_class:
            return
        dialog.chosen = (name, resource_class)
        dialog.accept()
        edit = self.workflow_edits[workflow_key][1]
        line = f'{name} | {resource_class} | '
        current = edit.toPlainText().rstrip('\n')
        edit.setPlainText((current + '\n' if current else '') + line)

    def _pick_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择参考文档模板', str(Path.home()),
                                              'DOCX 模板 (*.docx)')
        if path:
            self.reference_edit.setText(path)

    def _pick_save_dir(self):
        path = QFileDialog.getExistingDirectory(self, '选择 DOCX 保存目录',
                                                self.save_dir_edit.text().strip() or str(Path.home()))
        if path:
            self.save_dir_edit.setText(path)

    def _open_save_dir(self):
        save_dir = str(self.settings.get('save_dir') or '').strip()
        target = Path(save_dir).expanduser() if save_dir else \
            Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'pastemd'
        if not target.exists():
            self.report('目录不存在：' + str(target), notify=True)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _install_launcher(self):
        try:
            target = install_launcher()
            self.report('已添加到应用菜单：' + str(target))
        except OSError as error:
            self.report(str(error), notify=True)

    def _show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle('关于 PasteMD Linux')
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            '<h3>PasteMD Linux</h3>'
            '<p>本项目派生自 <a href="https://github.com/RICHQAQ/PasteMD">'
            'RICHQAQ/PasteMD</a>，原项目由 RICHQAQ 及历史贡献者开发。'
            'Linux 适配由 GMagisk9527 维护。</p>'
            '<p>软件按 GNU AGPL-3.0 发布，不提供任何担保；你可以按照许可证条款'
            '复制、修改和再发布。</p>'
        )
        license_button = box.addButton('查看许可证', QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is license_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / 'LICENSE')))

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_quit(self):
        if self.worker:
            self.pending_quit = True
            self.report('当前转换结束后退出。')
            return
        self.paste_timer.stop()
        self.closing = True
        if self.hotkey:
            try:
                self.hotkey.close()
            except Exception:
                pass
        if self.x11:
            self.x11.close()
        if self.kwin:
            try:
                self.kwin.close()
            except Exception:
                self.kwin = None
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.tray and not self.closing:
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.request_quit()


def start_window(window, args):
    # A cold --trigger must leave the foreground WPS window untouched.
    if args.smoke_test or (not args.trigger and (not args.minimized or not window.tray)):
        window.show()
    if args.trigger:
        QTimer.singleShot(500, lambda: window.convert(paste=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description='PasteMD Linux 图形界面')
    parser.add_argument('--minimized', action='store_true', help='启动后驻留托盘')
    parser.add_argument('--trigger', action='store_true', help='通知已运行实例转换并粘贴')
    parser.add_argument('--install', action='store_true', help='添加应用菜单入口并退出')
    parser.add_argument('--serve-clipboard', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--package-self-test', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--smoke-test', metavar='PNG', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.serve_clipboard:
        return cli.serve_clipboard()
    if args.package_self_test:
        pandoc = cli.pandoc_bin()
        missing = [name for name in (pandoc, 'wl-paste') if not shutil.which(name)]
        if missing:
            print('缺少打包组件：' + '、'.join(missing), file=sys.stderr)
            return 1
        document = cli.prepare_document(
            cli.DEMO_MARKDOWN.encode('utf-8'), 'markdown' + cli.MATH_EXTENSIONS)
        plain = cli.run([cli.pandoc_bin(), '--from', 'json', '--to', 'plain'], document)
        payload = cli.native_clipboard_payload(document, plain)
        print('PACKAGE-SELF-TEST OK: native DOCX bytes=' +
              str(len(payload['Kingsoft WPS 9.0 Format'])))
        return 0
    if args.install:
        print(install_launcher())
        return 0
    # Match WPS's XWayland environment, while KDE manages shortcuts on Wayland.
    os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
    app = QApplication(['PasteMD Linux'])
    app.setApplicationName('pastemd-linux')
    app.setDesktopFileName('pastemd-linux')
    app.setQuitOnLastWindowClosed(False)
    lock = server = None
    if not args.smoke_test:
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', tempfile.gettempdir()))
        socket_name = str(runtime / f'pastemd-linux-{os.getuid()}.sock')
        lock = QLockFile(socket_name + '.lock')
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            socket = QLocalSocket()
            socket.connectToServer(socket_name)
            if socket.waitForConnected(1500):
                socket.write(b'trigger\n' if args.trigger else b'show\n')
                socket.waitForBytesWritten(1500)
                socket.disconnectFromServer()
                return 0
            print('PasteMD 已运行，但无法连接其窗口。', file=sys.stderr)
            return 1
        QLocalServer.removeServer(socket_name)
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(socket_name):
            print(server.errorString(), file=sys.stderr)
            return 1
    window = MainWindow(smoke=bool(args.smoke_test))
    clients = []
    if server:
        def accept():
            socket = server.nextPendingConnection()
            clients.append(socket)
            def receive():
                if not socket.canReadLine():
                    return
                command = bytes(socket.readLine()).strip()
                if command == b'trigger':
                    window.convert(paste=True)
                elif command == b'show':
                    window.show_window()
                socket.disconnectFromServer()
            def discard():
                if socket in clients:
                    clients.remove(socket)
                socket.deleteLater()
            socket.readyRead.connect(receive)
            socket.disconnected.connect(discard)
            receive()
        server.newConnection.connect(accept)
    start_window(window, args)
    if args.smoke_test:
        def capture():
            window.grab().save(args.smoke_test)
            app.quit()
        QTimer.singleShot(300, capture)
    result = app.exec()
    if server:
        server.close()
    if lock:
        lock.unlock()
    return result
