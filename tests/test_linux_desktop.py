import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeySequence
from pastemd.linux import settings
from pastemd.linux.gui import MainWindow, start_window
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
        settings.config_file().write_text(json.dumps({
            'paste_delay_ms': -10, 'input_format': 'invalid', 'auto_paste': 'false',
            'reference_docx': 3, 'pandoc_filters': ['keep.lua', 5, ''],
            'pandoc_request_headers': 'bad', 'horizontal_rule_style': 'bogus'}))
        loaded = settings.load_settings()
        self.assertEqual(loaded['paste_delay_ms'], 100)
        self.assertEqual(loaded['input_format'], 'auto')
        self.assertTrue(loaded['auto_paste'])
        self.assertIsNone(loaded['reference_docx'])
        self.assertEqual(loaded['pandoc_filters'], ['keep.lua'])
        self.assertEqual(loaded['pandoc_request_headers'], settings.DEFAULTS['pandoc_request_headers'])
        self.assertEqual(loaded['horizontal_rule_style'], 'default')

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

    def test_flatpak_autostart_uses_host_kde_directory(self):
        with patch.dict(os.environ, {
                'FLATPAK_ID': settings.FLATPAK_APP_ID,
                'HOME': self.temp.name,
                'XDG_CONFIG_HOME': '/private-flatpak-config'}, clear=False):
            expected = Path(self.temp.name) / '.config/autostart/pastemd-linux.desktop'
            self.assertEqual(settings.autostart_path(), expected)
            settings.set_autostart(True)
            self.assertTrue(expected.is_file())

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

    def test_restart_restores_its_own_inactive_shortcut(self):
        hotkey = KdeHotkey()
        hotkey.bus = Mock()
        hotkey.iface = Mock()
        key = KdeHotkey.key_value('Ctrl+Shift+B')
        hotkey.iface.shortcut.return_value = [key]
        hotkey.iface.isGlobalShortcutAvailable.return_value = False
        hotkey.iface.setShortcut.return_value = [key]

        hotkey.bind('Ctrl+Shift+B')

        hotkey.iface.isGlobalShortcutAvailable.assert_not_called()
        hotkey.iface.doRegister.assert_called_once()
        self.assertTrue(hotkey.bound)

    def test_failed_hotkey_connection_can_retry(self):
        hotkey = KdeHotkey()
        bus = Mock()
        bus.name_has_owner.return_value = True
        with patch('dbus.SessionBus', return_value=bus), patch('dbus.Interface', side_effect=[RuntimeError('service restarting'), Mock()]):
            with self.assertRaises(RuntimeError):
                hotkey._connect()
            self.assertIsNone(hotkey.bus)
            self.assertIsNone(hotkey.iface)
            hotkey._connect()
        self.assertIs(hotkey.bus, bus)
        self.assertEqual(len(hotkey.matches), 1)
        hotkey.close()

    def test_shortcut_recording_handles_service_failure(self):
        window = self.window()
        window.hotkey = Mock(bound=True)
        window.hotkey.unbind.side_effect = RuntimeError('service unavailable')
        window.eventFilter(window.key_edit, QEvent(QEvent.Type.FocusIn))
        self.assertIn('暂停热键失败', window.log.toPlainText())

    def test_pending_quit_does_not_open_document_or_schedule_paste(self):
        window = self.window()
        window.pending_quit = True
        window.want_paste = True
        window.target = (10, b'docx')
        with patch('pastemd.linux.gui.cli.open_in_wps') as open_document:
            window._converted({'path': '/tmp/generated.docx'})
            window._converted({'clipboard': True})
        open_document.assert_not_called()
        self.assertFalse(window.paste_timer.isActive())
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert()
        worker.assert_not_called()
        window.hotkey = Mock(bound=False)
        window.smoke = False
        window._resume_after_recording()
        window.hotkey.bind.assert_not_called()

    def test_failed_save_disables_new_hotkey_when_old_setting_was_disabled(self):
        window = self.window()
        window.smoke = False
        window.settings['hotkey_enabled'] = False
        window.hotkey = Mock()
        window.hotkey_enabled.setChecked(True)
        window.key_edit.setKeySequence(QKeySequence('Ctrl+Shift+B'))
        with patch('pastemd.linux.gui.save_settings',
                   side_effect=OSError('read-only settings')):
            window.save()
        window.hotkey.bind.assert_called_once_with('Ctrl+Shift+B')
        window.hotkey.unbind.assert_called_once()
        self.assertIn('设置未完整保存', window.log.toPlainText())

    def window(self):
        window = MainWindow(smoke=True)
        self.addCleanup(window.deleteLater)
        return window

    def test_focus_change_cancels_paste(self):
        window = self.window()
        window.x11 = Mock()
        window.target = (10, b'docx')
        window.x11.focused_app.return_value = (11, b'other')
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
        window.x11.focused_app.return_value = window.target
        window.x11.modifiers_held.return_value = True
        window.paste_pending = True
        window.paste_ready_at = 0
        window.paste_deadline = time.monotonic() + 10
        window._try_paste()
        window.x11.paste.assert_not_called()
        self.assertTrue(window.paste_pending)
        window.x11.modifiers_held.return_value = False
        window.clipboard_token = b"sample"
        with patch("pastemd.linux.gui.QApplication.clipboard") as clipboard:
            clipboard.return_value.mimeData.return_value.data.return_value = b"sample"
            window._try_paste()
        window.x11.paste.assert_called_once_with(window.target)
        self.assertFalse(window.paste_pending)

    def test_changed_clipboard_cancels_automatic_paste(self):
        window = self.window()
        window.x11 = Mock()
        window.target = (10, b'docx')
        window.x11.focused_app.return_value = window.target
        window.x11.modifiers_held.return_value = False
        window.clipboard_token = b'original'
        window.paste_pending = True
        window.paste_ready_at = 0
        with patch('pastemd.linux.gui.QApplication.clipboard') as clipboard:
            clipboard.return_value.mimeData.return_value.data.return_value = b'other'
            window._try_paste()
        window.x11.paste.assert_not_called()
        self.assertFalse(window.paste_pending)
        self.assertIn('剪贴板已变化', window.log.toPlainText())

    def test_cold_trigger_does_not_show_window_even_without_tray(self):
        window = Mock(tray=None)
        args = Mock(trigger=True, minimized=False, smoke_test=None)
        with patch('pastemd.linux.gui.QTimer.singleShot') as timer:
            start_window(window, args)
            window.show.assert_not_called()
            timer.call_args.args[1]()
        window.convert.assert_called_once_with(paste=True)
        args.trigger = False
        start_window(window, args)
        window.show.assert_called_once()

    def test_paste_backend_refuses_changed_target_before_key_events(self):
        backend = X11Paste.__new__(X11Paste)
        backend.focused_app = Mock(return_value=(11, b'other'))
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

    def test_wps_window_class_matches_loose_variants(self):
        for names in (['wps'], ['kwps'], ['wpsoffice'], ['com.wps.writer']):
            self.assertTrue(X11Paste.is_wps(names), names)
        self.assertFalse(X11Paste.is_wps(['kwrite']))
        self.assertFalse(X11Paste.is_wps(['wpscloudsvr', 'qing']))
        self.assertFalse(X11Paste.is_wps([]))

    def test_window_classification_routes_wps_suites(self):
        classify = X11Paste.classify
        self.assertEqual(classify(['wps']), 'writer')
        self.assertEqual(classify(['kwps']), 'writer')
        self.assertEqual(classify(['et', 'wps']), 'spreadsheet')
        self.assertEqual(classify(['ket', 'ket']), 'spreadsheet')
        self.assertEqual(classify(['et.exe', 'Et']), 'spreadsheet')
        self.assertEqual(classify(['wpp', 'wps']), 'presentation')
        self.assertIsNone(classify(['kwrite']))
        self.assertIsNone(classify(['get', 'net']))
        self.assertIsNone(classify(['wpscloudsvr', 'qing']))
        self.assertIsNone(classify([]))

    def test_title_classification_for_unified_wpsoffice_windows(self):
        classify = X11Paste.classify
        self.assertEqual(classify(['wpsoffice'], '测试文档.docx - WPS Office'), 'writer')
        self.assertEqual(classify(['wpsoffice'], '成绩表.csv - WPS Office'), 'spreadsheet')
        self.assertEqual(classify(['wpsoffice'], '数据汇总.xlsx - WPS Office'), 'spreadsheet')
        self.assertEqual(classify(['wpsoffice'], '答辩.pptx - WPS Office'), 'presentation')
        self.assertEqual(classify(['wpsoffice'], '新建表格 - WPS Office'), 'spreadsheet')
        self.assertEqual(classify(['wpsoffice'], '工作簿1'), 'spreadsheet')
        self.assertEqual(classify(['wpsoffice'], '报表.et'), 'spreadsheet')
        self.assertIsNone(classify(['wpsoffice'], 'WPS Office'))
        self.assertIsNone(classify(['wpsoffice'], ''))

    def test_spreadsheet_focus_routes_to_table_flow(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.x11.focused_app.return_value = (10, '表格', 'spreadsheet')
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
        self.assertEqual(window.flow, 'table')
        self.assertTrue(window.want_paste)
        self.assertEqual(worker.call_args.kwargs['flow'], 'table')

    def test_spreadsheet_flow_disabled_keeps_document_flow(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.x11.focused_app.return_value = (10, '表格', 'spreadsheet')
        window.settings['enable_excel'] = False
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
        self.assertEqual(window.flow, 'doc')
        self.assertEqual(worker.call_args.kwargs['flow'], 'doc')

    def test_writer_focus_keeps_document_flow(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.x11.focused_app.return_value = (11, '文档', 'writer')
        with patch('pastemd.linux.gui.ConversionWorker'):
            window.convert(paste=True)
        self.assertEqual(window.flow, 'doc')
        self.assertTrue(window.want_paste)

    def test_kwin_focus_routes_spreadsheet_title(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.kwin = Mock()
        window.kwin.available = True
        window.kwin.focused_app.return_value = (
            'kwin:io.github.GMagisk9527.PasteMDLinux', '成绩表.csv - WPS Office', 'spreadsheet')
        with patch('pastemd.linux.gui.ConversionWorker'):
            window.convert(paste=True)
        self.assertEqual(window.flow, 'table')
        self.assertEqual(window.target,
                         ('kwin:io.github.GMagisk9527.PasteMDLinux', '成绩表.csv - WPS Office', 'spreadsheet'))
        self.assertTrue(window.want_paste)
        window.x11.focused_app.assert_not_called()

    def test_kwin_unavailable_degrades_to_x11(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.x11.focused_app.return_value = (10, 'doc', 'writer')
        window.kwin = Mock()
        window.kwin.available = True
        window.kwin.focused_app.return_value = None
        self.assertEqual(window.focused_app(), (10, 'doc', 'writer'))
        window.kwin = None
        self.assertEqual(window.focused_app(), (10, 'doc', 'writer'))
        window.x11 = None
        self.assertIsNone(window.focused_app())

    def test_rule_match_routes_md_text_flow(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.kwin = Mock()
        window.kwin.available = True
        window.kwin.focused_app.return_value = ('kwin:x', '语雀 - Chrome', None, 'chrome')
        window.settings['extensible_workflows'] = {
            'md': {'enabled': True, 'apps': [{'name': '语雀', 'class': 'chrome'}]},
            'latex': {'enabled': True, 'apps': []},
            'html': {'enabled': True, 'apps': []}}
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
        self.assertEqual(window.flow, 'md')
        self.assertTrue(window.want_paste)
        self.assertEqual(worker.call_args.kwargs['flow'], 'md')

    def test_non_wps_window_without_rule_keeps_manual_paste(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.kwin = Mock()
        window.kwin.available = True
        window.kwin.focused_app.return_value = ('kwin:x', '首页 - Chrome', None, 'chrome')
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
        self.assertEqual(window.flow, 'doc')
        self.assertIsNone(window.target)
        self.assertFalse(window.want_paste)
        self.assertIn('没有匹配的粘贴规则', window.log.toPlainText())
        worker.assert_called_once()

    def test_convert_debounces_rapid_triggers(self):
        import time as time_module
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.x11.focused_app.return_value = None
        window.kwin = Mock()
        window.kwin.available = True
        window.kwin.focused_app.return_value = None
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
            self.assertEqual(worker.call_count, 1)
            # 0.5 秒内的再次触发应被静默忽略（对齐上游 FIRE_DEBOUNCE_SEC）
            window.convert(paste=True)
            self.assertEqual(worker.call_count, 1)
            # 超过防抖窗口后可以再次触发（清掉上次的 worker 以隔离验证防抖）
            window._last_convert = time_module.monotonic() - 1
            window.worker = None
            window.convert(paste=True)
            self.assertEqual(worker.call_count, 2)

    def test_workflow_rules_survive_settings_round_trip(self):
        window = self.window()
        enabled, edit = window.workflow_edits['md']
        enabled.setChecked(True)
        edit.setPlainText('语雀 | yuque | 语雀\n腾讯文档 || docs\\.qq\\.com')
        window.save()
        loaded = settings.load_settings()
        self.assertTrue(loaded['extensible_workflows']['md']['enabled'])
        self.assertEqual(loaded['extensible_workflows']['md']['apps'], [
            {'name': '语雀', 'class': 'yuque', 'window_patterns': ['语雀']},
            {'name': '腾讯文档', 'window_patterns': ['docs\\.qq\\.com']}])
        self.assertEqual(loaded['extensible_workflows']['latex']['apps'], [])

    def test_settings_loader_sanitizes_workflow_rules(self):
        config = settings.config_file()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({
            'extensible_workflows': {
                'md': {'enabled': 'yes', 'apps': [{'name': 'ok', 'class': 'ok'}, 'bad', 7]},
                'latex': 'bad',
                'html': None,
                'file': {'enabled': True, 'apps': []}}}))
        loaded = settings.load_settings()
        self.assertEqual(loaded['extensible_workflows']['md'],
                         {'enabled': True, 'apps': [{'name': 'ok', 'class': 'ok'}]})
        self.assertEqual(loaded['extensible_workflows']['latex'],
                         {'enabled': True, 'apps': []})
        self.assertNotIn('file', loaded['extensible_workflows'])

    def test_settings_no_app_action_and_highlight_sanitize(self):
        config = settings.config_file()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({'no_app_action': 'whatever',
                                      'code_highlight_style': 'neon'}))
        loaded = settings.load_settings()
        self.assertEqual(loaded['no_app_action'], 'clipboard')
        self.assertEqual(loaded['code_highlight_style'], 'default')
        config.write_text(json.dumps({'no_app_action': 'ask',
                                      'code_highlight_style': 'zenburn'}))
        loaded = settings.load_settings()
        self.assertEqual(loaded['no_app_action'], 'ask')
        self.assertEqual(loaded['code_highlight_style'], 'zenburn')

    def test_gui_no_app_action_control_roundtrip(self):
        window = self.window()
        try:
            index = window.no_app_action.findData('convert_anyway')
            window.no_app_action.setCurrentIndex(index)
            window.save()
            reloaded = settings.load_settings()
            self.assertEqual(reloaded['no_app_action'], 'convert_anyway')
            index = window.highlight_style.findData('none')
            window.highlight_style.setCurrentIndex(index)
            window.save()
            self.assertEqual(settings.load_settings()['code_highlight_style'], 'none')
        finally:
            window.close()

    def test_gui_convert_anyway_keeps_target_without_match(self):
        window = self.window()
        try:
            window.smoke = False
            window.x11 = Mock()
            window.x11.focused_app.return_value = (11, '记事本', 'editor', 'gedit')
            window.settings['no_app_action'] = 'convert_anyway'
            with patch('pastemd.linux.gui.ConversionWorker') as worker:
                window.convert(paste=True)
            self.assertEqual(window.flow, 'doc')
            self.assertEqual(window.target, (11, '记事本', 'editor', 'gedit'))
            self.assertTrue(window.want_paste)
            worker.assert_called_once()
        finally:
            window.close()

    def test_gui_clipboard_action_drops_target(self):
        window = self.window()
        try:
            window.smoke = False
            window.x11 = Mock()
            window.x11.focused_app.return_value = (11, '记事本', 'editor', 'gedit')
            window.settings['no_app_action'] = 'clipboard'
            with patch('pastemd.linux.gui.ConversionWorker'):
                window.convert(paste=True)
            self.assertIsNone(window.target)
            self.assertFalse(window.want_paste)
        finally:
            window.close()

    def test_gui_tray_rule_action_refreshes_with_focus(self):
        window = self.window()
        try:
            window.smoke = True  # 无托盘实体，直接测 refresh 逻辑
            window.tray_rule_action = Mock()
            window.x11 = Mock()
            window.x11.focused_app.return_value = (11, '语雀笔记', 'browser', 'yuque')
            window._refresh_tray_rule_action()
            window.tray_rule_action.setVisible.assert_called_with(True)
            self.assertEqual(getattr(window, '_tray_app', None),
                             (11, '语雀笔记', 'browser', 'yuque'))
            window.x11.focused_app.return_value = (11, '文档', 'writer')
            window._refresh_tray_rule_action()
            window.tray_rule_action.setVisible.assert_called_with(False)
        finally:
            window.close()

    def test_gui_ask_action_offers_add_rule(self):
        window = self.window()
        try:
            window.smoke = False
            window.x11 = Mock()
            window.x11.focused_app.return_value = (11, '语雀笔记', 'browser', 'yuque')
            window.settings['no_app_action'] = 'ask'
            with patch.object(window, '_ask_no_app_action', return_value='rule') as ask, \
                 patch.object(window, '_add_rule_for_window') as add_rule, \
                 patch('pastemd.linux.gui.ConversionWorker') as worker:
                window.convert(paste=True)
            ask.assert_called_once()
            add_rule.assert_called_once()
            worker.assert_not_called()
        finally:
            window.close()

    def test_settings_html_formatting_roundtrip_and_sanitize(self):
        values = dict(settings.DEFAULTS)
        values['html_formatting'] = {'css_font_to_semantic': False,
                                     'bold_first_row_to_header': True,
                                     'preserve_prewrap_newlines': False}
        settings.save_settings(values)
        loaded = settings.load_settings()
        self.assertEqual(loaded['html_formatting'],
                         {'css_font_to_semantic': False,
                          'bold_first_row_to_header': True,
                          'preserve_prewrap_newlines': False})
        config = settings.config_file()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({'html_formatting': {
            'css_font_to_semantic': 'yes', 'bold_first_row_to_header': True,
            'strikethrough_to_del': False}}))
        loaded = settings.load_settings()
        self.assertEqual(loaded['html_formatting'],
                         {'css_font_to_semantic': True,
                          'bold_first_row_to_header': True,
                          'preserve_prewrap_newlines': True})

    def test_gui_save_persists_html_formatting(self):
        window = self.window()
        window.font_semantic.setChecked(False)
        window.bold_header.setChecked(True)
        window.prewrap_newlines.setChecked(False)
        window.save()
        loaded = settings.load_settings()
        self.assertEqual(loaded['html_formatting'],
                         {'css_font_to_semantic': False,
                          'bold_first_row_to_header': True,
                          'preserve_prewrap_newlines': False})

    def test_converted_reports_ready_table(self):
        window = self.window()
        window.want_paste = False
        window._converted({'clipboard': True, 'token': b't', 'rows': 5})
        self.assertIn('表格已就绪（5 行）', window.log.toPlainText())

    def test_convert_reports_missing_wps_focus(self):
        window = self.window()
        window.smoke = False
        window.x11 = Mock()
        window.x11.focused_app.return_value = None
        with patch('pastemd.linux.gui.ConversionWorker') as worker:
            window.convert(paste=True)
        self.assertIn('未找到获得焦点的 WPS 窗口', window.log.toPlainText())
        self.assertFalse(window.want_paste)
        self.assertIsNone(window.target)
        worker.assert_called_once()
        self.assertEqual(worker.call_args.kwargs['options'], dict(window.settings))

    def test_save_persists_conversion_enhancement_keys(self):
        window = self.window()
        window.keep_formula.setChecked(True)
        window.auto_tables.setChecked(True)
        window.rule_style.setCurrentIndex(window.rule_style.findData('paragraph_border'))
        window.reference_edit.setText('/tmp/template.docx')
        window.filters_edit.setPlainText('/tmp/custom.lua\n\n/tmp/tool.py')
        window.save()
        loaded = settings.load_settings()
        self.assertTrue(loaded['keep_original_formula'])
        self.assertTrue(loaded['docx_auto_table_layout'])
        self.assertEqual(loaded['horizontal_rule_style'], 'paragraph_border')
        self.assertEqual(loaded['reference_docx'], '/tmp/template.docx')
        self.assertEqual(loaded['pandoc_filters'], ['/tmp/custom.lua', '/tmp/tool.py'])
        self.assertEqual(loaded['pandoc_request_headers'],
                         [line.strip() for line in window.headers_edit.toPlainText().splitlines()
                          if line.strip()])

    def test_converted_reports_lost_images(self):
        window = self.window()
        window.want_paste = False
        window._converted({'clipboard': True, 'token': b't', 'lost_images': 2})
        self.assertIn('2 张图片未能嵌入', window.log.toPlainText())
        self.assertEqual(window.lost_images, 2)

    def test_paste_message_reports_lost_images(self):
        window = self.window()
        window.x11 = Mock()
        window.target = (10, b'docx')
        window.x11.focused_app.return_value = window.target
        window.x11.modifiers_held.return_value = False
        window.clipboard_token = b'sample'
        window.lost_images = 3
        window.paste_pending = True
        window.paste_ready_at = 0
        with patch('pastemd.linux.gui.QApplication.clipboard') as clipboard:
            clipboard.return_value.mimeData.return_value.data.return_value = b'sample'
            window._try_paste()
        self.assertIn('3 张图片未能嵌入', window.log.toPlainText())


if __name__ == '__main__':
    unittest.main()
