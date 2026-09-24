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
import re
import select
import time
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET

# 直接以脚本方式运行时(--serve-clipboard 子进程、手工执行)补上仓库根目录，
# 让下面的包内绝对导入可用。
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pastemd.utils.docx_processor import DocxProcessor
from pastemd.utils.latex import convert_latex_delimiters
from pastemd.utils.md_normalizer import normalize_markdown
from pastemd.utils.spreadsheet import parse_markdown_table, table_to_html, table_to_tsv


MATH_EXTENSIONS = '+tex_math_dollars+tex_math_single_backslash+tex_math_double_backslash'

# 转换结果附带的私有 MIME：热键再次触发时用来识别「这是我们写进去的」，
# 并取出原文，避免把 DOCX/表格结果再当 Markdown 源。
CLIPBOARD_TOKEN_MIME = 'application/x-pastemd-conversion-id'
CLIPBOARD_SOURCE_MIME = 'application/x-pastemd-source'
CLIPBOARD_READER_MIME = 'application/x-pastemd-source-reader'
CLIPBOARD_FLOW_MIME = 'application/x-pastemd-flow'


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


def lua_filter(name):
    """Path of a bundled Lua filter, covering source runs and PyInstaller bundles."""
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        candidate = Path(bundle) / 'lua' / name
        if candidate.is_file():
            return str(candidate)
    return str(Path(__file__).resolve().parents[1] / 'lua' / name)


def load_conversion_options():
    """Conversion settings from settings.json; empty dict when unavailable."""
    try:
        from pastemd.linux.settings import load_settings
        return load_settings()
    except Exception:
        return {}


CSS_CLASS_RE = re.compile(r'\.([A-Za-z_][\w-]*)\s*\{([^}]*)\}')
FONT_WEIGHT_RE = re.compile(r'font-weight\s*:\s*([^;}]+)')
FONT_STYLE_RE = re.compile(r'font-style\s*:\s*([^;}]+)')
WHITE_SPACE_RE = re.compile(r'white-space\s*:\s*([^;}]+)')
STRUCTURAL_TAG_RE = re.compile(
    r'<(p|h[1-6]|ul|ol|table|pre|blockquote|code)[\s>]', re.IGNORECASE)

# 与上游 PasteMD html_analyzer 相同的 Markdown 特征清单。
MARKDOWN_HINTS = (
    '\n#', '\n##', '\n- ', '\n* ', '\n1.', '```', '**', '__',
    '~~', '> ', '$$', '\\(', '\\)', '|', '\n---', '\n***', '`',
)


def extract_font_classes(html_text):
    """从 <style> 块提取加粗/斜体/pre-wrap class 名单（pandoc 不读样式表）。"""
    bold, italic, prewrap = [], [], []
    for match in CSS_CLASS_RE.finditer(html_text):
        body = match.group(2).lower()
        weight = FONT_WEIGHT_RE.search(body)
        if weight:
            value = weight.group(1).strip()
            if value in ('bold', 'bolder') or (
                    value.isdigit() and int(value) >= 600):
                bold.append(match.group(1))
        style = FONT_STYLE_RE.search(body)
        if style and ('italic' in style.group(1) or 'oblique' in style.group(1)):
            italic.append(match.group(1))
        space = WHITE_SPACE_RE.search(body)
        if space and 'pre-wrap' in space.group(1):
            prewrap.append(match.group(1))
    return bold, italic, prewrap


def markdown_hint_score(text):
    """按上游 html_analyzer 的规则对 Markdown 语法特征粗略打分。"""
    return sum(1 for hint in MARKDOWN_HINTS if hint in text)


def prefer_plain_over_html(plain_text, html_text):
    """剪贴板同时带 text/html 与 text/plain 时，判断是否该走 Markdown 流程。

    复制按钮、VSCode 等常把带内联样式的文本包装成 HTML；这时应使用原始
    Markdown 文本，而不是丢掉语法标记的样式包装。
    """
    if not plain_text.strip():
        return False
    if STRUCTURAL_TAG_RE.search(html_text or ''):
        return False
    return markdown_hint_score(plain_text) >= 3


def _filter_args(options):
    args = []
    for path in options.get('pandoc_filters') or []:
        path = str(path)
        args += ['--lua-filter' if path.lower().endswith('.lua') else '--filter', path]
    return args


def conversion_type(reader, target):
    """对齐上游的转换类型名：md/html 为源，docx/md/latex/html 为目标。"""
    source = 'md' if reader.startswith('markdown') else 'html'
    return f'{source}_to_{target}'


