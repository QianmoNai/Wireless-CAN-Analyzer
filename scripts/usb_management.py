"""Physical, identity-scoped USB management; no CAN sends or secret logging."""
from pathlib import Path
import secrets
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from can_scope.serial_port import open_native_port
from can_scope.usb_protocol import Decoder, encode


class LocalUsb:
    def __init__(self, device):
        self.port = open_native_port(device, timeout=.02)
        self.session = secrets.randbelow(0xffffffff) + 1
        self.decoder = Decoder()

    def close(self):
        self.port.close()

    def identity(self):
        """Verify the physical ESP32-S3 USB serial against its reported STA MAC."""
        from serial.tools import list_ports
        address = self.dual(5)['peer']
        ports = [p for p in list_ports.comports()
                 if Path(p.device).resolve() == Path(self.port.port).resolve()]
        if len(ports) != 1 or (ports[0].vid, ports[0].pid) != (0x303a, 0x1001):
            raise ValueError('Expected one native ESP32-S3 USB identity')
        serial = ports[0].serial_number
        if serial is None or address.hex() != serial.replace(':', '').lower():
            raise ValueError('USB identity and STA MAC do not match')
        return address

    def exchange(self, request, event, timeout=3):
        """Submit once. A missing response means unknown, never implicit replay."""
        frame = encode(self.session, request)
        if self.port.write(frame) != len(frame):
            raise OSError('Incomplete management write; outcome unknown')
        token = request[16:20]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for sid, p in self.decoder.feed(self.port.read(4096)):
                if sid != self.session:
                    continue
                if p[1] == 18 and p[3] == request[1] and p[4:8] == token and p[2]:
                    raise ValueError('USB management rejected with status ' + str(p[2]))
                if p[1] == event and p[16:20] == token:
                    return p
        raise TimeoutError('USB management response missing; outcome unknown')

    def dual(self, op=0, *, role=0, peer=None, token=None, code=0):
        p = bytearray((1, 0x1a, op, role)) + bytearray(16)
        if peer is not None:
            if len(peer) != 6:
                raise ValueError('Expected six-byte peer MAC')
            p[4:10] = peer
        struct.pack_into('<II', p, 12, code, token or secrets.randbelow(0xffffffff)+1)
        r = self.exchange(p, 23)
        if op == 6:
            sent, received, dropped = struct.unpack_from('<III', r, 4)
            return dict(state=r[3], sent=sent, received=received, dropped=dropped)
        return dict(state=r[3], peer=r[4:10], bound=bool(r[10]&1),
                    confirmed=bool(r[10]&2), uart=bool(r[10]&8), role=r[11],
                    code=struct.unpack_from('<I', r, 12)[0])

    def pairing_display(self):
        """Return active six-digit BLE display only; caller must not log it."""
        p = bytearray((1, 0x1b)) + bytearray(18)
        struct.pack_into('<I', p, 16, secrets.randbelow(0xffffffff)+1)
        r = self.exchange(p, 24)
        code = struct.unpack_from('<I', r, 4)[0]
        return f'{code:06d}' if r[2] == 1 and code <= 999999 else None

    def diagnostics(self):
        result = {}
        names = [('can_queue_expired', 'can_pending_expired', 'can_retries_exhausted'),
                 ('radio_send_failed', 'radio_submit_rejected', 'can_delivery_expired'),
                 ('radio_queue_full', 'can_queue_full', 'can_admission_expired')]
        for op, fields in enumerate(names, 7):
            p = bytearray((1, 0x1a, op)) + bytearray(17)
            struct.pack_into('<I', p, 16, secrets.randbelow(0xffffffff)+1)
            reply = self.exchange(p, 23)
            result.update(zip(fields, struct.unpack_from('<III', reply, 4)))
        return result

    def monitor_drops(self):
        """Read the three existing lifetime counter components; no reset."""
        p=bytearray((1,0x1c))+bytearray(18)
        struct.pack_into('<I',p,16,secrets.randbelow(0xffffffff)+1)
        reply=self.exchange(p,27)
        if reply[2]!=1 or reply[3]:
            raise ValueError('Unsupported CAN monitor counter version')
        fields=('bridge_delivery_dropped','can_rx_queue_dropped','dual_dropped')
        return dict(zip(fields,struct.unpack_from('<III',reply,4)))
