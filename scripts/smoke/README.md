# 真机冒烟测试（KDE Wayland）

自动化单元测试覆盖不到的「真实会话」链路在这里：KWin 焦点查询、窗口枚举、
应用规则匹配、XTEST 粘贴。日常改完代码跑 `unittest`；**发版前**建议跑一遍
本目录的冒烟脚本。

## 前置

- 正在运行的 KDE Wayland 会话（`XDG_SESSION_TYPE=wayland`）
- `wl-clipboard`（wl-copy / wl-paste）
- PySide6（`uv pip install --system PySide6` 或系统包）

## md 应用规则流程端到端

```bash
APPIMAGE=dist/PasteMD-Linux-linux-v*-x86_64.AppImage \
  bash scripts/smoke/smoke_md_rule_flow.sh
```

脚本自动完成：注入临时规则 → 启动测试窗口并激活（KWin）→ 复制含任务
列表/公式的 HTML → `--trigger` 触发转换 → 校验剪贴板里的任务列表未转义、
公式为 `$…$`、无占位符残留。用户的 settings.json 会被临时改写并在退出时
恢复。

## 单件工具

| 文件 | 用途 |
|---|---|
| `testwindow.py` | 起一个标题为「规则测试窗口」的非 WPS 窗口 |
| `receiver.py` | DBus 接收器（服务名 `org.pastemd.smoke`） |
| `probe3.js` + `activate.py` | KWin 脚本：枚举窗口并激活测试窗口 |
| `粘贴测试.md` | 手动测试 WPS 文档流程用的示例（复制后按热键） |

## 已知坑（改代码前先读）

- KWin 脚本**每次全新 load→run→stop→unload**；先 `stop()` 再 `run()` 在
  Plasma 6 上会静默失败
- DBus 回退服务名不能以数字开头（组件规则），见 `pastemd/linux/kwin.py`
- `pkill -f` 的模式会匹配到自己所在的命令行，用 `名字[x]` 字符类技巧
- 本机 KWin 的 XWayland 桥**不向 X11 客户端代发 Wayland 端写入**，剪贴板
  验证必须走应用自带的 X11 服务进程，不要用 wl-copy 写入后测 X11 读取