def _conversion_filter_args(conversion, options):
    """按转换类型配置的过滤器（上游 pandoc_filters_by_conversion）。"""
    filters = (options or {}).get('pandoc_filters_by_conversion') or {}
    if not isinstance(filters, dict):
        return []
    entries = filters.get(conversion) or []
    if isinstance(entries, str):
        entries = [entries]
    if not isinstance(entries, (list, tuple)):
        return []
    args = []
    for path in entries:
        path = str(path)
        if path.strip():
            args += ['--lua-filter' if path.lower().endswith('.lua') else '--filter', path]
    return args


def _stage_filter_args(reader, options, conversion=None):
    """Lua filters mirroring the upstream PasteMD conversion chain."""
    args = []
    if options.get('enable_latex_replacements', True):
        args += ['--lua-filter', lua_filter('latex-replacements.lua')]
    if reader.startswith('markdown'):
        args += ['--lua-filter', lua_filter('normalize-markdown-breaks.lua')]
    if options.get('keep_original_formula'):
        args += ['--lua-filter', lua_filter('keep-latex-math.lua')]
    args = args + _filter_args(options)
    if conversion:
        args += _conversion_filter_args(conversion, options)
    return args


def docx_writer_args(options):
    """Args appended to `pandoc --from json` DOCX conversions."""
    options = options or {}
    args = []
    style = options.get('code_highlight_style') or 'default'
    if style == 'default':
        style = 'tango'  # 历史行为：默认即 tango 配色
    if style != 'none':
        args += ['--highlight-style', style]
    reference = options.get('reference_docx')
    if reference:
        args += ['--reference-doc', str(reference)]
    for header in options.get('pandoc_request_headers') or []:
        args += ['--request-header', str(header)]
    return args


def finish_docx(docx, reader, options=None):
    """Upstream DOCX post-processing: indent style, rules, table layout."""
    options = options or {}
    indent = ('md_disable_first_para_indent' if reader.startswith('markdown')
              else 'html_disable_first_para_indent')
    return DocxProcessor.apply_custom_processing(
        docx,
        disable_first_para_indent=bool(options.get(indent, True)),
        horizontal_rule_style=options.get('horizontal_rule_style', 'default'),
        auto_layout_tables=bool(options.get('docx_auto_table_layout', False)),
    )


def docx_output_path(options=None):
    """DOCX 落盘路径：save_dir 优先且带时间戳文件名，否则沿用缓存临时文件。

    save_dir 不存在时会自动创建（失败则回退缓存目录，不让保存目录配置
    阻塞转换）。
    """
    options = options or {}
    save_dir = options.get('save_dir')
    if save_dir:
        target = Path(save_dir).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            target = None
        if target is not None:
            stamp = time.strftime('%Y%m%d-%H%M%S')
            return target / f'pastemd-{stamp}-{uuid.uuid4().hex}.docx'
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'pastemd'
    cache.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix='.docx', prefix='paste-', dir=cache)
    os.close(fd)
    return Path(name)


def prepare_document(content, reader, options=None, protect_task_lists=False,
                     conversion=None):
    options = options or {}
    env = None
    if reader.startswith('markdown'):
        text = content.decode('utf-8', 'replace')
        text = normalize_markdown(text)
        text = convert_latex_delimiters(
            text, bool(options.get('fix_single_dollar_block', True)))
        content = text.encode('utf-8')
        if options.get('markdown_hard_line_breaks'):
            reader += '+hard_line_breaks'
    else:
        html_text = content.decode('utf-8', 'replace')
        bold_classes, italic_classes, prewrap_classes = \
            extract_font_classes(html_text)
        formatting = options.get('html_formatting') or {}
        env = dict(os.environ)
        if bold_classes and formatting.get('css_font_to_semantic', True):
            env['PASTEMD_FONT_BOLD_CLASSES'] = ','.join(bold_classes)
        if italic_classes and formatting.get('css_font_to_semantic', True):
            env['PASTEMD_FONT_ITALIC_CLASSES'] = ','.join(italic_classes)
        if formatting.get('bold_first_row_to_header'):
            env['PASTEMD_PROMOTE_BOLD_HEADER'] = 'true'
        # pre-wrap 块的源码换行还原为硬换行（聊天/代码 UI 复制场景）
        if formatting.get('preserve_prewrap_newlines', True):
            env['PASTEMD_PRESERVE_PREWRAP'] = '1'
            if prewrap_classes:
                env['PASTEMD_PREWRAP_CLASSES'] = ','.join(prewrap_classes)
        # 任务列表 [x]/[ ] 占位保护：仅用于转 Markdown 文本的目标，避免
        # gfm writer 把方括号转义成 \[x]；docx 流程保持原样
        if protect_task_lists:
            env['PASTEMD_PROTECT_TASKS'] = '1'
    args = [pandoc_bin(), '--from', reader, '--to', 'json']
    if env is not None:
        args += ['--lua-filter', lua_filter('semantic-html.lua')]
    document = json.loads(run(
        args + _stage_filter_args(reader, options, conversion), content, env=env))
    document = clean_document(document)
    document['meta'] = {}
    return json.dumps(document, ensure_ascii=False).encode('utf-8')


