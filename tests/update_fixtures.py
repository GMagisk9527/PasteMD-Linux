#!/usr/bin/env python3
"""重新生成 tests/fixtures/ 下的期望输出（golden 文件）。

用法：修改了 Lua 过滤器或转换流程后，先核对变更是否符合预期，再运行
    python3 tests/update_fixtures.py
并用 git diff 审查期望输出的变化。黄金测试比较的是
cli.prepare_document 的完整产物（含 clean_document 与 meta 清理）。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pastemd.linux import cli  # noqa: E402

FIXTURES = Path(__file__).parent / 'fixtures'
# 黄金输出必须产自与 vendored 一致的官方 pandoc 3.7.0.2；仓库构建缓存里
# 有解好的副本（Fedora 等发行版重打包的 pandoc 输出会漂移，不能用）。
_CANDIDATES = [
    Path(__file__).resolve().parents[1] / '.build-tools/pandoc-3.7.0.2/bin/pandoc',
    Path('/tmp/pandoc-official/pandoc-3.7.0.2/bin/pandoc'),
]


def official_pandoc():
    override = os.environ.get('PASTEMD_PANDOC_BIN')
    if override:
        return override
    for path in _CANDIDATES:
        if path.exists():
            return str(path)
    return 'pandoc'


def main():
    os.environ['PASTEMD_PANDOC_BIN'] = official_pandoc()
    for name, suffix, kwargs in CASES:
        html = (FIXTURES / f'{name}.html').read_bytes()
        ast = cli.prepare_document(html, 'html' + cli.MATH_EXTENSIONS, {}, **kwargs)
        target = FIXTURES / f'{name}{suffix}.json'
        target.write_text(json.dumps(json.loads(ast), ensure_ascii=False, indent=1) + '\n',
                          encoding='utf-8')
        print(f'已生成 {target.relative_to(FIXTURES.parent.parent)}'
              f'（pandoc: {os.environ["PASTEMD_PANDOC_BIN"]}）')


CASES = [
    ('ai_answer', '', {}),
    ('obsidian_math', '', {}),
    ('prewrap', '', {}),
    ('task_lists', '.default', {}),
    ('task_lists', '.protect', {'protect_task_lists': True}),
]

if __name__ == '__main__':
    main()
