#!/usr/bin/env python3
"""Experimental Fedora/Wayland entry point. Modified 2026-09-05, AGPL-3.0.

Uses desktop-managed shortcuts; does not synthesize keyboard events.
"""
import argparse
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET


MATH_EXTENSIONS = '+tex_math_dollars+tex_math_single_backslash+tex_math_double_backslash'


DEMO_MARKDOWN = r"""# PasteMD 公式粘贴测试

中文、**粗体**与行内公式 $x^2+y^2=z^2$。

$$\frac{a}{b}+\sqrt[3]{x}=\sum_{i=1}^{n} i$$

$$\begin{pmatrix}a & b \\ c & d\end{pmatrix}$$

| 项目 | 公式 |
|---|---|
| 判别式 | $\Delta=b^2-4ac$ |

请点击公式，检查能否编辑分子、根式和求和上下限。
"""


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


def math_count(value):
    if isinstance(value, dict):
        return int(value.get('t') == 'Math') + sum(math_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(map(math_count, value))
    return 0


def native_clipboard_payload(document, plain_text):
    """WPS exposes a DOCX ZIP under this native X11 clipboard format."""
    docx = run(['pandoc', '--from', 'json', '--to', 'docx', '--output', '-'], document)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    expected = math_count(json.loads(document))
    actual = len(root.findall('.//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath'))
    if actual < expected:
        raise RuntimeError(f'DOCX 仅保留 {actual}/{expected} 个原生公式，剪贴板未修改。')
    return {'Kingsoft WPS 9.0 Format': docx, 'text/plain': plain_text}


def serve_clipboard():
    """Detached XWayland clipboard owner. Data arrives privately over stdin."""
    try:
        from PySide6.QtCore import QMimeData, QTimer
        from PySide6.QtGui import QGuiApplication
        encoded = json.load(sys.stdin)
        payload = {key: base64.b64decode(value, validate=True) for key, value in encoded.items()}
        os.environ['QT_QPA_PLATFORM'] = 'xcb'
        app = QGuiApplication(['PasteMD clipboard'])
        clipboard = app.clipboard()
        mime = QMimeData()
        for kind, data in payload.items():
            mime.setData(kind, data)

        def publish():
            clipboard.setMimeData(mime)
            if not clipboard.ownsClipboard():
                print('ERROR:无法取得 XWayland 剪贴板所有权。', flush=True)
                app.exit(1)
                return
            print('READY', flush=True)

        # Keep ownership until the user copies something else. Never recapture it.
        timer = QTimer()
        timer.timeout.connect(lambda: app.quit() if not clipboard.ownsClipboard() else None)
        timer.start(1000)
        QTimer.singleShot(0, publish)
        return app.exec()
    except Exception as error:
        print('ERROR:' + str(error), flush=True)
        return 1


def set_clipboard_payload(payload):
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('WPS 原生粘贴需要 XWayland（DISPLAY），当前不可用。')
    if importlib.util.find_spec('PySide6') is None:
        raise RuntimeError('缺少 PySide6：sudo dnf install python3-pyside6')
    encoded = json.dumps({key: base64.b64encode(value).decode('ascii')
                          for key, value in payload.items()}).encode()
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), '--serve-clipboard'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env=dict(os.environ, QT_QPA_PLATFORM='xcb'),
    )
    try:
        process.stdin.write(encoded)
        process.stdin.close()
        if not select.select([process.stdout], [], [], 15)[0]:
            raise RuntimeError('XWayland 剪贴板服务启动超时。')
        reply = process.stdout.readline().decode('utf-8', 'replace').strip()
        if reply != 'READY':
            raise RuntimeError(reply or '剪贴板服务启动失败，请检查 PySide6 的 xcb 插件和 DISPLAY。')
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
    return process.pid


def main(argv=None):
    if argv is None and sys.argv[1:] == ['--serve-clipboard']:
        return serve_clipboard()
    parser = argparse.ArgumentParser(description='Fedora Wayland 剪贴板转换（实验版）')
    parser.add_argument('--demo', action='store_true', help='转换内置公式样本，用于验证 WPS 粘贴，无需先复制来源')
    parser.add_argument('--input', choices=['auto', 'markdown', 'html'], default='auto')
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--clipboard', action='store_true', help='写入 WPS 原生 DOCX 剪贴板（默认），随后在 WPS 按 Ctrl+V')
    output.add_argument('--docx', action='store_true', help='保存 DOCX，而不是替换剪贴板')
    output.add_argument('--open', action='store_true', help='用 WPS 打开生成的 DOCX（隐含 --docx）')
    args = parser.parse_args(argv)
    open_docx = args.open
    use_clipboard = not (args.docx or args.open)
    try:
        if not os.environ.get('WAYLAND_DISPLAY'):
            raise RuntimeError('请在 Wayland 桌面会话中运行。')
        required = ['pandoc'] + ([] if args.demo else ['wl-paste'])
        if open_docx:
            required.append('wps')
        missing = [tool for tool in required if not shutil.which(tool)]
        if missing:
            raise RuntimeError('缺少命令：' + ', '.join(missing) + '。转换依赖：sudo dnf install pandoc wl-clipboard python3-pyside6；WPS 需单独安装。')
        if args.demo:
            content = DEMO_MARKDOWN.encode('utf-8')
            reader = 'markdown' + MATH_EXTENSIONS
        else:
            content, reader = read_clipboard(args.input)
        if not content.strip():
            raise RuntimeError('剪贴板内容为空。')
        content = prepare_document(content, reader)
        command = ['pandoc', '--from', 'json']
        if not use_clipboard:
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
            message = 'DOCX 已保存：' + name
            notify(message)
            print(message)
        else:
            plain_text = run(command + ['--to', 'plain'], content)
            payload = native_clipboard_payload(content, plain_text)
            set_clipboard_payload(payload)
            message = '公式富文本已就绪，请在 WPS 的 .docx 文档中按 Ctrl+V。'
            notify(message)
            print(message)
        return 0
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, ET.ParseError, subprocess.TimeoutExpired) as error:
        message = str(error)
        print('PasteMD: ' + message, file=sys.stderr)
        notify(message)
        return 1


if __name__ == '__main__':
    sys.exit(main())
