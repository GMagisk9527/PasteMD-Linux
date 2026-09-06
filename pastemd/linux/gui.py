"""Native Qt desktop UI for Fedora KDE/Wayland."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import (QEvent, QLockFile, QTimer, Qt, QThread, Signal, QUrl)
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
    QHBoxLayout, QLabel, QKeySequenceEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget, QMenu)

from . import cli
from .hotkey import KdeHotkey
from .settings import (ROOT, DEFAULTS, load_settings, save_settings, config_file,
                       autostart_path, set_autostart, install_launcher)
from .x11 import X11Paste


class ConversionWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, input_format, demo=False, open_docx=False, parent=None):
        super().__init__(parent)
        self.input_format, self.demo, self.open_docx = input_format, demo, open_docx

    def run(self):
        try:
            if self.demo:
                source, reader = cli.DEMO_MARKDOWN.encode(), 'markdown' + cli.MATH_EXTENSIONS
            else:
                source, reader = cli.read_clipboard(self.input_format)
            if not source.strip():
                raise RuntimeError('剪贴板内容为空，请先复制 Markdown 或网页正文。')
            document = cli.prepare_document(source, reader)
            if self.open_docx:
                cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'pastemd'
                cache.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix='paste-', suffix='.docx', dir=cache)
                os.close(fd)
                try:
                    cli.run(['pandoc', '-f', 'json', '-t', 'docx', '-o', name], document)
                except Exception:
                    Path(name).unlink(missing_ok=True)
                    raise
                self.completed.emit({'path': name})
            else:
                plain = cli.run(['pandoc', '-f', 'json', '-t', 'plain'], document)
                payload = cli.native_clipboard_payload(document, plain)
                cli.set_clipboard_payload(payload)
                self.completed.emit({'clipboard': True})
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self, smoke=False):
        super().__init__()
        self.smoke = smoke
        self.worker = None
        self.paste_pending = False
        self.pending_quit = False
        self.target = None
        self.paste_deadline = 0
        self.x11 = None
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
        controls.addStretch()
        self.tabs.addTab(settings_tab, '设置')
        footer = QLabel('Fedora · KDE Plasma · Wayland    |    关闭窗口后继续驻留托盘')
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
        menu.addSeparator()
        quit_action = menu.addAction('退出')
        quit_action.triggered.connect(self.request_quit)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _update_instructions(self):
        key = self.settings['hotkey']
        self.instructions.setText(f'① 复制 Markdown 或网页内容\n② 回到 WPS 的 .docx 文档\n③ 按 {key} 转换并粘贴')

    def _refresh_dependencies(self):
        missing = [name for name in ('pandoc', 'wl-paste') if not shutil.which(name)]
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
                self.hotkey.unbind()
            elif event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, self._resume_after_recording)
        return super().eventFilter(watched, event)

    def _resume_after_recording(self):
        if QApplication.focusWidget() in self.capture_widgets or not self.settings['hotkey_enabled'] or self.smoke:
            return
        if self.hotkey and not self.hotkey.bound:
            try:
                self.hotkey.bind(self.settings['hotkey'])
            except Exception as error:
                self.report('热键恢复失败：' + str(error))

    def save(self):
        new = {'hotkey': self.key_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
               'hotkey_enabled': self.hotkey_enabled.isChecked(), 'auto_paste': self.auto_paste.isChecked(),
               'input_format': self.input_format.currentData(), 'paste_delay_ms': self.delay.value(),
               'notifications': self.notifications.isChecked()}
        old = dict(self.settings)
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
            # Keep a working previous shortcut if saving the replacement failed.
            if self.hotkey and old['hotkey_enabled']:
                try:
                    self.hotkey.bind(old['hotkey'])
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

    def convert(self, paste=False, demo=False, open_docx=False):
        if self.worker or self.paste_pending:
            self.report('正在处理，请稍候。')
            return
        self.target = self.x11.focused_wps() if paste and self.settings['auto_paste'] and self.x11 else None
        self.want_paste = paste and self.settings['auto_paste']
        self.worker = ConversionWorker(self.input_format.currentData(), demo, open_docx, self)
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

    def _converted(self, result):
        if 'path' in result:
            if shutil.which('wps'):
                cli.subprocess.Popen(['wps', result['path']], stdout=cli.subprocess.DEVNULL, stderr=cli.subprocess.DEVNULL, start_new_session=True)
            self.report('DOCX 已保存：' + result['path'], notify=True)
            return
        if self.want_paste and self.target:
            self.paste_pending = True
            self.paste_ready_at = time.monotonic() + self.settings['paste_delay_ms'] / 1000
            self.paste_deadline = self.paste_ready_at + 3
            self.paste_timer.start()
        else:
            self.report('转换完成，请在 WPS 的 .docx 文档中按 Ctrl+V。', notify=True)

    def _try_paste(self):
        if time.monotonic() < self.paste_ready_at:
            return
        try:
            if self.x11.focused_wps() != self.target:
                raise RuntimeError('焦点已变化，内容已就绪，请在 WPS 中手动 Ctrl+V。')
            if self.x11.modifiers_held():
                if time.monotonic() < self.paste_deadline:
                    return
                raise RuntimeError('快捷键未松开，内容已就绪，请手动 Ctrl+V。')
            self.x11.paste(self.target)
            self.report('已向 WPS 发送粘贴，请使用 .docx 格式保留公式。', notify=True)
        except Exception as error:
            self.report(str(error), notify=True)
        self.paste_timer.stop()
        self.paste_pending = False
        self._set_busy(False)

    def _install_launcher(self):
        try:
            target = install_launcher()
            self.report('已添加到应用菜单：' + str(target))
        except OSError as error:
            self.report(str(error), notify=True)

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


def main(argv=None):
    parser = argparse.ArgumentParser(description='PasteMD Linux 图形界面')
    parser.add_argument('--minimized', action='store_true', help='启动后驻留托盘')
    parser.add_argument('--trigger', action='store_true', help='通知已运行实例转换并粘贴')
    parser.add_argument('--install', action='store_true', help='添加应用菜单入口并退出')
    parser.add_argument('--smoke-test', metavar='PNG', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
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
    if not args.minimized or not window.tray or args.smoke_test:
        window.show()
    if args.trigger:
        QTimer.singleShot(500, lambda: window.convert(paste=True))
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
