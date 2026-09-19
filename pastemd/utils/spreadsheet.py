"""Spreadsheet table support ported from upstream PasteMD (stdlib only).

Parse a Markdown table, then render it as an HTML clipboard payload with a
plain TSV fallback — the same strategy upstream uses for WPS 表格/Excel.
"""
from html import escape
import re
from typing import List, Optional


def _split_table_cells(line: str) -> List[str]:
    """按 | 分割单元格，正确处理转义：\\| 是字面竖线，\\\\ 是字面反斜杠。"""
    cells = []
    current_cell = []
    i = 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line) and line[i + 1] in ('\\', '|'):
            # \| -> 字面 |；\\ -> 字面 \（其后的 | 是真正的分隔符）
            current_cell.append(line[i + 1])
            i += 2
        elif line[i] == '|':
            cells.append(''.join(current_cell).strip())
            current_cell = []
            i += 1
        else:
            current_cell.append(line[i])
            i += 1
    if current_cell or cells:
        cells.append(''.join(current_cell).strip())
    return cells


def _is_table_separator(line: str) -> bool:
    """GFM 表格分隔行：`|---|`、`---|---`、`:---:`；裸 `---` 是水平线。"""
    return bool(line) and set(line) <= set('|-: \t') and '-' in line


def _is_table_row(line: str) -> bool:
    return bool(line) and '|' in line


def _row_cells(line: str) -> List[str]:
    cells = _split_table_cells(line)
    if cells and cells[0] == '':
        cells = cells[1:]
    if cells and cells[-1] == '':
        cells = cells[:-1]
    return cells


def parse_markdown_table(md_text: str) -> Optional[List[List[str]]]:
    """解析 Markdown 表格为二维数组；不是表格时返回 None。

    允许表格前后有标题、段落等非表格文字（AI 复制常见「说明 + 表格」），
    取第一张表。空行结束当前表，避免把后文第二张表拼进来。
    """
    lines = md_text.strip().split('\n')
    if len(lines) < 2:
        return None
    i = 0
    n = len(lines)
    while i < n:
        header_line = lines[i].strip()
        if not header_line:
            i += 1
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j >= n:
            break
        sep_line = lines[j].strip()
        if _is_table_row(header_line) and _is_table_separator(sep_line):
            header = _row_cells(header_line)
            if not header:
                i += 1
                continue
            table_data = [header]
            k = j + 1
            while k < n:
                row = lines[k].strip()
                if not row:
                    break
                if _is_table_separator(row):
                    k += 1
                    continue
                if not _is_table_row(row):
                    break
                cells = _row_cells(row)
                if cells:
                    table_data.append(cells)
                k += 1
            return table_data
        i += 1
    return None


class TextSegment:
    """文本片段，带有格式信息。"""

    def __init__(self, text: str, bold: bool = False, italic: bool = False,
                 strikethrough: bool = False, is_code: bool = False,
                 hyperlink_url: Optional[str] = None):
        self.text = text
        self.bold = bold
        self.italic = italic
        self.strikethrough = strikethrough
        self.is_code = is_code
        self.hyperlink_url = hyperlink_url