def pandoc_bin():
    """pandoc 可执行路径；PASTEMD_PANDOC_BIN 可覆盖（测试/多版本场景）。"""
    return os.environ.get('PASTEMD_PANDOC_BIN') or 'pandoc'


def run(command, data=None, env=None):
    result = subprocess.run(command, input=data, capture_output=True,
                            timeout=60, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip()
                           or f'{command[0]} failed ({result.returncode})')
    return result.stdout


def clipboard_types():
    return [kind.strip() for kind in
            run(['wl-paste', '--list-types']).decode('utf-8', 'replace').splitlines()
            if kind.strip()]


def _type_named(types, name):
    want = name.lower()
    return next((kind for kind in types
                 if kind.partition(';')[0].strip().lower() == want), None)


def stamp_conversion(payload, source, reader='', flow='doc', token=None):
    """把原文和流程标记进剪贴板载荷，便于再次热键时复用或按新流程重转。"""
    token = token or uuid.uuid4().hex.encode('ascii')
    if isinstance(source, str):
        source = source.encode('utf-8')
    payload[CLIPBOARD_TOKEN_MIME] = token
    payload[CLIPBOARD_SOURCE_MIME] = source or b''
    payload[CLIPBOARD_READER_MIME] = (reader or '').encode('utf-8')
    payload[CLIPBOARD_FLOW_MIME] = (flow or '').encode('utf-8')
    return payload, token


def read_embedded_source(types=None):
    """若剪贴板仍是上次转换结果，返回 (原文, reader, flow)；否则 None。"""
    try:
        types = clipboard_types() if types is None else types
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    if not _type_named(types, CLIPBOARD_TOKEN_MIME):
        return None
    source_type = _type_named(types, CLIPBOARD_SOURCE_MIME)
    if not source_type:
        return None
    source = run(['wl-paste', '--no-newline', '--type', source_type])
    reader = ''
    flow = ''
    reader_type = _type_named(types, CLIPBOARD_READER_MIME)
    if reader_type:
        reader = run(['wl-paste', '--no-newline', '--type', reader_type]).decode('utf-8', 'replace')
    flow_type = _type_named(types, CLIPBOARD_FLOW_MIME)
    if flow_type:
        flow = run(['wl-paste', '--no-newline', '--type', flow_type]).decode('utf-8', 'replace')
    return source, reader, flow


def clipboard_holds_conversion(types=None):
    try:
        types = clipboard_types() if types is None else types
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False
    return bool(_type_named(types, CLIPBOARD_TOKEN_MIME))


def read_clipboard(input_format):
    types = clipboard_types()
    embedded = read_embedded_source(types)
    if embedded is not None:
        source, reader, _flow = embedded
        if source.strip():
            return source, reader or ('markdown' + MATH_EXTENSIONS)
        raise RuntimeError('剪贴板仍是上次转换结果。请重新复制 Markdown 或网页正文后再转换。')
    if clipboard_holds_conversion(types):
        raise RuntimeError('剪贴板仍是上次转换结果。请重新复制 Markdown 或网页正文后再转换。')
    html = next((kind for kind in types
                 if kind.partition(';')[0].strip().lower() == 'text/html'), None)
    if input_format != 'markdown' and html:
        html_bytes = run(['wl-paste', '--no-newline', '--type', html])
        if input_format == 'auto':
            plain = next((t for t in types if t.lower().startswith('text/plain')), None)
            if plain:
                plain_text = run(['wl-paste', '--no-newline', '--type', plain]) \
                    .decode('utf-8', 'replace')
                if prefer_plain_over_html(
                        plain_text, html_bytes.decode('utf-8', 'replace')):
                    return plain_text.encode('utf-8'), 'markdown' + MATH_EXTENSIONS
        return html_bytes, 'html' + MATH_EXTENSIONS
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


