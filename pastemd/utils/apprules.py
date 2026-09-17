"""应用扩展规则：把指定窗口的粘贴模式换成 Markdown / LaTeX / HTML 文本。

配置与上游 RICHQAQ/PasteMD 的 extensible_workflows 同名同构（去掉 Linux
分支不支持的 file 工作流）：

    "extensible_workflows": {
        "html":  {"enabled": true, "apps": []},
        "md":    {"enabled": true, "apps": []},
        "latex": {"enabled": true, "apps": []}
    }

每条 app：{"name": 显示名, "class": WM_CLASS 片段, "window_patterns": [标题正则]}。
class 用 token 精确匹配（避免 'et' 误伤 'net'），window_patterns 用 re.search
匹配窗口标题；两者都留空则不命中。
"""

import re

WORKFLOW_ORDER = ('html', 'md', 'latex')

DEFAULT_WORKFLOWS = {key: {'enabled': True, 'apps': []} for key in WORKFLOW_ORDER}

FIELD_SEPARATOR = '|'


def _tokens(name):
    return {name, *filter(None, re.split(r'[^a-z0-9]+', name))}


def _class_matches(pattern, resource_class):
    """token 级匹配：'et' 命中 'et'/'et.exe'，不命中 'wpsoffice'/'netease'。"""
    pattern = (pattern or '').strip().lower()
    if not pattern:
        return False
    return bool(_tokens(pattern) & _tokens((resource_class or '').lower()))


def match_app(app, resource_class, caption):
    """单条规则是否命中当前窗口；class 与标题正则都给出时须同时命中。"""
    if not isinstance(app, dict):
        return False
    patterns = app.get('window_patterns') or []
    if isinstance(patterns, str):
        patterns = [patterns]
    class_required = bool(app.get('class'))
    title_required = bool(patterns)
    class_ok = not class_required or _class_matches(app.get('class'), resource_class)
    title_ok = not title_required
    if title_required:
        for pattern in patterns:
            try:
                if re.search(pattern, caption or ''):
                    title_ok = True
                    break
            except re.error:
                continue
    if class_required or title_required:
        return class_ok and title_ok
    return False


# 常见 Markdown 编辑器预设（Electron 应用 class 通常取可执行名；
# 不确定时在拾取对话框里核对实际窗口的 class）
APP_PRESETS = (('语雀', 'yuque'), ('Notion', 'notion'), ('Typora', 'typora'),
               ('Obsidian', 'obsidian'), ('飞书', 'bytedance-feishu'))


def active_flow(workflows, resource_class, caption):
    """返回命中的可扩展流程名（html/md/latex），无命中返回 None。"""
    if not isinstance(workflows, dict):
        return None
    for key in WORKFLOW_ORDER:
        cfg = workflows.get(key)
        if not isinstance(cfg, dict) or not cfg.get('enabled', True):
            continue
        apps = cfg.get('apps')
        if not isinstance(apps, list):
            continue
        if any(match_app(app, resource_class, caption) for app in apps):
            return key
    return None


def parse_rules(text):
    """设置页文本框格式：每行一条 `显示名 | class | 标题正则`，class 与正则可留空。"""
    apps = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [part.strip() for part in line.split(FIELD_SEPARATOR)]
        while len(parts) < 3:
            parts.append('')
        name, class_pattern, title_pattern = parts[0], parts[1], parts[2]
        if not class_pattern and not title_pattern:
            continue
        app = {'name': name or class_pattern or title_pattern}
        if class_pattern:
            app['class'] = class_pattern
        if title_pattern:
            app['window_patterns'] = [title_pattern]
        apps.append(app)
    return apps


def format_rules(apps):
    """parse_rules 的逆操作，用于把配置回填到设置页文本框。"""
    lines = []
    for app in apps or []:
        if not isinstance(app, dict):
            continue
        patterns = app.get('window_patterns') or []
        title = patterns[0] if patterns else ''
        lines.append(FIELD_SEPARATOR.join([
            app.get('name', ''), app.get('class', ''), title]))
    return '\n'.join(lines)
