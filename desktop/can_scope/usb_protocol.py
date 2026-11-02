"""Bounded, self-resynchronizing native USB framing shared with firmware."""
import struct
import zlib

HELLO = bytes([1, 0x7e, 0, 0]) + b'CANUSB1' + bytes(9)
PING = bytes([1, 0x7f]) + bytes(18)
BYE = bytes([1, 0x7d]) + bytes(18)


def encode(session, payload):
    if not 0 < session <= 0xffffffff or len(payload) != 20:
        raise ValueError('Invalid USB packet')
    frame = b'CUSB' + struct.pack('<I', session) + payload
    return frame + struct.pack('<I', zlib.crc32(frame))


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        packets = []
        for byte in data:
            self.buffer.append(byte)
            if len(self.buffer) < 32:
                continue
            frame = self.buffer
            if frame[:4] == b'CUSB' and struct.unpack_from('<I', frame, 28)[0] == zlib.crc32(frame[:28]):
                packets.append((struct.unpack_from('<I', frame, 4)[0], bytes(frame[8:28])))
                frame.clear()
            else:
                del frame[0]
        return packets