def read_table_source():
    """Clipboard text for table parsing; converts HTML tables via Pandoc when needed."""
    types = clipboard_types()
    embedded = read_embedded_source(types)
    if embedded is not None:
        source, _reader, _flow = embedded
        if source.strip():
            return source.decode('utf-8', 'replace')
        raise RuntimeError('剪贴板仍是上次转换结果。请重新复制 Markdown 表格后再转换。')
    if clipboard_holds_conversion(types):
        raise RuntimeError('剪贴板仍是上次转换结果。请重新复制 Markdown 表格后再转换。')
    plain = next((t for t in types if t.lower().startswith('text/plain')), None)
    plain_text = (run(['wl-paste', '--no-newline', '--type', plain]).decode('utf-8', 'replace')
                  if plain else None)
    if plain_text is not None and parse_markdown_table(plain_text):
        return plain_text
    html = next((kind for kind in types
                 if kind.partition(';')[0].strip().lower() == 'text/html'), None)
    if html:
        raw = run(['wl-paste', '--no-newline', '--type', html])
        if b'<table' in raw.lower():
            return run([pandoc_bin(), '--from', 'html', '--to', 'gfm'], raw).decode('utf-8', 'replace')
    if plain_text is not None:
        return plain_text
    raise RuntimeError('剪贴板没有文本，无法识别表格。请复制 Markdown 表格或网页表格。')


def table_clipboard_payload(markdown_text):
    """Build the text/html + text/plain payload for WPS 表格, with row count."""
    table = parse_markdown_table(markdown_text)
    if not table:
        raise RuntimeError('剪贴板中没有 Markdown 表格。请复制带 |---| 分隔符的表格，'
                           '或包含表格的网页内容。')
    payload = {'text/html': table_to_html(table).encode('utf-8'),
               'text/plain': table_to_tsv(table).encode('utf-8')}
    return payload, len(table)


DEMO_TABLE_MARKDOWN = """| 项目 | 公式 |
|---|---|
| 判别式 | $\\Delta=b^2-4ac$ |
"""


TEXT_FORMAT_LABELS = {'md': 'Markdown', 'latex': 'LaTeX', 'html': 'HTML'}


def text_clipboard_label(target_format):
    return TEXT_FORMAT_LABELS.get(target_format, target_format)


def text_clipboard_payload(raw, document, reader, target_format, options=None):
    """应用扩展流程：把内容转成 Markdown/LaTeX/HTML 纯文本剪贴板载荷。

    raw 是剪贴板原始字节，document 是 prepare_document 产出的 pandoc JSON AST。
    """
    options = options or {}
    if target_format == 'md':
        if reader.startswith('markdown'):
            # 原文直通，仅按用户设置套用公式分隔符修复，
            # 避免 Pandoc 重排用户的 Markdown。
            text = convert_latex_delimiters(
                raw.decode('utf-8', 'replace'),
                bool(options.get('fix_single_dollar_block', True)))
            return {'text/plain': text.encode('utf-8')}
        # gfm-raw_html 剥离残留 HTML，--wrap none 不做 72 列硬折行
        # （对齐上游 _convert_html_to_md；tex_math_dollars 对 gfm writer 无效，
        # 其数学固定输出 GitLab 风格的 $`…`，下方统一还原为通用性更好的 $…$）
        text = run([pandoc_bin(), '--from', 'json', '--to', 'gfm-raw_html',
                    '--wrap', 'none'], document).decode('utf-8', 'replace')
        text = text.replace('$`', '$').replace('`$', '$')
        # 任务列表占位还原（保护开关开启时由 Lua 写入，其余情况无占位符）
        text = text.replace('{{PASTEMD_TASK_CHECKED}}', '[x]') \
                   .replace('{{PASTEMD_TASK_UNCHECKED}}', '[ ]')
        return {'text/plain': text.encode('utf-8')}
    if target_format == 'latex':
        text = run([pandoc_bin(), '--from', 'json', '--to', 'latex'],
                   document).decode('utf-8', 'replace')
        return {'text/plain': text.encode('utf-8')}
    if target_format == 'html':
        if reader.startswith('html'):
            return {'text/html': raw}
        text = run([pandoc_bin(), '--from', 'json', '--to', 'html'],
                   document).decode('utf-8', 'replace')
        return {'text/html': text.encode('utf-8')}
    raise RuntimeError(f'未知的目标格式：{target_format}')


