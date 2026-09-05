import importlib.util
import os
from pathlib import Path
import tempfile
import shutil
import zipfile
import xml.etree.ElementTree as ET
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wayland_cli', Path(__file__).resolve().parents[1] / 'scripts/pastemd-wayland.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class WaylandTests(unittest.TestCase):
    def test_auto_prefers_html(self):
        with patch.object(cli, 'run', side_effect=[b'text/plain\ntext/html\n', b'<p>Hello</p>']) as run:
            self.assertEqual(cli.read_clipboard('auto'), (b'<p>Hello</p>', 'html' + cli.MATH_EXTENSIONS))
            self.assertIn('text/html', run.call_args.args[0])

    def test_markdown_override(self):
        with patch.object(cli, 'run', side_effect=[b'text/html\ntext/plain;charset=utf-8\n', b'# Hello']):
            self.assertEqual(cli.read_clipboard('markdown'), (b'# Hello', 'markdown' + cli.MATH_EXTENSIONS))

    def test_conversion_failure_does_not_write_clipboard(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'run', side_effect=RuntimeError('conversion failed')), patch.object(cli, 'notify'), patch.object(cli.subprocess, 'run') as process:
            self.assertEqual(cli.main([]), 1)
            process.assert_not_called()

    def test_docx_output_preserves_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'run', return_value=b'') as run, patch.object(cli, 'notify'):
                self.assertEqual(cli.main(['--docx']), 0)
                command = run.call_args.args[0]
                self.assertEqual(command[:5], ['pandoc', '--from', 'json', '--to', 'docx'])
                self.assertTrue(Path(command[-1]).is_file())

    def test_failed_docx_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'run', side_effect=RuntimeError('conversion failed')), patch.object(cli, 'notify'):
                self.assertEqual(cli.main(['--docx']), 1)
                self.assertEqual(list((Path(directory) / 'pastemd').iterdir()), [])


    def test_default_opens_docx_without_clipboard_write(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'run', return_value=b'') as run, patch.object(cli, 'notify'), patch.object(cli.subprocess, 'Popen') as opener:
                self.assertEqual(cli.main([]), 0)
                self.assertEqual(opener.call_args.args[0][0], 'wps')
                self.assertEqual(run.call_args.args[0][-2], '--output')

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


if __name__ == '__main__':
    unittest.main()
