"""KWin 焦点查询：Plasma Wayland 下识别激活窗口的唯一可靠途径。

XWayland 把 X 焦点指向 1×1 代理窗口，XGetInputFocus / _NET_ACTIVE_WINDOW
都拿不到真实应用窗口。这里通过 KWin 脚本接口查询 workspace 的激活窗口，
再用 callDBus（注意：接口名必须为空字符串，Qt 导出的无接口方法才会接收）
把结果回传给本进程注册的 DBus 对象。
"""
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QEventLoop, QTimer, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

SERVICE = 'io.github.GMagisk9527.PasteMDLinux'

# 只回传激活窗口；空接口名是 Qt ExportAllSlots 对象的接收条件。
KWIN_SCRIPT = """\
const active = workspace.windowList().find(w => w.active);
callDBus('%s', '/', '', 'Report',
         active ? active.caption : '',
         active ? String(active.resourceClass) : '');
""" % SERVICE


class _Reporter(QObject):
    """接收 KWin 脚本回传的激活窗口信息。"""

    def __init__(self):
        super().__init__()
        self.caption = ''
        self.resource_class = ''
        self.arrived = False

    @Slot(str, str)
    def Report(self, caption, resource_class):
        self.caption = caption
        self.resource_class = resource_class
        self.arrived = True


class KWinFocus:
    """查询 Plasma 激活窗口；非 KDE 会话自动降级为不可用。"""

    def __init__(self, classify, timeout_ms=400):
        self._classify = classify
        self._timeout_ms = timeout_ms
        self._script_id = None
        self._interface = None
        self._receiver = _Reporter()
        self._conn = QDBusConnection.sessionBus()
        self._name = SERVICE
        if self._conn is None or not self._conn.isConnected():
            return
        if not self._conn.registerService(self._name):
            # GUI 与 CLI 并存时换一个名字，互不影响。
            self._name = f'{SERVICE}.{id(self):x}'
            if not self._conn.registerService(self._name):
                return
        if not self._conn.registerObject('/', self._receiver, QDBusConnection.ExportAllSlots):
            return
        self._setup()

    def _setup(self):
        kwin = QDBusInterface('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting',
                              self._conn)
        if not kwin.isValid():
            return
        path = Path(tempfile.gettempdir()) / f'pastemd-active-window-{id(self):x}.js'
        try:
            path.write_text(KWIN_SCRIPT, encoding='utf-8')
        except OSError:
            return
        reply = kwin.call('loadScript', str(path), f'pastemd{id(self):x}')
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return
        self._script_id = reply.arguments()[0]
        self._interface = kwin

    @property
    def available(self):
        return self._script_id is not None

    def active_window(self):
        """(caption, resource_class) of the active window, or None."""
        if not self.available or self._interface is None:
            return None
        self._receiver.arrived = False
        self._receiver.caption = ''
        self._receiver.resource_class = ''
        path = f'/Scripting/Script{self._script_id}'
        runner = QDBusInterface('org.kde.KWin', path, 'org.kde.kwin.Script', self._conn)
        if not runner.isValid():
            return None
        runner.call('run')
        # 等回调到达：分步开事件循环处理 DBus 消息，总时长受 timeout 约束。
        waited = 0
        step = 20
        while not self._receiver.arrived and waited < self._timeout_ms:
            loop = QEventLoop()
            QTimer.singleShot(step, loop.quit)
            loop.exec()
            waited += step
        if not self._receiver.arrived:
            return None
        return self._receiver.caption, self._receiver.resource_class

    def focused_app(self):
        """(token, caption, kind)——与 X11Paste.focused_app 同构，kind 为 None 时整体 None。"""
        info = self.active_window()
        if not info:
            return None
        caption, resource_class = info
        kind = self._classify([resource_class.lower()], caption)
        if not kind:
            return None
        return f'kwin:{self._name}', caption, kind

    def close(self):
        if self._script_id is not None and self._interface is not None:
            path = f'/Scripting/Script{self._script_id}'
            runner = QDBusInterface('org.kde.KWin', path, 'org.kde.kwin.Script', self._conn)
            if runner.isValid():
                runner.call('stop')
            self._interface.call('unloadScript', f'pastemd{id(self):x}')
            self._script_id = None
        if self._conn is not None and self._conn.isConnected():
            self._conn.unregisterObject('/')
            self._conn.unregisterService(self._name)
