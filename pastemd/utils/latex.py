"""LaTeX formula conversion utilities."""

import re


def convert_latex_delimiters(text: str, fix_single_dollar_block: bool = True) -> str:
    """
    预处理 LaTeX 公式格式，使其能被 Pandoc 正确识别
    
    Args:
        text: 原始 Markdown 文本
        fix_single_dollar_block: 是否启用非标准块级公式修复（含单行 $ 块和行内 $ 空格）
        
    Returns:
        转换后的文本
    """
    # 1. (弃用) 标准 LaTeX 分隔符转换（\[...\] -> $$...$$）
    # 目前此功能被注释掉，若启用需解开 _convert_standard_latex_delimiters 内部注释
    text = _convert_standard_latex_delimiters(text)
    
    if fix_single_dollar_block:
        # 2. 修复行内公式中 $ 两侧的多余空格 ($  L  $ -> $L$)
        text = _fix_inline_math_spaces(text)
        
        # 3. 将单独一行的 $ ... $ 块级公式转换为 $$ ... $$
        text = _fix_single_dollar_blocks(text)
        
    return text


def _convert_standard_latex_delimiters(text: str) -> str:
    """
    (弃用) 将 LaTeX 标准分隔符转换为 Pandoc 支持的格式
    \\[ ... \\] -> $$ ... $$
    \\( ... \\) -> $ ... $
    """
    # # 匹配 \[ 开始到 \] 结束的公式块
    # pattern = r'\\\[(.*?)\\\]'
    # inline_pattern = r'\\\((.*?)\\\)'

    # def replace_match(match):
    #     formula = match.group(1).strip()
    #     return f"$$\n{formula}\n$$"

    # def replace_inline_match(match):
    #     formula = match.group(1).strip()
    #     return f"${formula}$"

    # text = re.sub(pattern, replace_match, text, flags=re.DOTALL)
    # text = re.sub(inline_pattern, replace_inline_match, text, flags=re.DOTALL)
    return text


def _fix_inline_math_spaces(text: str) -> str:
    """
    修复行内公式中 $ 后面的空格和 $ 前面的空格
    
    Pandoc tex_math_dollars 要求 $ 后不能有空格，$ 前不能有空格
    例如：$  L  $ -> $L$
    """
    def fix_inline_spaces(match):
        content = match.group(1)
        return f"${content.strip()}$"

    # 匹配 $ + 空格 + 内容 + 空格 + $
    # 排除 $$ 的情况
    # 使用 [ \t]+ 仅匹配水平空白，避免误伤多行块级公式
    return re.sub(r'(?<!\$)\$(?!\$)[ \t]+([^\n$]+?)[ \t]+(?<!\$)\$(?!\$)', fix_inline_spaces, text)


def _fix_single_dollar_blocks(text: str) -> str:
    """
    将单独一行的 $ ... $ 块级公式转换为 $$ ... $$
    规则：如果某一行去除首尾空白后只有 $，则视为块公式的分隔符
    注意要跳过代码块
    """
    lines = text.split('\n')
    new_lines = []
    in_code_block = False
    code_fence_char = ""
    in_dollar_block = False
    
    for line in lines:
        stripped = line.strip()
        
        # 1. 代码块检测
        if stripped.startswith('```') or stripped.startswith('~~~'):
            fence = stripped[:3]
            if not in_code_block:
                in_code_block = True
                code_fence_char = fence
                new_lines.append(line)
                continue
            elif stripped.startswith(code_fence_char):
                in_code_block = False
                code_fence_char = ""
                new_lines.append(line)
                continue
            
        if in_code_block:
            new_lines.append(line)
            continue
            
        # 2. 单行 $ 检测
        # 匹配仅包含 $ 的行（允许缩进）
        if re.match(r'^\s*\$\s*$', line):
            # 这是一个潜在的块公式分隔符
            if not in_dollar_block:
                # 块开始：替换为 $$
                # 尽量保留原有的缩进
                prefix = line[:line.find('$')]
                new_lines.append(f"{prefix}$$")
                in_dollar_block = True
            else:
                # 块结束：替换为 $$
                prefix = line[:line.find('$')]
                new_lines.append(f"{prefix}$$")
                in_dollar_block = False
        else:
            new_lines.append(line)
            
    return '\n'.join(new_lines)
