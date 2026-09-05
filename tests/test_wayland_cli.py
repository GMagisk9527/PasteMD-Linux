import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wayland_cli', Path(__file__).resolve().parents[1] / 'scripts/pastemd-wayland.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class WaylandTests(unittest.TestCase):
    def test_auto_prefers_html(self):
        with patch.object(cli, 'run', side_effect=[b'text/plain\ntext/html\n', b'<p>Hello</p>']) as run:
            self.assertEqual(cli.read_clipboard('auto'), (b'<p>Hello</p>', 'html'))
            self.assertIn('text/html', run.call_args.args[0])

    def test_markdown_override(self):
        with patch.object(cli, 'run', side_effect=[b'text/html\ntext/plain;charset=utf-8\n', b'# Hello']):
            self.assertEqual(cli.read_clipboard('markdown'), (b'# Hello', 'markdown+tex_math_dollars'))

    def test_conversion_failure_does_not_write_clipboard(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0'), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'run', side_effect=RuntimeError('conversion failed')), patch.object(cli, 'notify'), patch.object(cli.subprocess, 'run') as process:
            self.assertEqual(cli.main([]), 1)
            process.assert_not_called()

    def test_docx_output_preserves_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'run', return_value=b'') as run, patch.object(cli, 'notify'):
                self.assertEqual(cli.main(['--docx']), 0)
                command = run.call_args.args[0]
                self.assertEqual(command[:5], ['pandoc', '--from', 'markdown', '--to', 'docx'])
                self.assertTrue(Path(command[-1]).is_file())

    def test_failed_docx_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-0', XDG_CACHE_HOME=directory), patch.object(cli.shutil, 'which', return_value='/bin/tool'), patch.object(cli, 'read_clipboard', return_value=(b'# Hello', 'markdown')), patch.object(cli, 'run', side_effect=RuntimeError('conversion failed')), patch.object(cli, 'notify'):
                self.assertEqual(cli.main(['--docx']), 1)
                self.assertEqual(list((Path(directory) / 'pastemd').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
