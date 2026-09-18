import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import tempfile
import shutil
import zipfile
import xml.etree.ElementTree as ET
import unittest
from unittest.mock import patch

from pastemd.linux import cli


class WaylandTests(unittest.TestCase):
    def pipe_process(self, code):
        process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdin.close()
            process.stdout.close()
        self.addCleanup(cleanup)
        return process

    def test_clipboard_handshake_bounds_blocked_write(self):
        process = self.pipe_process('import time; time.sleep(30)')
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, '超时'):
            cli.clipboard_handshake(process, b'x' * 1000000, timeout=0.2)
        self.assertLess(time.monotonic() - started, 3)

    def test_clipboard_handshake_bounds_partial_reply(self):
        process = self.pipe_process("import sys,time; sys.stdin.buffer.read(); sys.stdout.write('REA'); sys.stdout.flush(); time.sleep(30)")
        with self.assertRaisesRegex(RuntimeError, '超时'):
            cli.clipboard_handshake(process, b'{}', timeout=0.3)

    def test_clipboard_handshake_accepts_complete_reply(self):
        process = self.pipe_process("import sys; sys.stdin.buffer.read(); print('READY', flush=True)")
        cli.clipboard_handshake(process, b'{}', timeout=3)

    def test_auto_prefers_html(self):
        with patch.object(cli, 'run', side_effect=[
                b'text/plain\ntext/html\n', b'<p>Hello</p>', b'Hello plain']) as run:
            self.assertEqual(cli.read_clipboard('auto'), (b'<p>Hello</p>', 'html' + cli.MATH_EXTENSIONS))
            self.assertIn('text/html', run.call_args_list[1].args[0])

    def test_auto_accepts_html_type_with_charset(self):
        advertised = 'text/html;charset=utf-8'
        with patch.object(cli, 'run', side_effect=[
                ('text/plain\n' + advertised + '\n').encode(), b'<p>Hello</p>',
                b'Hello']) as run:
            content, reader = cli.read_clipboard('auto')
        self.assertEqual(content, b'<p>Hello</p>')
        self.assertEqual(reader, 'html' + cli.MATH_EXTENSIONS)
        self.assertEqual(run.call_args_list[1].args[0][-1], advertised)

    def test_markdown_override(self):
        with patch.object(cli, 'run', side_effect=[b'text/html\ntext/plain;charset=utf-8\n', b'# Hello']):
            self.assertEqual(cli.read_clipboard('markdown'), (b'# Hello', 'markdown' + cli.MATH_EXTENSIONS))

    def test_flatpak_opens_docx_through_host_wps(self):
        with patch.dict(os.environ, FLATPAK_ID='io.github.example.App'), \
                patch.object(cli.shutil, 'which', return_value='/usr/bin/flatpak-spawn'), \
                patch.object(cli.subprocess, 'Popen') as process:
            cli.open_in_wps('/tmp/example.docx')
        self.assertEqual(process.call_args.args[0],
                         ['flatpak-spawn', '--host', 'wps', '/tmp/example.docx'])

    def test_conversion_failure_does_not_write_clipboard(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'run', side_effect=RuntimeError('conversion failed')), patch.object(cli, 'notify'), patch.object(cli.subprocess, 'run') as process:
            self.assertEqual(cli.main([], options={}), 1)
            process.assert_not_called()

    def test_docx_output_preserves_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'run', return_value=b'') as run, patch.object(cli, 'notify'):
                self.assertEqual(cli.main(['--docx'], options={}), 0)
                command = run.call_args.args[0]
                self.assertEqual([Path(command[0]).name] + command[1:3], ['pandoc', '--from', 'json'])
                self.assertIn('--to', command)
                self.assertEqual(command[-1], '-')
                saved = list((Path(directory) / 'pastemd').glob('*.docx'))
                self.assertEqual(len(saved), 1)

    def test_failed_docx_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'run', side_effect=RuntimeError('conversion failed')), patch.object(cli, 'notify'):
                self.assertEqual(cli.main(['--docx'], options={}), 1)
                self.assertEqual(list((Path(directory) / 'pastemd').iterdir()), [])


    def test_default_writes_native_docx_without_opening_wps(self):
        expected = {'Kingsoft WPS 9.0 Format': b'docx', 'text/plain': b'plain'}
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'native_clipboard_payload', return_value=expected), patch.object(cli, 'run', return_value=b'plain'), patch.object(cli, 'notify'), patch.object(cli, 'set_clipboard_payload') as clipboard, patch.object(cli.subprocess, 'Popen') as opener:
            self.assertEqual(cli.main([], options={}), 0)
            clipboard.assert_called_once_with(expected)
            opener.assert_not_called()

    def test_failed_native_conversion_does_not_change_clipboard(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'run', return_value=b'plain'), patch.object(cli, 'native_clipboard_payload', side_effect=RuntimeError('math lost')), patch.object(cli, 'notify'), patch.object(cli, 'set_clipboard_payload') as clipboard:
            self.assertEqual(cli.main([], options={}), 1)
            clipboard.assert_not_called()

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_native_payload_preserves_demo_equations_without_image_fallback(self):
        import io
        prepared = cli.prepare_document(cli.DEMO_MARKDOWN.encode(), 'markdown' + cli.MATH_EXTENSIONS)
        payload = cli.native_clipboard_payload(prepared, b'plain')
        self.assertEqual(set(payload), {'Kingsoft WPS 9.0 Format', 'text/plain'})
        with zipfile.ZipFile(io.BytesIO(payload['Kingsoft WPS 9.0 Format'])) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        ns = {'m': 'http://schemas.openxmlformats.org/officeDocument/2006/math', 'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        self.assertEqual(len(root.findall('.//m:oMath', ns)), 4)
        for kind in ('f', 'rad', 'nary', 'm'):
            self.assertTrue(root.findall('.//m:' + kind, ns), kind)
        self.assertTrue(root.findall('.//w:tbl', ns))
        self.assertFalse(root.findall('.//w:drawing', ns))
        self.assertIn('中文', ''.join(root.itertext()))

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_native_rejects_missing_equations(self):
        import json
        plain = cli.prepare_document(b'No math', 'markdown')
        docx = cli.run(['pandoc', '-f', 'json', '-t', 'docx', '-o', '-'], plain)
        document = json.dumps({'blocks': [{'t': 'Math', 'c': [{'t': 'InlineMath'}, 'a']}]}).encode()
        with patch.object(cli, 'run', return_value=docx):
            with self.assertRaisesRegex(RuntimeError, '0/1'):
                cli.native_clipboard_payload(document, b'plain')

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_deepseek_hidden_math_survives_as_native_docx(self):
        source = r'''<div style="padding-left:calc(50% - 420px);min-height:3202px">
<h3>第一步</h3><p><strong>中文</strong>
<span class="katex"><span class="katex-mathml" style="clip:rect(1px,1px,1px,1px);width:1px;height:1px;overflow:hidden">
<math xmlns="http://www.w3.org/1998/Math/MathML"><semantics><mfrac><mi>b</mi><mi>a</mi></mfrac><annotation encoding="application/x-tex">\frac{b}{a}</annotation></semantics></math>
</span><span class="katex-html" aria-hidden="true">DUPLICATE</span></span></p>
<table><tr><th>列</th></tr><tr><td>值</td></tr></table></div>'''
        prepared = cli.prepare_document(source.encode(), 'html' + cli.MATH_EXTENSIONS)
        html = cli.run(['pandoc', '-f', 'json', '-t', 'html', '--mathml'], prepared).decode()
        self.assertNotIn('3202px', html)
        self.assertNotIn('1px', html)
        self.assertNotIn('DUPLICATE', html)
        self.assertNotIn('<style', html)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'math.docx'
            cli.run(['pandoc', '-f', 'json', '-t', 'docx', '-o', str(path)], prepared)
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read('word/document.xml'))
            ns = {'m': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
                  'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            self.assertEqual(len(root.findall('.//m:oMath', ns)), 1)
            self.assertEqual(len(root.findall('.//m:f', ns)), 1)
            self.assertEqual(len(root.findall('.//w:tbl', ns)), 1)
            self.assertIn('中文', ''.join(root.itertext()))

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_all_math_delimiters_and_code(self):
        source = r'$a$' + '\n\n' + r'$$b$$' + '\n\n' + r'\(c\)' + '\n\n' + r'\[d\]' + '\n\n' + r'`$literal$`'
        prepared = cli.prepare_document(source.encode(), 'markdown' + cli.MATH_EXTENSIONS)
        import json
        def count_math(value):
            if isinstance(value, dict):
                return int(value.get('t') == 'Math') + sum(count_math(v) for v in value.values())
            if isinstance(value, list):
                return sum(map(count_math, value))
            return 0
        self.assertEqual(count_math(json.loads(prepared)), 4)

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_lost_image_count_flags_unreachable_images(self):
        prepared = cli.prepare_document(
            '<p><img src="/pastemd-missing-image.png" alt="缺失" /></p>'.encode(), 'html')
        docx = cli.run(['pandoc', '-f', 'json', '-t', 'docx', '-o', '-'], prepared)
        self.assertEqual(cli.lost_image_count(prepared, docx), 1)

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_lost_image_count_accepts_embedded_and_deduped_images(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'dot.png'
            image.write_bytes(base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ'
                'AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))
            source = (f'<p><img src="{image}" alt="一"/></p>'
                      f'<p><img src="{image}" alt="二"/></p>').encode()
            prepared = cli.prepare_document(source, 'html')
            docx = cli.run(['pandoc', '-f', 'json', '-t', 'docx', '-o', '-'], prepared)
            self.assertEqual(cli.lost_image_count(prepared, docx), 0)

    def test_docx_writer_args_carry_reference_headers(self):
        options = {'reference_docx': '/tmp/ref.docx',
                   'pandoc_request_headers': ['User-Agent: test-agent']}
        self.assertEqual(cli.docx_writer_args(options),
                         ['--highlight-style', 'tango', '--reference-doc', '/tmp/ref.docx',
                          '--request-header', 'User-Agent: test-agent'])
        self.assertEqual(cli.docx_writer_args({}), ['--highlight-style', 'tango'])

    def test_stage_filters_follow_conversion_flags(self):
        options = {'pandoc_filters': ['/tmp/custom.lua', '/tmp/tool.py'],
                   'enable_latex_replacements': False, 'keep_original_formula': True}
        args = cli._stage_filter_args('markdown' + cli.MATH_EXTENSIONS, options)
        self.assertIn(cli.lua_filter('keep-latex-math.lua'), args)
        self.assertIn(cli.lua_filter('normalize-markdown-breaks.lua'), args)
        self.assertNotIn(cli.lua_filter('latex-replacements.lua'), args)
        self.assertEqual(args.count('--lua-filter'), 3)
        self.assertIn('/tmp/custom.lua', args)
        self.assertEqual(args[args.index('--filter') + 1], '/tmp/tool.py')

    def test_lua_filters_resolve_inside_package(self):
        for name in ('latex-replacements.lua', 'normalize-markdown-breaks.lua', 'keep-latex-math.lua'):
            self.assertTrue(Path(cli.lua_filter(name)).is_file(), name)

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_single_dollar_block_fix(self):
        source = '前言\n$\nx^2+y^2\n$\n后记'
        prepared = cli.prepare_document(source.encode(), 'markdown' + cli.MATH_EXTENSIONS, {})
        import json
        self.assertGreaterEqual(self._count_math(json.loads(prepared)), 1)
        untouched = cli.prepare_document(source.encode(), 'markdown' + cli.MATH_EXTENSIONS,
                                         {'fix_single_dollar_block': False})
        self.assertEqual(self._count_math(json.loads(untouched)), 0)

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_keep_original_formula_converts_math_to_text(self):
        import json
        prepared = cli.prepare_document(cli.DEMO_MARKDOWN.encode(),
                                        'markdown' + cli.MATH_EXTENSIONS,
                                        {'keep_original_formula': True})
        self.assertEqual(self._count_math(json.loads(prepared)), 0)
        self.assertIn('$', prepared.decode('utf-8'))

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_finish_docx_applies_upstream_post_processing(self):
        import io
        prepared = cli.prepare_document(
            '# Heading\n\n段落文本\n\n| 一 | 二 |\n|---|---|\n| a | b |\n'.encode(),
            'markdown' + cli.MATH_EXTENSIONS, {})
        raw = cli.run(['pandoc', '--from', 'json', '--to', 'docx', '--output', '-'], prepared)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        for docx, options, expect_layout in ((raw, {}, False),
                                             (cli.finish_docx(raw, 'markdown', {}), {}, False),
                                             (cli.finish_docx(raw, 'markdown',
                                                              {'docx_auto_table_layout': True}),
                                              {'docx_auto_table_layout': True}, True)):
            with zipfile.ZipFile(io.BytesIO(docx)) as archive:
                root = ET.fromstring(archive.read('word/document.xml'))
            layouts = root.findall('.//w:tblLayout', ns)
            self.assertEqual(bool(layouts and layouts[0].get(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type') == 'fixed'),
                expect_layout)

    @unittest.skipUnless(shutil.which('pandoc'), 'Pandoc required')
    def test_finish_docx_replaces_first_paragraph_style(self):
        import io
        raw = cli.run(['pandoc', '--from', 'markdown', '--to', 'docx', '--output', '-'],
                      b'# Title\n\nFirst body paragraph.')
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        before = [p.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')
                  for p in root.findall('.//w:pStyle', ns)]
        self.assertIn('FirstParagraph', before)
        docx = cli.finish_docx(raw, 'markdown', {})
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        after = [p.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')
                 for p in root.findall('.//w:pStyle', ns)]
        self.assertNotIn('FirstParagraph', after)
        self.assertIn('BodyText', after)

    @staticmethod
    def _count_math(value):
        if isinstance(value, dict):
            return int(value.get('t') == 'Math') + sum(WaylandTests._count_math(v) for v in value.values())
        if isinstance(value, list):
            return sum(map(WaylandTests._count_math, value))
        return 0

    def test_markdown_table_parser(self):
        from pastemd.utils.spreadsheet import parse_markdown_table
        table = parse_markdown_table(
            '| 名称 | 值 |\n|---|---|\n| a\\|b | **粗** |\n| `c` | [链接](https://x.y) |')
        self.assertEqual(table, [['名称', '值'], ['a|b', '**粗**'], ['`c`', '[链接](https://x.y)']])
        self.assertEqual(parse_markdown_table('| 名称 |\n|---|\n| 值 |'), [['名称'], ['值']])
        self.assertEqual(parse_markdown_table('a | b\n--- | ---\n1 | 2'),
                         [['a', 'b'], ['1', '2']])
        self.assertIsNone(parse_markdown_table('没有表格的文本'))
        self.assertIsNone(parse_markdown_table('| 只有表头 |'))

    def test_table_html_and_tsv_rendering(self):
        from pastemd.utils.spreadsheet import table_to_html, table_to_tsv
        html = table_to_html([['名称', '值'], ['a|b', '**粗**`code`']])
        self.assertIn('<th', html)
        self.assertIn('<b>粗</b>', html)
        self.assertIn('<code>code</code>', html)
        self.assertIn('a|b', html)
        self.assertEqual(table_to_tsv([['名称', '值'], ['a|b', 'plain']]),
                         '名称\t值\na|b\tplain')

    def test_table_clipboard_payload_formats(self):
        payload, rows = cli.table_clipboard_payload('| 名称 | 值 |\n|---|---|\n| a | b |')
        self.assertEqual(rows, 2)
        self.assertEqual(sorted(payload), ['text/html', 'text/plain'])
        self.assertIn(b'<table>', payload['text/html'])
        self.assertEqual(payload['text/plain'].decode('utf-8'), '名称\t值\na\tb')

    def test_table_payload_rejects_non_table(self):
        with self.assertRaisesRegex(RuntimeError, '没有 Markdown 表格'):
            cli.table_clipboard_payload('普通文本，没有表格')

    def test_read_table_source_prefers_plain_then_html(self):
        with patch.object(cli, 'run', side_effect=[
                b'text/plain;charset=utf-8\ntext/html\n',
                '| a | b |\n|---|---|\n| 1 | 2 |'.encode()]) as run:
            text = cli.read_table_source()
        self.assertIn('| a | b |', text)
        self.assertEqual(run.call_args_list[1].args[0][1], '--no-newline')

    def test_read_table_source_converts_html_tables(self):
        html = '<table><tr><th>a</th></tr><tr><td>1</td></tr></table>'
        with patch.object(cli, 'run', side_effect=[
                b'text/html\n', html.encode(), '| a |\n|---|\n| 1 |'.encode()]) as run:
            text = cli.read_table_source()
        self.assertIn('| a |', text)
        self.assertEqual([Path(run.call_args_list[2].args[0][0]).name] + run.call_args_list[2].args[0][1:2], ['pandoc', '--from'])
        self.assertEqual(run.call_args_list[2].args[0][-1], 'gfm')

    def test_cli_table_mode_writes_html_clipboard(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), \
             patch.object(cli.shutil, 'which', return_value='/bin/tool'), \
             patch.object(cli, 'read_table_source', return_value='| 名称 |\n|---|\n| 值 |'), \
             patch.object(cli, 'notify') as notify, \
             patch.object(cli, 'set_clipboard_payload') as clipboard:
            self.assertEqual(cli.main(['--table'], options={}), 0)
        payload = clipboard.call_args.args[0]
        self.assertIn(b'<table>', payload['text/html'])
        self.assertIn('表格已就绪（2 行）', notify.call_args.args[0])

    def test_apprules_match_semantics(self):
        from pastemd.utils import apprules
        hit_class = {'name': '语雀', 'class': 'yuque'}
        self.assertTrue(apprules.match_app(hit_class, 'yuque', '语雀文档'))
        self.assertTrue(apprules.match_app({'class': 'et'}, 'et.exe', '表格'))
        self.assertFalse(apprules.match_app({'class': 'et'}, 'wpsoffice', 'WPS'))
        self.assertFalse(apprules.match_app({'class': 'et'}, 'netease-cloud', '云音乐'))
        hit_title = {'name': '文档', 'window_patterns': ['语雀']}
        self.assertTrue(apprules.match_app(hit_title, 'chrome', '语雀 - Google Chrome'))
        self.assertFalse(apprules.match_app(hit_title, 'chrome', '首页 - Google Chrome'))
        both = {'name': 'x', 'class': 'yuque', 'window_patterns': ['语雀']}
        self.assertFalse(apprules.match_app(both, 'chrome', '语雀 - Chrome'))
        self.assertIsNone(apprules.active_flow({}, 'yuque', '语雀'))
        workflows = {'md': {'enabled': True, 'apps': [hit_class]},
                     'html': {'enabled': False, 'apps': [{'class': 'yuque'}]}}
        self.assertEqual(apprules.active_flow(workflows, 'yuque', '语雀'), 'md')

    def test_apprules_parse_and_format_round_trip(self):
        from pastemd.utils import apprules
        text = '# 注释\n语雀 | yuque | 语雀\n纯标题 || Notion\n'
        apps = apprules.parse_rules(text)
        self.assertEqual(apps, [
            {'name': '语雀', 'class': 'yuque', 'window_patterns': ['语雀']},
            {'name': '纯标题', 'window_patterns': ['Notion']}])
        self.assertEqual(apprules.parse_rules(apprules.format_rules(apps)), apps)

    def test_text_clipboard_payload_formats(self):
        payload = cli.text_clipboard_payload('正文'.encode(), b'ast', 'markdown', 'md')
        self.assertEqual(payload, {'text/plain': '正文'.encode()})
        html_doc = b'<p>hello</p>'
        payload = cli.text_clipboard_payload(html_doc, b'ast', 'html', 'html')
        self.assertEqual(payload, {'text/html': html_doc})
        with patch.object(cli, 'run', return_value=b'latex out') as run:
            payload = cli.text_clipboard_payload(b'x', b'ast', 'markdown+tex', 'latex')
        self.assertEqual(payload['text/plain'], b'latex out')
        self.assertEqual(run.call_args.args[0][-2:], ['--to', 'latex'])
        self.assertEqual([Path(run.call_args.args[0][0]).name] + run.call_args.args[0][1:4], ['pandoc', '--from', 'json', '--to'])
        with patch.object(cli, 'run', return_value=b'md out') as run:
            payload = cli.text_clipboard_payload(b'<p>x</p>', b'ast', 'html', 'md')
        self.assertEqual(payload['text/plain'], b'md out')
        self.assertEqual([Path(run.call_args.args[0][0]).name] + run.call_args.args[0][1:],
                         ['pandoc', '--from', 'json', '--to', 'gfm-raw_html',
                          '--wrap', 'none'])
        with self.assertRaisesRegex(RuntimeError, '未知的目标格式'):
            cli.text_clipboard_payload(b'x', b'ast', 'markdown', 'rtf')

    @unittest.skipUnless(shutil.which('pandoc'), '需要系统 pandoc')
    def test_md_text_flow_task_lists_math_and_wrapping(self):
        html = (b'<ul><li><span>[x]</span> done item</li>'
                b'<li><span>[ ]</span> todo item</li></ul>'
                b'<p>math $x^2+y^2$ end</p>')
        reader = 'html' + cli.MATH_EXTENSIONS
        ast = cli.prepare_document(html, reader, {}, protect_task_lists=True)
        text = cli.text_clipboard_payload(html, ast, reader, 'md')['text/plain'].decode()
        self.assertIn('- [x] done item', text)
        self.assertIn('- [ ] todo item', text)
        self.assertNotIn('\\[', text)
        self.assertIn('$x^2+y^2$', text)
        self.assertNotIn('$`', text)
        # docx 流程不带保护：AST 中不出现占位符
        ast_doc = json.loads(cli.prepare_document(html, reader, {}))
        self.assertNotIn('PASTEMD_TASK', json.dumps(ast_doc))

    def test_cli_as_flag_writes_plain_text(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), \
             patch.object(cli.shutil, 'which', return_value='/bin/tool'), \
             patch.object(cli, 'read_clipboard', return_value=('# 标题\n'.encode(), 'markdown')), \
             patch.object(cli, 'notify') as notify, \
             patch.object(cli, 'set_clipboard_payload') as clipboard:
            self.assertEqual(cli.main(['--as', 'md'], options={}), 0)
        payload = clipboard.call_args.args[0]
        self.assertEqual(payload['text/plain'], '# 标题\n'.encode())
        self.assertIn('Markdown', notify.call_args.args[0])

    def test_md_normalizer_adds_blank_lines_between_blocks(self):
        from pastemd.utils.md_normalizer import normalize_markdown
        text = '# 标题\n正文段落\n## 二级\n- 列表项\n| a | b |\n|---|---|\n| 1 | 2 |'
        fixed = normalize_markdown(text)
        lines = fixed.split('\n')
        self.assertEqual(lines[0], '# 标题')
        self.assertEqual(lines[1], '')
        self.assertEqual(lines[2], '正文段落')
        self.assertIn('\n## 二级\n\n- 列表项', fixed)
        self.assertIn('\n| a | b |', fixed)
        # 代码块内部保持原样
        code = '段落\n```\nline1\nline2\n```\n结尾'
        self.assertEqual(normalize_markdown(code),
                         '段落\n\n```\nline1\nline2\n```\n\n结尾')

    def test_extract_font_classes_from_style_blocks(self):
        html = ('<style>.b{font-weight:bold}.w{font-weight:600}.i{font-style:italic}'
                '.n{font-weight:normal}.u{color:red}.chat{white-space:pre-wrap}</style>')
        bold, italic, prewrap = cli.extract_font_classes(html)
        self.assertEqual(bold, ['b', 'w'])
        self.assertEqual(italic, ['i'])
        self.assertEqual(prewrap, ['chat'])

    @unittest.skipUnless(shutil.which('pandoc'), '需要系统 pandoc')
    def test_prewrap_newlines_restored(self):
        html = (b'<style>.chat{white-space:pre-wrap}</style>'
                b'<div class="chat">line one\nline two</div>'
                b'<div style="white-space:pre-wrap">l1\nl2</div>')
        ast = json.loads(cli.prepare_document(html, 'html', {}))
        self.assertEqual(json.dumps(ast).count('LineBreak'), 2)
        self.assertNotIn('SoftBreak', json.dumps(ast))
        # 开关关闭时保持原 SoftBreak 行为
        ast_off = json.loads(cli.prepare_document(
            html, 'html', {'html_formatting': {'preserve_prewrap_newlines': False}}))
        self.assertEqual(json.dumps(ast_off).count('SoftBreak'), 2)

    @unittest.skipUnless(shutil.which('pandoc'), '需要系统 pandoc')
    def test_obsidian_math_spans_restored(self):
        html = (b'<p><span class="math math-inline">\\(x^2+y^2\\)</span> ok '
                b'<span class="math math-block">\\[E=mc^2\\]</span></p>')
        ast = json.loads(cli.prepare_document(html, 'html', {}))
        blob = json.dumps(ast)
        self.assertIn('InlineMath', blob)
        self.assertIn('x^2+y^2', blob)
        self.assertIn('DisplayMath', blob)
        self.assertIn('E=mc^2', blob)
        # 正文里的普通 span 不受影响
        self.assertIn('ok', blob)

    def test_app_presets_defined(self):
        from pastemd.utils import apprules
        self.assertIn(('语雀', 'yuque'), apprules.APP_PRESETS)
        for name, resource_class in apprules.APP_PRESETS:
            self.assertTrue(name and resource_class)

    @unittest.skipUnless(shutil.which('pandoc') or os.environ.get('PASTEMD_PANDOC_BIN'),
                         '需要系统 pandoc')
    def test_lua_golden_fixtures(self):
        """黄金测试：锁住 Lua 过滤器 + 转换链的行为，防 pandoc/过滤器回归。

        期望输出由 tests/update_fixtures.py 生成；有意变更行为后重新生成，
        并用 git diff 审查变化。黄金只钉与 vendored 一致的**官方** pandoc
        3.7.0.2：发行版重打包（如 Fedora 会 backport 后续 AST 变更，api
        [1,23,1,1]）与官方（[1,23,1]）输出不同，遇到发行版版本会跳过——
        设 PASTEMD_PANDOC_BIN 指向官方二进制即可运行。
        """
        pandoc = cli.pandoc_bin()
        api = json.loads(subprocess.run(
            [pandoc, '-f', 'html', '-t', 'json'], input='<p>x</p>',
            capture_output=True, text=True).stdout)['pandoc-api-version']
        if api != [1, 23, 1]:
            self.skipTest(f'pandoc api-version {api} 与官方 3.7.0.2 不符'
                          '（发行版补丁版）；设 PASTEMD_PANDOC_BIN 指向官方二进制')
        fixtures = Path(__file__).parent / 'fixtures'
        cases = [
            ('ai_answer', ''),
            ('obsidian_math', ''),
            ('prewrap', ''),
            ('task_lists', '.default'),
            ('task_lists', '.protect'),
        ]
        for name, suffix in cases:
            with self.subTest(fixture=name + suffix):
                html = (fixtures / f'{name}.html').read_bytes()
                kwargs = {'protect_task_lists': True} if suffix == '.protect' else {}
                actual = json.loads(cli.prepare_document(
                    html, 'html' + cli.MATH_EXTENSIONS, {}, **kwargs))
                expected = json.loads(
                    (fixtures / f'{name}{suffix}.json').read_text(encoding='utf-8'))
                self.assertEqual(actual, expected)

    def test_prefer_plain_over_html_detection(self):
        plain = '# 标题\n\n- 列表一\n- 列表二\n\n```code```\n**加粗**'
        wrapped_html = '<span style="color:#333"># 标题</span><br><span>- 列表一</span>'
        self.assertTrue(cli.prefer_plain_over_html(plain, wrapped_html))
        structural = '<h1>标题</h1><ul><li>列表</li></ul>'
        self.assertFalse(cli.prefer_plain_over_html(plain, structural))
        self.assertFalse(cli.prefer_plain_over_html('', wrapped_html))

    @unittest.skipUnless(shutil.which('pandoc'), '需要系统 pandoc')
    def test_prepare_document_semantic_html_recovery(self):
        html = (
            '<style>.fb{font-weight:bold}</style>'
            '<p><span class="fb">加粗文本</span></p>'
            '<p><span role="math" data-math-source="E=mc^2">katex</span></p>'
            '<p><img src="diagram.svg"></p>')
        ast = json.loads(cli.prepare_document(html.encode(), 'html', {}))
        # clean_document 会解开 span 包装，但 Strong 应该保留
        self.assertEqual(ast['blocks'][0]['c'][0]['t'], 'Strong')
        # 公式源恢复成 Math 元素（c = [类型, 源码]）
        math = ast['blocks'][1]['c'][0]
        self.assertEqual(math['t'], 'Math')
        self.assertEqual(math['c'][1], 'E=mc^2')
        # .svg 图片被整体移除
        self.assertNotIn('"Image"', json.dumps(ast))

    @unittest.skipUnless(shutil.which('pandoc'), '需要系统 pandoc')
    def test_prepare_document_bold_first_row_promotion(self):
        html = ('<table><tr><td><strong>列一</strong></td><td><strong>列二</strong></td></tr>'
                '<tr><td>a</td><td>b</td></tr></table>')
        options = {'html_formatting': {'css_font_to_semantic': True,
                                       'bold_first_row_to_header': True}}
        ast = json.loads(cli.prepare_document(html.encode(), 'html', options))
        head_rows = ast['blocks'][0]['c'][3][1]
        self.assertEqual(len(head_rows), 1)
        # 默认关闭：表头保持为空
        ast_default = json.loads(cli.prepare_document(html.encode(), 'html', {}))
        self.assertEqual(len(ast_default['blocks'][0]['c'][3][1]), 0)

    def test_read_clipboard_prefers_markdown_plain_text(self):
        types = b'text/html;charset=utf-8\ntext/plain;charset=utf-8\n'
        wrapped = '<span style="font-family:monospace"># 标题</span>'
        markdown = '# 标题\n\n- 甲\n- 乙\n- 丙\n\n```py\nx=1\n```\n**粗**'
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), \
             patch.object(cli.shutil, 'which', return_value='/bin/tool'), \
             patch.object(cli, 'run', side_effect=[
                 types, wrapped.encode(), markdown.encode()]) as run:
            content, reader = cli.read_clipboard('auto')
        self.assertEqual(reader, 'markdown' + cli.MATH_EXTENSIONS)
        self.assertEqual(content, markdown.encode())
        self.assertEqual(run.call_args_list[1].args[0][2], '--type')


if __name__ == '__main__':
    unittest.main()
