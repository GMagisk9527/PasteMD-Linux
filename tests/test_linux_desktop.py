import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from pastemd.linux import settings
from pastemd.linux.gui import MainWindow
from pastemd.linux.hotkey import KdeHotkey
from pastemd.linux.x11 import X11Paste

app = QApplication.instance() or QApplication([])


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, XDG_CONFIG_HOME=self.temp.name, XDG_DATA_HOME=self.temp.name)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_settings_roundtrip_and_invalid_values(self):
        values = dict(settings.DEFAULTS, hotkey='Ctrl+Alt+B', paste_delay_ms=500, input_format='markdown')
        settings.save_settings(values)
        self.assertEqual(settings.load_settings(), values)
        settings.config_file().write_text(json.dumps({'paste_delay_ms': -10, 'input_format': 'invalid', 'auto_paste': 'false'}))
        loaded = settings.load_settings()
        self.assertEqual(loaded['paste_delay_ms'], 100)
        self.assertEqual(loaded['input_format'], 'auto')
        self.assertTrue(loaded['auto_paste'])

    def test_autostart_and_launcher_only_touch_their_files(self):
        other = Path(self.temp.name) / 'autostart/other.desktop'
        other.parent.mkdir()
        other.write_text('other app')
        settings.set_autostart(True)
        self.assertIn('--minimized', settings.autostart_path().read_text())
        settings.set_autostart(False)
        self.assertFalse(settings.autostart_path().exists())
        self.assertEqual(other.read_text(), 'other app')
        launcher = settings.install_launcher()
        self.assertIn('pastemd-linux.py', launcher.read_text())
        self.assertIn('Terminal=false', launcher.read_text())
        self.assertTrue((Path(self.temp.name) / 'icons/hicolor/256x256/apps/pastemd-linux.png').exists())

    def test_packaged_launch_commands(self):
        with patch.dict(os.environ, {'APPIMAGE': '/tmp/PasteMD.AppImage'}, clear=False):
            self.assertIn('/tmp/PasteMD.AppImage', settings.desktop_entry())
        with patch.dict(os.environ, {'FLATPAK_ID': settings.FLATPAK_APP_ID}, clear=False):
            entry = settings.desktop_entry(minimized=True)
            self.assertIn('flatpak run', entry)
            self.assertIn(settings.FLATPAK_APP_ID, entry)
            self.assertIn('--minimized', entry)

    def test_shortcut_rejects_unmodified_or_multiple_keys(self):
        for text in ('A', '', 'Ctrl+B, Ctrl+C'):
            with self.assertRaises(ValueError):
                KdeHotkey.key_value(text)
        self.assertTrue(KdeHotkey.key_value('Ctrl+Shift+B'))

    def test_rebinding_same_shortcut_does_not_query_availability(self):
        hotkey = KdeHotkey()
        hotkey.bus = Mock()
        hotkey.iface = Mock()
        hotkey.bound = True
        hotkey.sequence = 'Ctrl+Shift+B'
        hotkey.bind('Ctrl+Shift+B')
        hotkey.iface.isGlobalShortcutAvailable.assert_not_called()

    def window(self):
        window = MainWindow(smoke=True)
        self.addCleanup(window.deleteLater)
        return window

    def test_focus_change_cancels_paste(self):
        window = self.window()
        window.x11 = Mock()
        window.target = (10, b'docx')
        window.x11.focused_wps.return_value = (11, b'other')
        window.paste_pending = True
        window.paste_ready_at = 0
        window._try_paste()
        window.x11.paste.assert_not_called()
        self.assertFalse(window.paste_pending)
        self.assertIn('焦点已变化', window.log.toPlainText())

    def test_held_modifiers_wait_then_paste_only_once(self):
        window = self.window()
        window.x11 = Mock()
        window.target = (10, b'docx')
        window.x11.focused_wps.return_value = window.target
        window.x11.modifiers_held.return_value = True
        window.paste_pending = True
        window.paste_ready_at = 0
        window.paste_deadline = time.monotonic() + 10
        window._try_paste()
        window.x11.paste.assert_not_called()
        self.assertTrue(window.paste_pending)
        window.x11.modifiers_held.return_value = False
        window._try_paste()
        window.x11.paste.assert_called_once_with(window.target)
        self.assertFalse(window.paste_pending)

    def test_paste_backend_refuses_changed_target_before_key_events(self):
        backend = X11Paste.__new__(X11Paste)
        backend.focused_wps = Mock(return_value=(11, b'other'))
        backend.xtest = Mock()
        with self.assertRaises(RuntimeError):
            backend.paste((10, b'docx'))
        backend.xtest.XTestFakeKeyEvent.assert_not_called()

    def test_busy_window_does_not_start_another_conversion(self):
        window = self.window()
        window.paste_pending = True
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
            worker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
