"""Markdown 块间空行规范化，移植自上游 PasteMD 的 md_normalizer。

不同来源的 Markdown（尤其 AI 聊天页的复制输出）经常缺少块级元素之间的
空行：标题、代码块、表格、列表、引用前后没有空行时，Pandoc 会把后续
内容当成同一段落。这里逐行判断块类型并补齐必要的空行。
"""

import re

_HEADING_RE = re.compile(r'^#{1,6}\s+')
_HR_RE = re.compile(r'^[-*_]{3,}$')
_ULIST_RE = re.compile(r'^[-*+]\s')
_OLIST_RE = re.compile(r'^\d+\.\s')


def _line_type(line, in_code_block, in_table):
    stripped = line.strip()
    if not stripped:
        return 'empty'
    if in_code_block:
        return 'code'
    if line.startswith('```'):
        return 'code'
    if _HEADING_RE.match(line):
        return 'heading'
    if line.startswith('|') and line.endswith('|'):
        return 'table'
    if _HR_RE.match(stripped):
        return 'hr'
    if _ULIST_RE.match(line) or _OLIST_RE.match(line):
        return 'list'
    if line.startswith('>'):
        return 'quote'
    return 'text'


def _needs_blank_before(prev_type, current_type):
    if prev_type in ('start', 'empty') or current_type == 'empty':
        return False
    if current_type == 'heading':
        return prev_type != 'heading'
    if current_type == 'code' and prev_type != 'code':
        return True
    if current_type == 'table' and prev_type != 'table':
        return True
    if current_type == 'list' and prev_type != 'list':
        return True
    if current_type == 'quote' and prev_type != 'quote':
        return True
    return current_type == 'hr'


def _needs_blank_after(current_type, index, lines, in_code_block):
    if index >= len(lines) - 1:
        return False
    if not lines[index + 1].strip():
        return False
    if current_type in ('heading', 'hr'):
        return True
    if current_type == 'code' and lines[index].startswith('```') and not in_code_block:
        return True
    return False


def normalize_markdown(md_text):
    """补齐块级元素之间的空行；代码块内部保持原样。"""
    lines = md_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    result = []
    in_code_block = False
    in_table = False
    prev_type = 'start'
    for index, line in enumerate(lines):
        current = _line_type(line, in_code_block, in_table)
        if line.startswith('```'):
            in_code_block = not in_code_block
        if current == 'table':
            in_table = True
        elif in_table and current not in ('table', 'empty'):
            in_table = False
        if _needs_blank_before(prev_type, current) and result and result[-1].strip():
            result.append('')
        result.append(line)
        if _needs_blank_after(current, index, lines, in_code_block):
            result.append('')
        prev_type = current
    return '\n'.join(result)
