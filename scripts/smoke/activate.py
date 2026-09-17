#!/usr/bin/env python3
"""冒烟激活驱动：加载并运行 KWin 脚本（每次全新 load→run→stop→unload）。

用法：activate.py <script.js> <脚本注册名> [服务名]
注意：不能先 stop() 再 run()——Plasma 6 上 stop 后的 run 会静默失败。
"""
import os
import sys
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

path, name = sys.argv[1], sys.argv[2]
service = sys.argv[3] if len(sys.argv) > 3 else 'org.pastemd.smoke'
app = QCoreApplication(['activate'])
bus = QDBusConnection.sessionBus()
kwin = QDBusInterface('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', bus)
script = open(path, encoding='utf-8').read().replace('pastemdSmokeService', repr(service))
tmp = os.path.join('/tmp', f'{name}.js')
with open(tmp, 'w', encoding='utf-8') as handle:
    handle.write(script)
reply = kwin.call('loadScript', tmp, name)
if reply.type() == QDBusMessage.MessageType.ErrorMessage:
    print('loadScript 失败：' + reply.errorMessage(), file=sys.stderr, flush=True)
    sys.exit(1)
script_id = int(reply.arguments()[0])
runner = QDBusInterface('org.kde.KWin', f'/Scripting/Script{script_id}',
                        'org.kde.kwin.Script', bus)
if not runner.isValid():
    print('runner 无效', file=sys.stderr, flush=True)
    kwin.call('unloadScript', name)
    sys.exit(1)
runner.call('run')


def cleanup():
    kwin.call('unloadScript', name)
    app.quit()


QTimer.singleShot(2500, cleanup)
app.exec()
