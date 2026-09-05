#!/usr/bin/env python3
"""Experimental Fedora/Wayland entry point. Modified 2026-09-05, AGPL-3.0.

Uses desktop-managed shortcuts; does not synthesize keyboard events.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


MATH_EXTENSIONS = '+tex_math_dollars+tex_math_single_backslash+tex_math_double_backslash'


def clean_document(value):
    """Drop browser layout wrappers while retaining semantic text, tables and math."""
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict) and item.get('t') in ('Span', 'Div'):
                if 'katex-html' not in item['c'][0][1]:
                    result.extend(clean_document(item['c'][1]))
            elif isinstance(item, dict) and item.get('t') in ('RawInline', 'RawBlock') and item['c'][0] == 'html':
                continue
            else:
                result.append(clean_document(item))
        return result
    if isinstance(value, dict):
        return {key: clean_document(item) for key, item in value.items()}
    return value


def prepare_document(content, reader):
    document = json.loads(run(['pandoc', '--from', reader, '--to', 'json'], content))
    document = clean_document(document)
    document['meta'] = {}
    return json.dumps(document, ensure_ascii=False).encode('utf-8')


def run(command, data=None):
    result = subprocess.run(command, input=data, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip()
                           or f'{command[0]} failed ({result.returncode})')
    return result.stdout


def read_clipboard(input_format):
    types = run(['wl-paste', '--list-types']).decode().splitlines()
    if input_format != 'markdown' and 'text/html' in types:
        return run(['wl-paste', '--no-newline', '--type', 'text/html']), 'html' + MATH_EXTENSIONS
    if input_format == 'html':
        raise RuntimeError('剪贴板没有 text/html；请复制网页正文或使用 --input markdown。')
    plain = next((t for t in types if t.lower().startswith('text/plain')), None)
    if plain is None:
        raise RuntimeError('剪贴板没有文本。请先复制 Markdown 或网页正文。')
    return run(['wl-paste', '--no-newline', '--type', plain]), 'markdown' + MATH_EXTENSIONS


def notify(message):
    if shutil.which('notify-send'):
        try:
            run(['notify-send', 'PasteMD', message])
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fedora Wayland 剪贴板转换（实验版）')
    parser.add_argument('--input', choices=['auto', 'markdown', 'html'], default='auto')
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--clipboard', action='store_true', help='实验性 HTML 剪贴板输出，WPS 公式兼容性不保证')
    output.add_argument('--docx', action='store_true', help='保存 DOCX，而不是替换剪贴板')
    output.add_argument('--open', action='store_true', help='用 WPS 打开生成的 DOCX（隐含 --docx）')
    args = parser.parse_args(argv)
    open_docx = args.open or not (args.docx or args.clipboard)
    try:
        if not os.environ.get('WAYLAND_DISPLAY'):
            raise RuntimeError('请在 Wayland 桌面会话中运行。')
        required = ['wl-paste', 'pandoc'] + (['wl-copy'] if args.clipboard else [])
        if open_docx:
            required.append('wps')
        missing = [tool for tool in required if not shutil.which(tool)]
        if missing:
            raise RuntimeError('缺少命令：' + ', '.join(missing) + '。Fedora 依赖：sudo dnf install pandoc wl-clipboard')
        content, reader = read_clipboard(args.input)
        if not content.strip():
            raise RuntimeError('剪贴板内容为空。')
        content = prepare_document(content, reader)
        command = ['pandoc', '--from', 'json']
        if not args.clipboard:
            cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'pastemd'
            cache.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(suffix='.docx', prefix='paste-', dir=cache)
            os.close(fd)
            try:
                run(command + ['--to', 'docx', '--output', name], content)
            except Exception:
                Path(name).unlink(missing_ok=True)
                raise
            print(name)
            if open_docx:
                subprocess.Popen(['wps', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            message = 'DOCX 已保存；请从 WPS 打开的文档复制内容到目标文档：' + name
            notify(message)
            print(message)
        else:
            converted = run(command + ['--to', 'html' + MATH_EXTENSIONS, '--standalone', '--mathml',
                                       '--metadata', 'title=PasteMD'], content)
            # wl-copy forks a clipboard owner; DEVNULL avoids inherited captured pipes.
            result = subprocess.run(['wl-copy', '--type', 'text/html'], input=converted,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if result.returncode:
                raise RuntimeError('写入 Wayland 剪贴板失败。')
            notify('富文本已就绪，请在 WPS 中按 Ctrl+V；公式可使用 DOCX 模式。')
            print('富文本已就绪，请在 WPS 中按 Ctrl+V。')
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        message = str(error)
        print('PasteMD: ' + message, file=sys.stderr)
        notify(message)
        return 1


if __name__ == '__main__':
    sys.exit(main())
