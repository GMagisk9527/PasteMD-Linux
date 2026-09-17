// 冒烟探针：回传窗口列表并激活标题含「规则测试」的窗口。
// 注意：目标服务名由 activate.py 通过环境变量注入。
let target = null;
const lines = [];
for (const w of workspace.windowList()) {
    lines.push(JSON.stringify({caption: w.caption, rc: String(w.resourceClass)}));
    if (w.caption.includes('规则测试')) {
        target = w;
    }
}
callDBus(pastemdSmokeService, '/', '', 'ReceiveText', lines.join('\n'));
if (target) {
    target.minimized = false;
    workspace.activeWindow = target;
    callDBus(pastemdSmokeService, '/', '', 'ReceiveText',
             'ACTIVATED: ' + target.caption + ' rc=' + String(target.resourceClass));
} else {
    callDBus(pastemdSmokeService, '/', '', 'ReceiveText', 'ACTIVATED: NONE');
}
