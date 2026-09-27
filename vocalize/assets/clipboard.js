ObjC.import('AppKit');
ObjC.import('Foundation');

function run() {
    try {
        const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
        const source = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
        const request = JSON.parse(ObjC.unwrap(source));
        const board = $.NSPasteboard.generalPasteboard;
        const plainType = 'public.utf8-plain-text';
        const saidType = 'io.github.vocalize-cli.said';
        const change = Number(board.changeCount);
        if (request.op === 'read') {
            const plain = board.stringForType(plainType);
            const said = board.stringForType(saidType);
            if (Number(board.changeCount) !== change) {
                return JSON.stringify({ok: false, reason: 'changed'});
            }
            return JSON.stringify({
                ok: true,
                plain: plain.isNil() ? null : ObjC.unwrap(plain),
                said: said.isNil() ? null : ObjC.unwrap(said),
                change: change
            });
        }
        if (request.op !== 'write' || typeof request.plain !== 'string' ||
                typeof request.said !== 'string' ||
                ('expect' in request && !Number.isInteger(request.expect))) {
            return JSON.stringify({ok: false, reason: 'error'});
        }
        if ('expect' in request && Number(board.changeCount) !== request.expect) {
            return JSON.stringify({ok: false, reason: 'changed'});
        }
        board.clearContents;
        const owned = Number(board.declareTypesOwner($([plainType, saidType]), null));
        if (!board.setStringForType($(request.plain), plainType) ||
                !board.setStringForType($(request.said), saidType)) {
            return JSON.stringify({ok: false, reason: 'error'});
        }
        const after = Number(board.changeCount);
        if (after !== owned) {
            return JSON.stringify({ok: false, reason: 'changed'});
        }
        return JSON.stringify({ok: true, change: after});
    } catch (_) {
        return JSON.stringify({ok: false, reason: 'error'});
    }
}
