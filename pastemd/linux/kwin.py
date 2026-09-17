"""KWin 焦点查询：Plasma Wayland 下识别激活窗口的唯一可靠途径。

XWayland 把 X 焦点指向 1×1 代理窗口，XGetInputFocus / _NET_ACTIVE_WINDOW
都拿不到真实应用窗口。这里通过 KWin 脚本接口查询 workspace 的激活窗口，
再用 callDBus（注意：接口名必须为空字符串，Qt 导出的无接口方法才会接收）
把结果回传给本进程注册的 DBus 对象。
"""
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QEventLoop, QTimer, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

SERVICE = 'io.github.GMagisk9527.PasteMDLinux'

# 只回传激活窗口；空接口名是 Qt ExportAllSlots 对象的接收条件。
# 服务名用 %s 占位，写入脚本时替换为实例实际注册名（可能是回退名）。
KWIN_SCRIPT = """\
const active = workspace.windowList().find(w => w.active);
callDBus('%s', '/', '', 'Report',
         active ? active.caption : '',
         active ? String(active.resourceClass) : '');
"""

# 应用规则拾取器：回传全部受管窗口（过滤交给 Python 侧）。
KWIN_LIST_SCRIPT = """\
const lines = [];
for (const w of workspace.windowList()) {
    lines.push(JSON.stringify({c: w.caption, r: String(w.resourceClass)}));
}
callDBus('%s', '/', '', 'ReportWindows', lines.join('\\n'));
"""


class _Reporter(QObject):
    """接收 KWin 脚本回传的激活窗口信息。"""

    def __init__(self):
        super().__init__()
        self.caption = ''
        self.resource_class = ''
        self.arrived = False
        self.windows = []
        self.arrived_windows = False

    @Slot(str, str)
    def Report(self, caption, resource_class):
        self.caption = caption
        self.resource_class = resource_class
        self.arrived = True

    @Slot(str)
    def ReportWindows(self, text):
        self.windows = []
        for line in (text or '').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            caption = str(item.get('c') or '')
            resource_class = str(item.get('r') or '')
            if caption and resource_class:
                self.windows.append((caption, resource_class))
        self.arrived_windows = True


class KWinFocus:
    """查询 Plasma 激活窗口；非 KDE 会话自动降级为不可用。"""

    def __init__(self, classify, timeout_ms=400):
        self._classify = classify
        self._timeout_ms = timeout_ms
        self._path = None
        self._interface = None
        self._receiver = _Reporter()
        self._conn = QDBusConnection.sessionBus()
        self._name = SERVICE
        if self._conn is None or not self._conn.isConnected():
            return
        if not self._conn.registerService(self._name):
            # GUI 与 CLI 并存时换一个名字，互不影响。
            # 注意 DBus 名字组件不能以数字开头，需补字母前缀。
            self._name = f'{SERVICE}.inst{id(self):x}'
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
        self._path = Path(tempfile.gettempdir()) / f'pastemd-active-window-{id(self):x}.js'
        try:
            self._path.write_text(KWIN_SCRIPT % self._name, encoding='utf-8')
        except OSError:
            return
        self._interface = kwin

    @property
    def available(self):
        return self._interface is not None

    def active_window(self):
        """(caption, resource_class) of the active window, or None.

        每次查询都重新加载脚本再运行：同一个脚本实例的第二次 run()
        在 Plasma 6 上不可靠（实测会静默失败或返回 false）。
        """
        if not self.available or self._interface is None:
            return None
        name = f'pastemd{id(self):x}'
        reply = self._interface.call('loadScript', str(self._path), name)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return None
        script_id = reply.arguments()[0]
        runner = QDBusInterface('org.kde.KWin', f'/Scripting/Script{script_id}',
                                'org.kde.kwin.Script', self._conn)
        if not runner.isValid():
            self._interface.call('unloadScript', name)
            return None
        self._receiver.arrived = False
        self._receiver.caption = ''
        self._receiver.resource_class = ''
        runner.call('run')
        # 等回调到达：分步开事件循环处理 DBus 消息，总时长受 timeout 约束。
        waited = 0
        step = 20
        while not self._receiver.arrived and waited < self._timeout_ms:
            loop = QEventLoop()
            QTimer.singleShot(step, loop.quit)
            loop.exec()
            waited += step
        runner.call('stop')
        self._interface.call('unloadScript', name)
        if not self._receiver.arrived:
            return None
        return self._receiver.caption, self._receiver.resource_class

    def list_windows(self):
        """[(caption, resource_class)] 受管窗口列表（应用规则拾取器用）。"""
        if not self.available or self._interface is None:
            return []
        name = f'pastemdlist{id(self):x}'
        path = self._path.parent / f'pastemd-window-list-{id(self):x}.js'
        try:
            path.write_text(KWIN_LIST_SCRIPT % self._name, encoding='utf-8')
        except OSError:
            return []
        reply = self._interface.call('loadScript', str(path), name)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return []
        script_id = reply.arguments()[0]
        runner = QDBusInterface('org.kde.KWin', f'/Scripting/Script{script_id}',
                                'org.kde.kwin.Script', self._conn)
        if not runner.isValid():
            self._interface.call('unloadScript', name)
            return []
        self._receiver.arrived_windows = False
        self._receiver.windows = []
        runner.call('run')
        waited = 0
        step = 20
        while not self._receiver.arrived_windows and waited < self._timeout_ms * 5:
            loop = QEventLoop()
            QTimer.singleShot(step, loop.quit)
            loop.exec()
            waited += step
        runner.call('stop')
        self._interface.call('unloadScript', name)
        skip = {'plasmashell', 'org.kde.plasmashell', 'kwin_wayland', 'kwin_x11'}
        return [(caption, resource_class)
                for caption, resource_class in self._receiver.windows
                if resource_class.lower() not in skip]

    def focused_app(self):
        """(token, caption, kind, resource_class)——kind 可为 None（非 WPS 窗口）。"""
        info = self.active_window()
        if not info:
            return None
        caption, resource_class = info
        resource_class = (resource_class or '').lower()
        kind = self._classify([resource_class], caption)
        return f'kwin:{self._name}', caption, kind, resource_class

    def close(self):
        if self._conn is not None and self._conn.isConnected():
            self._conn.unregisterObject('/')
            self._conn.unregisterService(self._name)
        if self._interface is not None:
            # 兜底卸载同名的遗留脚本（正常流程在每次查询后已卸载）。
            self._interface.call('unloadScript', f'pastemd{id(self):x}')
            self._interface = None
        if self._path is not None:
            try:
                self._path.unlink()
            except OSError:
                pass
            self._path = None
