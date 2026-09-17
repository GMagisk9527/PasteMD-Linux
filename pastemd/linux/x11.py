"""Focus-checked Ctrl+V for WPS running under XWayland."""
import ctypes as C
import ctypes.util
from contextlib import contextmanager
import re


class ClassHint(C.Structure):
    _fields_ = [('res_name', C.c_void_p), ('res_class', C.c_void_p)]


class TextProperty(C.Structure):
    _fields_ = [('value', C.c_void_p), ('encoding', C.c_ulong),
                ('format', C.c_int), ('nitems', C.c_ulong)]


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
        self.x.XInternAtom.argtypes = [ptr, C.c_char_p, C.c_int]
        self.x.XInternAtom.restype = window
        self.x.XGetTextProperty.argtypes = [ptr, window, C.POINTER(TextProperty), window]
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

    @staticmethod
    def is_wps(names):
        """Token-exact WM_CLASS match: wps, kwps, wpsoffice, com.wps.*.

        Substring matching would also catch helper windows such as the
        wpscloudsvr daemon observed on real installs.
        """
        def tokens(name):
            return {name, *filter(None, re.split(r'[^a-z0-9]+', name))}
        return any(tokens(name) & {'wps', 'kwps', 'wpsoffice'} for name in names)

    TITLE_SPREADSHEET = re.compile(r'\.(xls\w*|csv|et)(?![a-z0-9])')
    TITLE_WRITER = re.compile(r'\.(docx?|wps|rtf)(?![a-z0-9])')
    TITLE_PRESENTATION = re.compile(r'\.(ppt\w*|dps|ppsx)(?![a-z0-9])')

    @staticmethod
    def classify_title(title):
        """Suite kind from the window title; modern WPS is one wpsoffice
        window with tabs, so only the title tells 文字/表格/演示 apart."""
        text = title.decode('utf-8', 'replace') if isinstance(title, bytes) else title
        text = text.lower()
        if (X11Paste.TITLE_SPREADSHEET.search(text) or 'wps表格' in text
                or '新建表格' in text or '工作簿' in text):
            return 'spreadsheet'
        if (X11Paste.TITLE_PRESENTATION.search(text) or 'wps演示' in text
                or '新建演示' in text):
            return 'presentation'
        if (X11Paste.TITLE_WRITER.search(text) or 'wps文字' in text
                or '新建文档' in text or '新建文字' in text):
            return 'writer'
        return None

    @staticmethod
    def classify(names, title=''):
        """Classify a WPS window: writer, spreadsheet, presentation or None.

        Names are tried in order so res_name wins over res_class; 'et' uses
        exact tokens to avoid matching unrelated apps (net, get, terminal…).
        Modern unified builds expose every suite as 'wpsoffice', where the
        active tab's window title is the only discriminator.
        """
        def tokens(name):
            return {name, *filter(None, re.split(r'[^a-z0-9]+', name))}
        for name in names:
            if tokens(name) & {'et', 'ket'}:
                return 'spreadsheet'
            if tokens(name) & {'wpp', 'kwpp'}:
                return 'presentation'
            if tokens(name) & {'wps', 'kwps'}:
                return 'writer'
        if any(tokens(name) & {'wpsoffice'} for name in names):
            return X11Paste.classify_title(title)
        return None

    def _window_title(self, window_id):
        """UTF-8 title via _NET_WM_NAME, falling back to legacy WM_NAME."""
        with self._errors():
            for atom_name in ('_NET_WM_NAME', 'WM_NAME'):
                atom = self.x.XInternAtom(self.display, atom_name.encode(), False)
                if not atom:
                    continue
                prop = TextProperty()
                if self.x.XGetTextProperty(self.display, window_id, C.byref(prop), atom) \
                        and prop.value and prop.nitems:
                    raw = C.string_at(prop.value, prop.nitems * (prop.format // 8))
                    self.x.XFree(prop.value)
                    return raw.decode('utf-8', 'replace').rstrip('\x00')
            title_ptr = C.c_void_p()
            if self.x.XFetchName(self.display, window_id, C.byref(title_ptr)) and title_ptr.value:
                title = C.string_at(title_ptr.value)
                self.x.XFree(title_ptr)
                return title.decode('utf-8', 'replace')
        return ''

    def focused_app(self):
        """(window, title, kind) of the focused WPS-suite window, else None."""
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
                if names:
                    title = self._window_title(current)
                    kind = self.classify(names, title)
                    if kind:
                        return current, title, kind
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

    def focused_wps(self):
        """Legacy view of focused_app(): the (window, title) of WPS 文字 only."""
        app = self.focused_app()
        if app and app[2] == 'writer':
            return app[0], app[1]
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
        # KWin 目标（非 X 窗口 id）的稳定性已由调用方核对过；XWayland 的 X 焦点
        # 永远是代理窗口，这里只对真正的 X11 目标做身份复核。
        if not target:
            raise RuntimeError('WPS 窗口或文档焦点已变化，内容已准备好，请手动 Ctrl+V。')
        if isinstance(target[0], int) and self.focused_app() != target:
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
