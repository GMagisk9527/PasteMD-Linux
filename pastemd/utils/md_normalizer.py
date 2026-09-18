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
_FENCE_RE = re.compile(r'^(```|~~~)')


def _is_table_separator(line):
    """GFM 表格分隔行：只含 |、-、: 和空白，且至少有一个竖线。

    `---|---|---` 与 `|---|---|` 等价，都是合法分隔行；裸 `---` 是
    水平线/-setext- 下划线，不算。
    """
    stripped = line.strip()
    if '|' not in stripped or '-' not in stripped:
        return False
    cells = [cell.strip() for cell in stripped.strip('|').split('|')]
    return bool(cells) and all(re.fullmatch(r':?-+:?', cell) for cell in cells)


def _line_type(line, in_code_block):
    stripped = line.strip()
    if not stripped:
        return 'empty'
    if in_code_block:
        return 'code'
    if _FENCE_RE.match(line):
        return 'code'
    if _HEADING_RE.match(line):
        return 'heading'
    if line.startswith('|') and line.endswith('|'):
        return 'table'
    if _is_table_separator(line):
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
    if current_type == 'code' and _FENCE_RE.match(lines[index]) and not in_code_block:
        return True
    return False


def normalize_markdown(md_text):
    """补齐块级元素之间的空行；代码块内部保持原样。"""
    lines = md_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    result = []
    in_code_block = False
    code_fence_char = ""
    prev_type = 'start'
    for index, line in enumerate(lines):
        current = _line_type(line, in_code_block)
        fence = _FENCE_RE.match(line)
        if fence:
            if not in_code_block:
                in_code_block = True
                code_fence_char = fence.group(1)
            elif fence.group(1) == code_fence_char:
                in_code_block = False
                code_fence_char = ""
        if _needs_blank_before(prev_type, current) and result and result[-1].strip():
            result.append('')
        result.append(line)
        if _needs_blank_after(current, index, lines, in_code_block):
            result.append('')
        prev_type = current
    return '\n'.join(result)
