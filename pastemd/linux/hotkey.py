"""KDE's compositor-managed global shortcuts, including Wayland sessions."""
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QKeySequence


class KdeHotkey(QObject):
    activated = Signal()
    changed = Signal(str)

    def __init__(self, parent=None, component='pastemd-linux'):
        super().__init__(parent)
        self.component = component
        self.action = [component, 'convert-paste', 'PasteMD Linux', '转换并粘贴到 WPS']
        self.bus = None
        self.iface = None
        self.matches = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._dispatch)
        self.bound = False
        self.sequence = ''

    @staticmethod
    def key_value(text):
        sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
        if sequence.count() != 1 or sequence.isEmpty():
            raise ValueError('请设置一个组合键，例如 Ctrl+Shift+B。')
        key = sequence[0].toCombined()
        if not key & 0x7e000000:
            raise ValueError('快捷键需要包含 Ctrl、Alt、Shift 或 Meta。')
        return key

    def _connect(self):
        if self.bus is not None:
            return
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.context = GLib.MainContext.default()
        if not self.bus.name_has_owner('org.kde.kglobalaccel'):
            self.bus = None
            raise RuntimeError('此桌面没有 KDE 全局快捷键服务，可在系统快捷键设置中绑定 --trigger。')
        self.iface = dbus.Interface(self.bus.get_object('org.kde.kglobalaccel', '/kglobalaccel'), 'org.kde.KGlobalAccel')
        self.matches.append(self.bus.add_signal_receiver(self._pressed, signal_name='globalShortcutPressed', dbus_interface='org.kde.kglobalaccel.Component', bus_name='org.kde.kglobalaccel'))
        self.timer.start(20)

    def _dispatch(self):
        for _ in range(20):
            if not self.context.pending():
                break
            self.context.iteration(False)

    def _pressed(self, component, action, timestamp):
        if self.bound and str(component) == self.component and str(action) == self.action[1]:
            self.activated.emit()

    def bind(self, sequence):
        import dbus
        key = self.key_value(sequence)
        self._connect()
        # Re-registering our own current shortcut is valid. KDE reports it as
        # unavailable while the action is active, so skip that check when the
        # normalized sequence did not change.
        normalized = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
        unchanged = self.bound and normalized == self.sequence
        if unchanged:
            return
        if not self.iface.isGlobalShortcutAvailable(dbus.Int32(key), self.component, timeout=3):
            raise RuntimeError('这个快捷键已被其他应用占用，请换一个组合键。')
        self.iface.doRegister(self.action, timeout=3)
        # KDE SetPresent=2, NoAutoloading=4: activate this explicitly chosen binding.
        result = self.iface.setShortcut(self.action, dbus.Array([key], signature='i'), dbus.UInt32(6), timeout=3)
        if key not in result:
            raise RuntimeError('系统未接受快捷键，可能存在冲突。')
        self.bound = True
        self.sequence = normalized
        self.changed.emit(self.sequence)

    def unbind(self):
        if self.iface and self.bound:
            self.iface.setInactive(self.action, timeout=3)
        self.bound = False

    def remove(self):
        """Remove the action from KDE, used only for explicit shutdown/tests."""
        if self.iface:
            self.iface.unRegister(self.action, timeout=3)
        self.bound = False

    def close(self):
        try:
            self.unbind()
        finally:
            self.timer.stop()
            for match in self.matches:
                match.remove()
            self.matches.clear()
