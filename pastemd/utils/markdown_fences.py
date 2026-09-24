"""识别 Markdown 围栏代码块的起止行。"""
import re

_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def fence_marker(line):
    """返回围栏符号序列；普通文本返回空字符串。"""
    match = _FENCE.match(line)
    return match.group(1) if match else ''


def closes_fence(line, opening):
    """关闭围栏须同字符、长度不短于开始围栏，且不能带信息字符串。"""
    marker = fence_marker(line)
    return (bool(marker) and bool(opening) and marker[0] == opening[0]
            and len(marker) >= len(opening) and not line.lstrip(' ')[len(marker):].strip())
