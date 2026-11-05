"""Identity-scoped Android native BLE pairing through physical USB and keys.

No PIN is returned, printed or written to a snapshot file. The caller owns the
hardware bench lock and must first request authenticated status over the known
BLE connection. Existing bonds need no native interaction.
"""
import re
import resource
import subprocess
import time
import xml.etree.ElementTree as ET

from usb_management import LocalUsb


def recover_wifi_trust(page, phone, port, identity, name, timeout=65):
    if not isinstance(name,str) or not name or len(name.encode())>16:
        raise ValueError('Expected an explicit valid analyzer name')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def adb(*items):
        try:
            return subprocess.check_output(['adb', '-s', phone, *items],
                                           stderr=subprocess.DEVNULL, timeout=12)
        except (subprocess.SubprocessError,OSError):
            # Exceptions normally include argv, which may contain the PIN.
            raise RuntimeError('Android native action failed') from None

    def key(code):
        adb('shell', 'input', 'keyevent', str(code))

    def keys(*codes):
        adb('shell','input','keyevent',*map(str,codes))

    def dump():
        # /dev/tty sends XML directly to captured stdout, never an SD-card file.
        raw = adb('shell', '-tt', 'uiautomator', 'dump', '/dev/tty')
        return ET.fromstring(raw[raw.find(b'<?xml'):raw.rfind(b'</hierarchy>')+12])

    def native_edits(ui):
        return [n for n in ui.iter('node') if n.attrib.get('class') == 'android.widget.EditText'
                and n.attrib.get('package') == 'com.android.settings']

    def matches(text):
        return re.search(r'(?<![A-Za-z0-9_-])'+re.escape(name)+r'(?![A-Za-z0-9_-])', text or '') is not None

    def rectangle(node):
        return list(map(int, re.findall(r'\d+', node.attrib.get('bounds', ''))))

    def ready():
        return page.evaluate("!!pairedNetworkId&&!wifiBusy&&$('wifiKnown').textContent.includes('已保存')")

    usb = LocalUsb(port)
    handled = False
    started=time.monotonic()
    trace=[]
    def stage(name):
        trace.append((name,round(time.monotonic()-started,1)))
    try:
        if usb.identity().hex() != identity.lower().replace(':', ''):
            raise ValueError('Physical USB identity differs from Android trust target')
        page.evaluate("()=>{window.pairedNetworkId='';const previous=networkEvent;networkEvent=(kind,data)=>{if(kind==='wifiStatus')pairedNetworkId=data.id||'';return previous(kind,data)}}")
        page.locator('#wifiRefresh').click()
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if ready():
                if page.evaluate('pairedNetworkId') != identity.lower().replace(':', ''):
                    raise ValueError('Authenticated BLE identity differs from physical USB')
                return dict(native_pairing_used=handled, identity_checked=True)
            code = usb.pairing_display()
            if code is None or handled:
                page.wait_for_timeout(100)
                continue
            # The physical display is active. Find the actual Settings input,
            # excluding all editable fields belonging to the WebView.
            ui = dump()
            stage('display-active')
            if not native_edits(ui):
                adb('shell', 'cmd', 'statusbar', 'expand-notifications')
                ui = dump()
                targets = [n for n in ui.iter('node')
                           if n.attrib.get('package') == 'com.android.systemui'
                           and matches(n.attrib.get('text'))]
                if not targets:
                    raise RuntimeError('Identity-matched Android pairing notification missing')
                # A notification's content and keyboard-focus wrapper can be
                # siblings in MIUI's accessibility tree. Match their geometry.
                target = rectangle(targets[0])
                if len(target) != 4:
                    raise RuntimeError('Pairing notification bounds missing')
                x, y = (target[0]+target[2])/2, (target[1]+target[3])/2
                focused=[n for n in ui.iter('node')if n.attrib.get('focused')=='true']
                if len(focused)==1 and focused[0].attrib.get('resource-id')=='com.android.systemui:id/scrim_behind':
                    # MIUI header: battery, clock, date, shortcut, scrim. Move
                    # focus only, then verify the next notification geometry.
                    keys(61,61,61,61,61)
                    ui=dump()
                for _ in range(16):
                    focused = [n for n in ui.iter('node') if n.attrib.get('focused') == 'true']
                    if len(focused) == 1:
                        bounds = rectangle(focused[0])
                        if (len(bounds) == 4 and bounds[0] <= x <= bounds[2]
                                and bounds[1] <= y <= bounds[3]
                                and focused[0].attrib.get('class') == 'android.widget.FrameLayout'):
                            key(66)
                            stage('notification-open-'+str(_))
                            break
                    key(61)
                    ui = dump()
                else:
                    raise RuntimeError('Pairing notification did not receive keyboard focus')
                ui = dump()
            # Notification activation and Settings activity mapping are async.
            mapped_until = min(deadline,time.monotonic()+5)
            while not native_edits(ui) and time.monotonic() < mapped_until:
                page.wait_for_timeout(100)
                ui = dump()
            fields = native_edits(ui)
            stage('dialog-mapped')
            if len(fields) != 1 or not any(matches(n.attrib.get('text')) for n in ui.iter('node')):
                packages = sorted({n.attrib.get('package', '') for n in ui.iter('node')
                                   if n.attrib.get('class') == 'android.widget.EditText'})
                named = any(matches(n.attrib.get('text')) for n in ui.iter('node'))
                raise RuntimeError(f'Native pairing target missing: fields={len(fields)}, edit_packages={packages}, name_matches={named}')
            if fields[0].attrib.get('text'):
                raise RuntimeError('Native pairing input is not empty; no code appended')
            for _ in range(6):
                if fields[0].attrib.get('focused') == 'true':
                    break
                key(61)
                ui = dump()
                fields = native_edits(ui)
                if len(fields) != 1:
                    raise RuntimeError('Native pairing input disappeared')
            else:
                raise RuntimeError('Native pairing input lacks focus')
            positive=[n for n in ui.iter('node')if n.attrib.get('package')=='com.android.settings'
                      and n.attrib.get('text')in('配对','确定','Pair','OK')and n.attrib.get('clickable')=='true']
            if len(positive)!=1:raise RuntimeError('Native positive pairing action missing before input')
            selected_action=(positive[0].attrib.get('resource-id'),positive[0].attrib.get('text'))
            # Re-read the current display immediately before submission. Do not
            # reuse a code from an expired/cancelled request.
            code = usb.pairing_display()
            if code is None:
                raise RuntimeError('Physical pairing display expired')
            adb('shell', 'input', 'text', code)
            stage('digits-entered')
            # Move focus without submitting, then verify both input and action
            # from the same fresh native snapshot.
            key(61)
            ui = dump()
            entered = native_edits(ui)
            value=entered[0].attrib.get('text','')if len(entered)==1 else ''
            masked=(len(entered)==1 and entered[0].attrib.get('password')=='true'
                    and len(value)==6 and all(c in '*•●·'for c in value))
            if len(entered) != 1 or (value != code and not masked):
                raise RuntimeError(f'Native pairing input check failed: fields={len(entered)}, length={len(value)}, password={entered[0].attrib.get("password")if entered else None}')
            code = None
            value = None
            # Submit only after verifying the positive button's focus; avoid
            # an IME action while focus still belongs to the PIN input.
            for _ in range(8):
                buttons = [n for n in ui.iter('node')
                           if n.attrib.get('package') == 'com.android.settings'
                           and n.attrib.get('text')in('配对','确定','Pair','OK')
                           and n.attrib.get('clickable') == 'true']
                if len(buttons) != 1 or buttons[0].attrib.get('text') not in ('配对', '确定', 'Pair', 'OK'):
                    details = [{k:n.attrib.get(k) for k in ('class','resource-id','text','package','enabled','clickable','focused')}
                               for n in ui.iter('node') if re.fullmatch(r'(配对|确定|取消|Pair|OK|Cancel)([（(]\d+[)）])?',n.attrib.get('text',''))]
                    packages=sorted({n.attrib.get('package','')for n in ui.iter('node')})
                    raise RuntimeError(f'Pair buttons missing: {details}, packages={packages}, stages={trace}')
                if buttons[0].attrib.get('focused') == 'true':
                    if (buttons[0].attrib.get('resource-id'),buttons[0].attrib.get('text'))!=selected_action:
                        raise RuntimeError('Native pairing action changed during PIN entry')
                    key(66)
                    handled = True
                    break
                key(61)
                ui = dump()
            else:
                raise RuntimeError('Native pairing confirmation lacks focus')
        raise TimeoutError('Authenticated Android status missing after native pairing')
    finally:
        code = None
        usb.close()