def open_in_wps(path):
    """Open a generated document in host WPS from source, AppImage or Flatpak."""
    if os.environ.get('FLATPAK_ID'):
        command = ['flatpak-spawn', '--host', 'wps', str(path)]
        missing = 'Flatpak 缺少 flatpak-spawn，DOCX 已保存但无法打开 WPS。'
    else:
        command = ['wps', str(path)]
        missing = '找不到 WPS 命令，DOCX 已保存但无法自动打开。'
    if not shutil.which(command[0]):
        raise RuntimeError(missing)
    return subprocess.Popen(command, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def math_count(value):
    if isinstance(value, dict):
        return int(value.get('t') == 'Math') + sum(math_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(map(math_count, value))
    return 0


def image_urls(value):
    """Distinct image sources in a Pandoc AST. Pandoc dedupes identical
    media in DOCX, so counting sources (not nodes) matches its output."""
    found = set()
    if isinstance(value, dict):
        if value.get('t') == 'Image' and len(value.get('c', [])) > 2:
            found.add(value['c'][2][0])
        for item in value.values():
            found |= image_urls(item)
    elif isinstance(value, list):
        for item in value:
            found |= image_urls(item)
    return found


def lost_image_count(document, docx):
    """Images the document references but the DOCX media store lacks."""
    expected = image_urls(json.loads(document))
    if not expected:
        return 0
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        embedded = sum(1 for name in archive.namelist()
                       if name.startswith('word/media/'))
    return max(0, len(expected) - embedded)


def native_clipboard_payload(document, plain_text, options=None, reader='markdown'):
    """WPS exposes a DOCX ZIP under this native X11 clipboard format."""
    docx = run([pandoc_bin(), '--from', 'json', '--to', 'docx', '--output', '-']
               + docx_writer_args(options), document)
    docx = finish_docx(docx, reader, options)
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


def clipboard_handshake(process, encoded, timeout=15):
    """Bound both pipe writes and partial replies by one monotonic deadline."""
    writer, reader = process.stdin.fileno(), process.stdout.fileno()
    os.set_blocking(writer, False)
    os.set_blocking(reader, False)
    deadline = time.monotonic() + timeout
    pending = memoryview(encoded)
    reply = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('XWayland 剪贴板服务启动超时。')
        readable, writable, _ = select.select([reader], [writer] if pending else [], [], remaining)
        if writable:
            try:
                pending = pending[os.write(writer, pending[:65536]):]
            except BlockingIOError:
                continue
            if not pending:
                process.stdin.close()
        if readable:
            try:
                chunk = os.read(reader, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                raise RuntimeError('剪贴板服务启动失败：连接已关闭。')
            reply.extend(chunk)
            if len(reply) > 4096:
                raise RuntimeError('剪贴板服务返回了无效响应。')
            if b'\n' in reply:
                line = reply.split(b'\n', 1)[0].decode('utf-8', 'replace').strip()
                if line != 'READY' or pending:
                    raise RuntimeError(line or '剪贴板服务启动失败。')
                return


def set_clipboard_payload(payload):
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('WPS 原生粘贴需要 XWayland（DISPLAY），当前不可用。')
    if importlib.util.find_spec('PySide6') is None:
        raise RuntimeError('缺少 PySide6：sudo dnf install python3-pyside6')
    encoded = json.dumps({key: base64.b64encode(value).decode('ascii')
                          for key, value in payload.items()}).encode()
    command = ([sys.executable, '--serve-clipboard'] if getattr(sys, 'frozen', False)
               else [sys.executable, str(Path(__file__).resolve()), '--serve-clipboard'])
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env=dict(os.environ, QT_QPA_PLATFORM='xcb'),
    )
    try:
        clipboard_handshake(process, encoded)
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


def main(argv=None, options=None):
    if argv is None and sys.argv[1:] == ['--serve-clipboard']:
        return serve_clipboard()
    parser = argparse.ArgumentParser(description='Fedora Wayland 剪贴板转换（实验版）')
    parser.add_argument('--demo', action='store_true', help='转换内置公式样本，用于验证 WPS 粘贴，无需先复制来源')
    parser.add_argument('--input', choices=['auto', 'markdown', 'html'], default='auto')
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--clipboard', action='store_true', help='写入 WPS 原生 DOCX 剪贴板（默认），随后在 WPS 按 Ctrl+V')
    output.add_argument('--table', action='store_true', help='识别剪贴板中的 Markdown 表格，写入 HTML 表格剪贴板（适配 WPS 表格）')
    output.add_argument('--as', dest='as_format', choices=['md', 'latex', 'html'],
                        help='应用扩展流程：按 Markdown/LaTeX/HTML 纯文本写入剪贴板')
    output.add_argument('--docx', action='store_true', help='保存 DOCX，而不是替换剪贴板')
    output.add_argument('--open', action='store_true', help='用 WPS 打开生成的 DOCX（隐含 --docx）')
    args = parser.parse_args(argv)
    open_docx = args.open
    use_clipboard = not (args.docx or args.open or args.table or args.as_format)
    options = load_conversion_options() if options is None else options
    try:
        if not os.environ.get('WAYLAND_DISPLAY'):
            raise RuntimeError('请在 Wayland 桌面会话中运行。')
        required = [pandoc_bin()] + ([] if args.demo else ['wl-paste'])
        if open_docx:
            required.append('flatpak-spawn' if os.environ.get('FLATPAK_ID') else 'wps')
        missing = [tool for tool in required if not shutil.which(tool)]
        if missing:
            raise RuntimeError('缺少命令：' + ', '.join(missing) + '。转换依赖：sudo dnf install pandoc wl-clipboard python3-pyside6；WPS 需单独安装。')
        if args.table:
            source = DEMO_TABLE_MARKDOWN if args.demo else read_table_source()
            payload, rows = table_clipboard_payload(source)
            payload, _token = stamp_conversion(
                payload, source, 'markdown' + MATH_EXTENSIONS, 'table')
            set_clipboard_payload(payload)
            message = f'表格已就绪（{rows} 行），请在 WPS 表格中按 Ctrl+V。'
            notify(message)
            print(message)
            return 0
        if args.as_format:
            if args.demo:
                content = DEMO_MARKDOWN.encode('utf-8')
                reader = 'markdown' + MATH_EXTENSIONS
            else:
                content, reader = read_clipboard(args.input)
            if not content.strip():
                raise RuntimeError('剪贴板内容为空。')
            raw_source = content
            content = prepare_document(content, reader, options,
                                       protect_task_lists=(args.as_format == 'md'),
                                       conversion=conversion_type(reader, args.as_format))
            payload = text_clipboard_payload(raw_source, content, reader,
                                             args.as_format, options)
            payload, _token = stamp_conversion(payload, raw_source, reader, args.as_format)
            set_clipboard_payload(payload)
            message = f'已按{text_clipboard_label(args.as_format)}文本写入剪贴板，在目标应用中按 Ctrl+V。'
            notify(message)
            print(message)
            return 0
        if args.demo:
            content = DEMO_MARKDOWN.encode('utf-8')
            reader = 'markdown' + MATH_EXTENSIONS
        else:
            content, reader = read_clipboard(args.input)
        if not content.strip():
            raise RuntimeError('剪贴板内容为空。')
        raw_source = content
        content = prepare_document(content, reader, options,
                                   conversion=conversion_type(reader, 'docx'))
        command = [pandoc_bin(), '--from', 'json']
        if not use_clipboard:
            name = docx_output_path(options)
            try:
                docx = run(command + docx_writer_args(options)
                           + ['--to', 'docx', '--output', '-'], content)
                docx = finish_docx(docx, reader, options)
                Path(name).write_bytes(docx)
            except Exception:
                Path(name).unlink(missing_ok=True)
                raise
            print(name)
            if open_docx:
                open_in_wps(name)
            message = 'DOCX 已保存：' + str(name)
            lost = lost_image_count(content, docx)
            if lost:
                message += f'（注意：{lost} 张图片未能嵌入）'
            notify(message)
            print(message)
        else:
            plain_text = run(command + ['--to', 'plain'], content)
            payload = native_clipboard_payload(content, plain_text, options, reader)
            lost = lost_image_count(content, payload['Kingsoft WPS 9.0 Format'])
            payload, _token = stamp_conversion(payload, raw_source, reader, 'doc')
            set_clipboard_payload(payload)
            message = '公式富文本已就绪，请在 WPS 的 .docx 文档中按 Ctrl+V。'
            if lost:
                message += f'注意：{lost} 张图片未能嵌入。'
            if options.get('keep_file'):
                try:
                    keep_path = docx_output_path(options)
                    Path(keep_path).write_bytes(payload['Kingsoft WPS 9.0 Format'])
                    message += ' 已保留文件：' + str(keep_path)
                except OSError as keep_error:
                    message += f'（文件保留失败：{keep_error}）'
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
