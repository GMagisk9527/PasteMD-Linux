"""Focus-checked Ctrl+V for WPS running under XWayland."""
import ctypes as C
import ctypes.util
from contextlib import contextmanager


class ClassHint(C.Structure):
    _fields_ = [('res_name', C.c_void_p), ('res_class', C.c_void_p)]


class X11Paste:
    def __init__(self):
        self.x = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.xtest = C.CDLL(ctypes.util.find_library('Xtst') or 'libXtst.so.6')
        ptr = C.c_void_p
        window = C.c_ulong
        self.x.XOpenDisplay.argtypes = [C.c_char_p]
        self.x.XOpenDisplay.restype = ptr
        self.x.XCloseDisplay.argtypes = [ptr]
        self.x.XGetInputFocus.argtypes = [ptr, C.POINTER(window), C.POINTER(C.c_int)]
        self.x.XGetClassHint.argtypes = [ptr, window, C.POINTER(ClassHint)]
        self.x.XQueryTree.argtypes = [ptr, window, C.POINTER(window), C.POINTER(window), C.POINTER(C.POINTER(window)), C.POINTER(C.c_uint)]
        self.x.XFetchName.argtypes = [ptr, window, C.POINTER(ptr)]
        self.x.XFree.argtypes = [ptr]
        self.x.XKeysymToKeycode.argtypes = [ptr, C.c_ulong]
        self.x.XKeysymToKeycode.restype = C.c_ubyte
        self.x.XQueryKeymap.argtypes = [ptr, C.c_void_p]
        self.x.XSync.argtypes = [ptr, C.c_int]
        self.x.XSetErrorHandler.argtypes = [ptr]
        self.x.XSetErrorHandler.restype = ptr
        self.xtest.XTestFakeKeyEvent.argtypes = [ptr, C.c_uint, C.c_int, C.c_ulong]
        self.error_handler = C.CFUNCTYPE(C.c_int, ptr, ptr)(lambda *_: 0)
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('无法连接 XWayland，自动粘贴不可用。')

    @contextmanager
    def _errors(self):
        # A target may disappear during conversion. Do not let BadWindow kill the UI.
        previous = self.x.XSetErrorHandler(C.cast(self.error_handler, C.c_void_p))
        try:
            yield
        finally:
            self.x.XSync(self.display, 0)
            self.x.XSetErrorHandler(previous)

    def focused_wps(self):
        with self._errors():
            focus, revert = C.c_ulong(), C.c_int()
            self.x.XGetInputFocus(self.display, C.byref(focus), C.byref(revert))
            current = focus.value
            for _ in range(16):
                if current in (0, 1):
                    return None
                hint = ClassHint()
                names = []
                if self.x.XGetClassHint(self.display, current, C.byref(hint)):
                    for value in (hint.res_name, hint.res_class):
                        if value:
                            names.append(C.string_at(value).decode('utf-8', 'replace').lower())
                            self.x.XFree(value)
                if any(name in ('wps', 'kwps') for name in names):
                    title_ptr = C.c_void_p()
                    title = b''
                    if self.x.XFetchName(self.display, current, C.byref(title_ptr)) and title_ptr.value:
                        title = C.string_at(title_ptr.value)
                        self.x.XFree(title_ptr)
                    return current, title
                root, parent = C.c_ulong(), C.c_ulong()
                children, count = C.POINTER(C.c_ulong)(), C.c_uint()
                if not self.x.XQueryTree(self.display, current, C.byref(root), C.byref(parent), C.byref(children), C.byref(count)):
                    return None
                if children:
                    self.x.XFree(children)
                if current == parent.value:
                    return None
                current = parent.value
        return None

    def modifiers_held(self):
        state = C.create_string_buffer(32)
        self.x.XQueryKeymap(self.display, state)
        for symbol in (0xffe1, 0xffe2, 0xffe3, 0xffe4, 0xffe7, 0xffe8, 0xffe9, 0xffea, 0xffeb, 0xffec):
            code = self.x.XKeysymToKeycode(self.display, symbol)
            if code and state.raw[code // 8] & (1 << (code % 8)):
                return True
        return False

    def paste(self, target):
        if not target or self.focused_wps() != target:
            raise RuntimeError('WPS 窗口或文档焦点已变化，内容已准备好，请手动 Ctrl+V。')
        if self.modifiers_held():
            raise RuntimeError('快捷键尚未松开，内容已准备好，请手动 Ctrl+V。')
        ctrl = self.x.XKeysymToKeycode(self.display, 0xffe3)
        letter = self.x.XKeysymToKeycode(self.display, ord('v'))
        if not ctrl or not letter:
            raise RuntimeError('无法映射 Ctrl+V 按键，请手动粘贴。')
        # The main-thread check above immediately precedes the four XTEST events.
        try:
            self.xtest.XTestFakeKeyEvent(self.display, ctrl, 1, 0)
            self.xtest.XTestFakeKeyEvent(self.display, letter, 1, 0)
        finally:
            self.xtest.XTestFakeKeyEvent(self.display, letter, 0, 0)
            self.xtest.XTestFakeKeyEvent(self.display, ctrl, 0, 0)
            self.x.XSync(self.display, 0)

    def close(self):
        if self.display:
            self.x.XCloseDisplay(self.display)
            self.display = None
