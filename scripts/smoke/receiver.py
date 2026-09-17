#!/usr/bin/env python3
"""冒烟 DBus 接收器：接收 KWin 探针回调并打印（ExportAllSlots 无接口名）。"""
import sys
from PySide6.QtCore import QObject, Slot, QCoreApplication
from PySide6.QtDBus import QDBusConnection

SERVICE = 'org.pastemd.smoke'


class Receiver(QObject):
    @Slot(str)
    def ReceiveText(self, text):
        print(text, flush=True)
        if text.startswith('ACTIVATED'):
            QCoreApplication.quit()


if __name__ == '__main__':
    app = QCoreApplication(['receiver'])
    receiver = Receiver()  # 必须保持引用，否则注册对象会被回收
    bus = QDBusConnection.sessionBus()
    if not bus.registerService(SERVICE):
        print('无法注册服务（是否已有同名接收器？）', file=sys.stderr, flush=True)
        sys.exit(1)
    bus.registerObject('/', receiver, QDBusConnection.ExportAllSlots)
    app.exec()
