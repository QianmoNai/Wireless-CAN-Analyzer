"""Explicit hardware acceptance through the real Qt page and serial transports.

Run as the desktop user, with both ports free. Persists USB + UART / 115200 on
this device, uses temporary application data, and writes JSON + screenshots.
"""
import hashlib
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.usb_transport import prepare_usb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--usb', required=True)
    parser.add_argument('--peer', required=True)
    parser.add_argument('--output', required=True, type=Path, help='JSON report path')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    checks = {'bootstrap': asyncio.run(prepare_usb(args.usb, data=1, baud=115200))}
    # Import app before Qt: it configures the Linux rendering backend.
    from can_scope.app import Window
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import serial
    peer = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=.5, exclusive=True)
    peer.dtr = False
    peer.rts = False
    peer.port = args.peer
    peer.open()
    peer.reset_input_buffer()
    # UART adapters can deliver ROM startup bytes already in flight after a
    # kernel input flush. Account for setup noise, then require a quiet line
    # before test traffic begins; do not filter bytes during a test transfer.
    setup_noise = bytearray()
    quiet_since = time.monotonic()
    drain_deadline = quiet_since + 1.5
    while time.monotonic() < drain_deadline:
        chunk=peer.read(peer.in_waiting)
        if chunk:
            setup_noise.extend(chunk)
            quiet_since=time.monotonic()
        elif time.monotonic()-quiet_since >= .1:
            break
        time.sleep(.005)
    checks['peer_setup_noise']={'bytes':len(setup_noise),'hex':setup_noise[:4096].hex()}
    app = QApplication(sys.argv[:1])
    app.setOrganizationName('Qianmo')
    app.setApplicationName('CAN Scope serial hardware test')
    events = []
    packet_types = set()
    status_packets = []
    sessions = []
    class TestWindow(Window):
        def deliver(self, slot, kind, payload):
            data = json.loads(payload)
            if kind in ('state','device','error','deviceSettings'):
                events.append({'kind':kind, 'data':data})
            if kind == 'state' and data.get('value') == 'ready':
                sessions.append(self.runtime.channels[slot].transport.session)
            if kind == 'packets':
                for item in data.get('items', []):
                    raw = bytes.fromhex(item if isinstance(item,str) else item['hex'])
                    packet_types.add(raw[1])
                    if raw[1] == 21:
                        status_packets.append({'data':raw[2], 'status':raw[3],
                                               'baud':int.from_bytes(raw[4:8],'little'),
                                               'rx':int.from_bytes(raw[8:12],'little'),
                                               'tx':int.from_bytes(raw[12:16],'little')})
            super().deliver(slot, kind, payload)
    temp = tempfile.TemporaryDirectory(prefix='can-serial-hardware-')
    window = TestWindow(Path(temp.name))
    window.show()
    finished = False
    failures = []
    def capture(name):
        path = args.output.with_name(args.output.stem+'-'+name+'.png')
        window.grab().save(str(path))
        checks[name+'_screenshot'] = str(path)
    def finish(error=None):
        nonlocal finished
        if finished:return
        finished = True
        if error:failures.append(str(error))
        if not error:
            if not {19,20,21}.issubset(packet_types):failures.append('missing UART packet family: '+str(packet_types))
            if len(sessions) != 2 or sessions[0] == sessions[1]:failures.append('reconnect did not use a fresh USB session')
            if not any(p['data']==1 and p['status']==0 and p['baud']==115200 for p in status_packets):failures.append('missing healthy type 21 UART / 115200 status')
        report = {'ok':not failures, 'checks':checks, 'failures':failures,
                  'events':events, 'packet_types':sorted(packet_types), 'js_errors':window.js_errors,
                  'status_packets':status_packets, 'sessions':sessions,
                  'uid':__import__('os').getuid(), 'usb':args.usb, 'peer':args.peer}
        if window.js_errors:report['ok']=False
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        peer.close()
        def close():
            window.close()
            app.exit(0 if report['ok'] else 1)
        QTimer.singleShot(0,close)
    def js(code, callback=lambda _:None):
        if not finished:window.views[1].page().runJavaScript(code,callback)
    def wait_js(code, predicate, done, description, timeout=10):
        deadline = time.monotonic()+timeout
        def poll():
            if finished:return
            def result(value):
                if finished:return
                if predicate(value):done(value)
                elif time.monotonic()>=deadline:finish(description+'; observed '+str(value))
                else:QTimer.singleShot(100,poll)
            js(code,result)
        poll()
    def wait_peer(expected, done):
        actual = bytearray()
        deadline = time.monotonic()+30
        def poll():
            if finished:return
            actual.extend(peer.read(peer.in_waiting))
            if len(actual)>=len(expected):
                if bytes(actual)!=expected:finish('UART output mismatch: '+actual.hex()+' expected '+expected.hex())
                else:done(bytes(actual))
            elif time.monotonic()>deadline:finish('UART peer timed out: '+actual.hex())
            else:QTimer.singleShot(20,poll)
        poll()
    def start():
        if 1 not in window.ready:
            QTimer.singleShot(100,start);return
        js("$('connectionMode').value='usb';$('connectionMode').onchange();true",lambda _:wait_js("$('connectionMode').value",lambda x:x=='usb',scan,'connection mode did not update'))
    def scan(_=None):
        js("$('connectButton').click();true",lambda _:wait_js("JSON.stringify([...deviceMap.keys()])",lambda x:bool(x and 'usb:'+args.usb in json.loads(x)),scanned,'USB scan missing target'))
    def scanned(value):
        checks['scanned_ports'] = json.loads(value)
        QTimer.singleShot(250,lambda:(capture('scan'),connect()))
    def connect():
        code="[...$('deviceList').children].find(b=>b.querySelector('small').textContent===TARGET).click();true".replace('TARGET',json.dumps('usb:'+args.usb))
        js(code,lambda _:wait_js("connected&&window.uartMode&&!$('uartSend').disabled",bool,connected,'GUI USB/UART not ready'))
    def connected(_):
        checks['connected_uart'] = True
        marker=bytes.fromhex('43 53 2D 00 FE 53 59 4E 43 0D 0A')
        code="showTab('uart');$('uartClear').click();$('uartFormat').value='hex';$('uartInput').value=MARKER;$('uartSend').click();true".replace('MARKER',json.dumps(marker.hex()))
        js(code,lambda _:sync_peer(marker))
    def sync_peer(marker):
        # CMSIS-DAP CDC may hold boot bytes until the first UART TX. A known
        # setup marker exposes those bytes before strict transfer comparisons.
        actual=bytearray()
        deadline=time.monotonic()+5
        def poll():
            if finished:return
            actual.extend(peer.read(peer.in_waiting))
            index=actual.find(marker)
            if index >= 0:
                if bytes(actual[index:]) != marker:
                    finish('unexpected bytes after setup marker: '+actual.hex());return
                checks['peer_sync']={'marker':marker.hex(),'preceding_bytes':index,
                                     'preceding_hex':actual[:index].hex()}
                wait_js("$('uartResult').textContent",lambda x:f'已确认输出 {len(marker)} / {len(marker)}' in x,
                        lambda _:receive_hex(),'setup marker ACK missing')
            elif len(actual)>4096 or time.monotonic()>deadline:
                finish('setup marker not received: '+actual.hex())
            else:QTimer.singleShot(20,poll)
        poll()
    def receive_hex():
        raw = b'\x00\xff\x41\x7f'
        peer.write(raw)
        wait_js("$('uartLog').textContent",lambda x:x=='00 FF 41 7F',hex_received,'HEX RX did not render')
    def hex_received(value):
        checks['rx_hex'] = value
        QTimer.singleShot(250,lambda:(capture('rx-hex'),js("$('uartClear').click();$('uartDisplay').value='text';$('uartDisplay').onchange();true",lambda _:receive_text())))
    rx_text='实机 UART UTF-8 🙂\n'
    def receive_text():
        peer.write(rx_text.encode())
        wait_js("$('uartLog').textContent",lambda x:x==rx_text,text_received,'UTF-8 RX did not render')
    def text_received(value):
        checks['rx_utf8'] = value
        QTimer.singleShot(250,lambda:(capture('rx-utf8'),send_hex()))
    tx_hex = bytes(range(256))*16
    tx_text = '串口🙂'*409+'abcdef'  # exactly 4096 UTF-8 bytes
    transfer_started = 0
    def transfer(name, value, expected, done):
        nonlocal transfer_started
        peer.reset_input_buffer()
        transfer_started = time.monotonic()
        code = "$('uartFormat').value=FORMAT;$('uartInput').value=VALUE;$('uartSend').click();true"
        code = code.replace('FORMAT',json.dumps('hex' if name=='hex' else 'text')).replace('VALUE',json.dumps(value))
        def received(actual):
            peer_elapsed = time.monotonic()-transfer_started
            def confirmed(result):
                elapsed=time.monotonic()-transfer_started
                checks['tx_4096_'+name] = dict(bytes=len(actual), strict_equal=actual==expected,
                    sha256=hashlib.sha256(actual).hexdigest(), peer_complete_ms=round(peer_elapsed*1000,3),
                    complete_ms=round(elapsed*1000,3), Bps=round(len(actual)/elapsed,2), result=result)
                done(result)
            wait_js("$('uartResult').textContent",lambda x:f'已确认输出 {len(expected)} / {len(expected)}' in x,
                    confirmed,name+' send ACK missing',timeout=30)
        js(code,lambda _:wait_peer(expected,received))
    def send_hex():
        transfer('hex',tx_hex.hex(),tx_hex,lambda _:send_text())
    def send_text():
        assert len(tx_text.encode())==4096
        transfer('utf8',tx_text,tx_text.encode(),send_confirmed)
    def send_confirmed(value):
        checks['tx_utf8_ack'] = value
        js("$('uartResult').scrollIntoView({block:'center'});true",lambda _:QTimer.singleShot(250,tx_captured))
    def tx_captured():
        capture('tx-complete')
        js("invoke('deviceSettings',{data:0});true",lambda _:wait_js('!window.uartMode',bool,can_mode,'remote CAN switch did not reach UI'))
    def can_mode(_):
        checks['remote_can'] = True
        js("invoke('deviceSettings',{data:1,baud:115200});true",lambda _:wait_js("window.uartMode&&!$('uartSend').disabled",bool,uart_mode,'remote UART switch did not reach UI'))
    def uart_mode(_):
        checks['remote_uart'] = True
        js("$('connectButton').click();true",lambda _:wait_js('!connected',bool,disconnected,'BYE disconnect not reflected'))
    def disconnected(_):
        checks['bye_disconnect'] = True
        js("$('connectButton').click();true",lambda _:wait_js("deviceMap.has(TARGET)".replace('TARGET',json.dumps('usb:'+args.usb)),bool,lambda _:reconnect(),'reconnect scan missing'))
    def reconnect():
        code="[...$('deviceList').children].find(b=>b.querySelector('small').textContent===TARGET).click();true".replace('TARGET',json.dumps('usb:'+args.usb))
        js(code,lambda _:wait_js("connected&&window.uartMode&&!$('uartSend').disabled",bool,reconnected,'reconnect failed'))
    def reconnected(_):
        checks['reconnected_uart'] = True
        js("$('view-uart').scrollIntoView();true",lambda _:QTimer.singleShot(250,lambda:(capture('reconnected'),finish())))
    QTimer.singleShot(120000,lambda:finish('overall GUI hardware timeout'))
    QTimer.singleShot(100,start)
    result=app.exec()
    # Match app.main(): destroy pages before their profiles release storage.
    from shiboken6 import delete, isValid
    for view in window.views.values():
        page=view.page()
        if isValid(page):delete(page)
    for profile in window.profiles:
        if isValid(profile):delete(profile)
    temp.cleanup()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
