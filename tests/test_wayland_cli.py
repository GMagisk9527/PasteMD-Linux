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


    def test_default_writes_rtf_without_opening_wps(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'convert_to_rtf', return_value=b'rtf') as convert, patch.object(cli, 'run', return_value=b'plain'), patch.object(cli, 'notify'), patch.object(cli, 'set_rtf_clipboard') as clipboard, patch.object(cli.subprocess, 'Popen') as opener:
            self.assertEqual(cli.main([]), 0)
            clipboard.assert_called_once_with(b'rtf', b'plain')
            opener.assert_not_called()

    def test_failed_rtf_does_not_change_clipboard(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'hello', 'markdown')), patch.object(cli, 'prepare_document', return_value=b'{}'), patch.object(cli, 'convert_to_rtf', side_effect=RuntimeError('math lost')), patch.object(cli, 'notify'), patch.object(cli, 'set_rtf_clipboard') as clipboard:
            self.assertEqual(cli.main([]), 1)
            clipboard.assert_not_called()

    def test_rtf_payload_offers_native_and_mime_formats_without_html(self):
        payload = cli.clipboard_payload(b'rtf data', '中文'.encode())
        self.assertEqual(payload['Rich Text Format'], b'rtf data')
        self.assertEqual(payload['text/rtf'], b'rtf data')
        self.assertEqual(payload['text/plain'].decode(), '中文')
        self.assertNotIn('text/html', payload)

    def test_rtf_rejects_flattened_equations(self):
        import json
        from types import SimpleNamespace
        doc = json.dumps({'blocks': [{'t': 'Math', 'c': [{'t': 'InlineMath'}, 'a']}]}).encode()
        def converter(command, **kwargs):
            target = Path(command[-1]).with_suffix('.rtf')
            target.write_bytes(b'{\\rtf1 flattened}')
            return SimpleNamespace(returncode=0, stderr=b'', stdout=b'')
        with patch.object(cli, 'run'), patch.object(cli.subprocess, 'run', side_effect=converter):
            with self.assertRaisesRegex(RuntimeError, '0/1'):
                cli.convert_to_rtf(doc)

    @unittest.skipUnless(os.environ.get('PASTEMD_TEST_OFFICE') == '1', 'Set PASTEMD_TEST_OFFICE=1 for LibreOffice integration')
    def test_native_math_rtf_roundtrip(self):
        sample = '# 中文标题\n\n行内 $x^2$ 与中文。\n\n$$\\frac{a}{b}+\\sqrt[3]{x}=\\sum_{i=1}^{n} i$$\n\n|项目|值|\n|---|---|\n|中文|$a^2$|\n'
        prepared = cli.prepare_document(sample.encode(), 'markdown' + cli.MATH_EXTENSIONS)
        rtf = cli.convert_to_rtf(prepared)
        self.assertIn(b'\\moMath', rtf)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'roundtrip.rtf'
            source.write_bytes(rtf)
            cli.subprocess.run(['libreoffice', '-env:UserInstallation=' + (root / 'profile').as_uri(), '--headless', '--convert-to', 'docx', '--outdir', directory, str(source)], check=True, capture_output=True, timeout=60, env=dict(os.environ, SAL_USE_VCLPLUGIN='svp', GSETTINGS_BACKEND='memory'))
            with zipfile.ZipFile(root / 'roundtrip.docx') as archive:
                xml = ET.fromstring(archive.read('word/document.xml'))
            ns = {'m': 'http://schemas.openxmlformats.org/officeDocument/2006/math', 'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            self.assertEqual(len(xml.findall('.//m:oMath', ns)), 3)
            self.assertTrue(xml.findall('.//m:f', ns))
            self.assertTrue(xml.findall('.//m:rad', ns))
            self.assertTrue(xml.findall('.//m:nary', ns))
            self.assertTrue(xml.findall('.//w:tbl', ns))
            self.assertIn('中文', ''.join(xml.itertext()))

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