class CellFormat:
    """单元格内联 Markdown 格式解析（字符级，与上游逻辑一致）。"""

    def __init__(self, text: str):
        self.text = text
        self.is_code_block = False
        self.has_newline = False
        self.segments = []
        self.clean_text = text

    def parse(self) -> str:
        text = self.text
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        if '\n' in text:
            self.has_newline = True
        if '<pre>' in text.lower() or '<code>' in text.lower():
            self.is_code_block = True
            text = re.sub(r'<pre>(.*?)</pre>',
                          lambda m: re.sub(r'<br\s*/?>', '\n', m.group(1), flags=re.IGNORECASE),
                          text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<code>(.*?)</code>',
                          lambda m: re.sub(r'<br\s*/?>', '\n', m.group(1), flags=re.IGNORECASE),
                          text, flags=re.DOTALL | re.IGNORECASE)
            self.clean_text = text.strip()
            self.segments = [TextSegment(self.clean_text, is_code=True)]
            return self.clean_text
        self.segments = self._parse_segments(text)
        self.clean_text = ''.join(seg.text for seg in self.segments)
        return self.clean_text

    def _parse_segments(self, text: str, bold: bool = False, italic: bool = False,
                        strikethrough: bool = False) -> List[TextSegment]:
        segments = []
        current_text = []
        i = 0

        def flush_current():
            if current_text:
                text_str = ''.join(current_text)
                if text_str:
                    segments.append(TextSegment(text_str, bold, italic, strikethrough))
                current_text.clear()

        while i < len(text):
            if text[i] == '\\' and i + 1 < len(text):
                # 只解除 Markdown 可转义标点；LaTeX 命令（如 \\Delta）必须保留反斜杠。
                if text[i + 1] in r'\\`*{}_[]()#+-.!|>~':
                    current_text.append(text[i + 1])
                    i += 2
                    continue
                current_text.append('\\')
                i += 1
                continue
            if text[i] == '`':
                end = text.find('`', i + 1)
                if end != -1:
                    flush_current()
                    segments.append(TextSegment(text[i + 1:end], is_code=True))
                    i = end + 1
                    continue
            if text[i:i + 2] == '~~' and not strikethrough:
                end = text.find('~~', i + 2)
                if end != -1:
                    flush_current()
                    segments.extend(self._parse_segments(text[i + 2:end], bold, italic, True))
                    i = end + 2
                    continue
            if text[i:i + 3] == '***' and not bold and not italic:
                end = text.find('***', i + 3)
                if end != -1:
                    flush_current()
                    segments.extend(self._parse_segments(text[i + 3:end], True, True, strikethrough))
                    i = end + 3
                    continue
            if text[i:i + 2] == '**' and not bold:
                end = i + 2
                while end < len(text) - 1:
                    if text[end:end + 2] == '**':
                        flush_current()
                        segments.extend(self._parse_segments(text[i + 2:end], True, italic, strikethrough))
                        i = end + 2
                        break
                    end += 1
                else:
                    current_text.append(text[i])
                    i += 1
                continue
            if text[i:i + 3] == '___' and not bold and not italic:
                end = text.find('___', i + 3)
                if end != -1:
                    flush_current()
                    segments.extend(self._parse_segments(text[i + 3:end], True, True, strikethrough))
                    i = end + 3
                    continue
            if text[i:i + 2] == '__' and not bold:
                end = i + 2
                while end < len(text) - 1:
                    if text[end:end + 2] == '__':
                        flush_current()
                        segments.extend(self._parse_segments(text[i + 2:end], True, italic, strikethrough))
                        i = end + 2
                        break
                    end += 1
                else:
                    current_text.append(text[i])
                    i += 1
                continue
            if text[i] == '*' and (i + 1 >= len(text) or text[i + 1] != '*'):
                end = i + 1
                while end < len(text):
                    if text[end] == '*' and (end + 1 >= len(text) or text[end + 1] != '*'):
                        flush_current()
                        segments.extend(self._parse_segments(text[i + 1:end], bold, True, strikethrough))
                        i = end + 1
                        break
                    end += 1
                else:
                    current_text.append(text[i])
                    i += 1
                continue
            if text[i] == '_' and (i + 1 >= len(text) or text[i + 1] != '_'):
                end = i + 1
                while end < len(text):
                    if text[end] == '_' and (end + 1 >= len(text) or text[end + 1] != '_'):
                        flush_current()
                        segments.extend(self._parse_segments(text[i + 1:end], bold, True, strikethrough))
                        i = end + 1
                        break
                    end += 1
                else:
                    current_text.append(text[i])
                    i += 1
                continue
            if text[i] == '[':
                close_bracket = text.find(']', i + 1)
                if close_bracket != -1 and close_bracket + 1 < len(text) and text[close_bracket + 1] == '(':
                    close_paren = text.find(')', close_bracket + 2)
                    if close_paren != -1:
                        flush_current()
                        link_text = text[i + 1:close_bracket]
                        link_url = text[close_bracket + 2:close_paren]
                        for seg in self._parse_segments(link_text, bold, italic, strikethrough):
                            seg.hyperlink_url = link_url
                            segments.append(seg)
                        i = close_paren + 1
                        continue
            current_text.append(text[i])
            i += 1

        flush_current()
        return segments


def _wrap_tag(tag: str, content: str) -> str:
    return f"<{tag}>{content}</{tag}>"


def cell_to_html(cell_value: str, *, keep_format: bool):
    """单元格转 HTML，返回 (html, 是否需要代码底色)。"""
    cf = CellFormat(cell_value)
    clean_text = cf.parse()
    if not keep_format:
        return escape(clean_text).replace('\n', '<br />'), False
    if cf.is_code_block:
        inner = escape(clean_text).replace('\n', '<br />')
        return _wrap_tag('code', inner), True

    parts = []
    needs_code_bg = False
    for seg in cf.segments:
        seg_text = escape(seg.text or '').replace('\n', '<br />')
        chunk = seg_text
        if seg.is_code:
            needs_code_bg = True
            chunk = _wrap_tag('code', chunk)
        if seg.strikethrough:
            chunk = _wrap_tag('s', chunk)
        if seg.italic:
            chunk = _wrap_tag('i', chunk)
        if seg.bold:
            chunk = _wrap_tag('b', chunk)
        if seg.hyperlink_url:
            url = escape(seg.hyperlink_url, quote=True)
            chunk = f'<a href="{url}">{chunk}</a>'
        parts.append(chunk)
    return (''.join(parts) or escape(clean_text)), needs_code_bg


def table_to_html(table_data: List[List[str]], *, keep_format: bool = True) -> str:
    """表格转完整 HTML 文档，首行作为加粗底色表头。"""
    rows_html = []
    for r, row in enumerate(table_data):
        cell_tag = 'th' if r == 0 else 'td'
        cell_html = []
        for cell_value in row:
            content_html, needs_code_bg = cell_to_html(cell_value, keep_format=keep_format)
            style_parts = ['padding:2px 6px', 'vertical-align:middle']
            if r == 0:
                style_parts.extend(['font-weight:bold', 'background-color:#D3D3D3'])
            if needs_code_bg:
                style_parts.extend(['background-color:#F0F0F0',
                                    'font-family:Menlo,Consolas,monospace'])
            cell_html.append(f'<{cell_tag} style="{";".join(style_parts)}">{content_html}</{cell_tag}>')
        rows_html.append('<tr>' + ''.join(cell_html) + '</tr>')
    return ('<!--StartFragment--><html><head><meta charset="utf-8" />'
            '<style>table{border-collapse:collapse}td,th{border:1px solid #D0D0D0}'
            'a{color:#0563C1;text-decoration:underline}</style></head><body><table>'
            + ''.join(rows_html)
            + '</table></body></html><!--EndFragment-->')


def table_to_tsv(table_data: List[List[str]]) -> str:
    """表格转 TSV，单元格内换行折叠为空格，制表符替换为空格防串列。"""
    lines = []
    for row in table_data:
        out_cells = []
        for cell_value in row:
            text = CellFormat(cell_value).parse()
            text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', ' ')
            text = text.replace('\t', ' ')
            out_cells.append(text)
        lines.append('\t'.join(out_cells))
    return '\n'.join(lines)
