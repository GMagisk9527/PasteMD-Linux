#!/usr/bin/env bash
# 冒烟测试：md 应用规则流程的真机端到端验证（KDE Wayland）。
# 用法：在仓库根目录执行
#   APPIMAGE=dist/PasteMD-Linux-linux-v*-x86_64.AppImage bash scripts/smoke/smoke_md_rule_flow.sh
# 前置：wl-clipboard、PySide6、正在运行的 KDE Wayland 会话。
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
smoke="$root/scripts/smoke"
appimage="${APPIMAGE:-$(ls "$root"/dist/PasteMD-Linux-linux-v*-x86_64.AppImage 2>/dev/null | head -1)}"
[[ -n "$appimage" && -f "$appimage" ]] || { echo '未找到 AppImage（用 APPIMAGE= 指定）' >&2; exit 1; }
command -v wl-copy >/dev/null && command -v wl-paste >/dev/null \
  || { echo '需要 wl-clipboard' >&2; exit 1; }

cfg="$HOME/.config/pastemd-linux/settings.json"
backup=""
if [[ -f "$cfg" ]]; then
  backup="$(mktemp)"
  cp "$cfg" "$backup"
  trap '{ [[ -n "$backup" ]] && cp "$backup" "$cfg"; } ; rm -f "$backup"' EXIT
else
  mkdir -p "$(dirname "$cfg")"
  trap 'rm -f "$cfg"' EXIT
fi

python3 - "$cfg" <<'EOF'
import json, sys
cfg = sys.argv[1]
try:
    data = json.load(open(cfg))
except (OSError, ValueError):
    data = {}
apps = [a for a in (data.get('extensible_workflows', {}).get('md', {}).get('apps') or [])
        if a.get('name') != 'SMOKE规则测试']
apps.append({'name': 'SMOKE规则测试', 'class': 'python3', 'window_patterns': ['规则测试']})
data.setdefault('extensible_workflows', {})['md'] = {'enabled': True, 'apps': apps}
json.dump(data, open(cfg, 'w'), ensure_ascii=False, indent=2)
EOF

cleanup() {
  pkill -f "smoke[/]testwindow.py" 2>/dev/null || true
  pkill -f "smoke[/]receiver.py" 2>/dev/null || true
}
trap cleanup EXIT

python3 "$smoke/testwindow.py" >/dev/null 2>&1 &
python3 "$smoke/receiver.py" > /tmp/pastemd-smoke-receiver.out 2>&1 &
sleep 3
python3 "$smoke/activate.py" "$smoke/probe3.js" pastemd_smoke_probe >/dev/null 2>&1
grep -q '^ACTIVATED: 规则测试窗口' /tmp/pastemd-smoke-receiver.out \
  || { echo '✗ 未能激活测试窗口'; cat /tmp/pastemd-smoke-receiver.out; exit 1; }
echo '✓ 测试窗口已激活'

wl-copy --type 'text/html;charset=utf-8' \
  '<ul><li><span>[x]</span> 已完成</li><li><span>[ ]</span> 待办</li></ul><p>公式 $x^2$ 结束</p>'
"$appimage" --trigger >/dev/null 2>&1
sleep 6

output="$(wl-paste --type 'text/plain;charset=utf-8' 2>/dev/null || wl-paste --type text/plain)"
echo "--- 粘贴结果 ---"
echo "$output"
echo "----------------"
fail=0
[[ "$output" == *'- [x] 已完成'* ]] || { echo '✗ 任务列表被转义或丢失'; fail=1; }
[[ "$output" == *'- [ ] 待办'* ]] || { echo '✗ 未完成任务丢失'; fail=1; }
[[ "$output" == *'$x^2$'* ]] || { echo '✗ 公式未还原为 $…$'; fail=1; }
[[ "$output" != *'\['* ]] || { echo '✗ 存在方括号转义'; fail=1; }
[[ "$output" != *'PASTEMD_TASK'* ]] || { echo '✗ 占位符泄漏'; fail=1; }
[[ $fail -eq 0 ]] && echo '✓ 冒烟测试全部通过' || exit 1
