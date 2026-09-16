import base64
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
        with patch.object(cli, 'run', side_effect=[b'text/plain\ntext/html\n', b'<p>Hello</p>']) as run:
            self.assertEqual(cli.read_clipboard('auto'), (b'<p>Hello</p>', 'html' + cli.MATH_EXTENSIONS))
            self.assertIn('text/html', run.call_args.args[0])

    def test_auto_accepts_html_type_with_charset(self):
        advertised = 'text/html;charset=utf-8'
        with patch.object(cli, 'run', side_effect=[
                ('text/plain\n' + advertised + '\n').encode(), b'<p>Hello</p>']) as run:
            content, reader = cli.read_clipboard('auto')
        self.assertEqual(content, b'<p>Hello</p>')
        self.assertEqual(reader, 'html' + cli.MATH_EXTENSIONS)
        self.assertEqual(run.call_args.args[0][-1], advertised)

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
                self.assertEqual(command[:3], ['pandoc', '--from', 'json'])
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
            '# Heading\n\n段落与 | 表格 | 列 |\n|---|---|\n| a | b |\n'.encode(),
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


if __name__ == '__main__':
    unittest.main()
