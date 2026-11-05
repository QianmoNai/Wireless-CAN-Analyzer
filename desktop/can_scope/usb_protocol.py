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
        self.buffer.extend(data)
        offset = 0
        while len(self.buffer)-offset >= 32:
            start = self.buffer.find(b'CUSB', offset)
            if start < 0:
                # Keep a possible fragmented magic prefix, as well as the
                # original <32-byte bounded tail for the next read.
                offset = len(self.buffer)-31
                break
            if len(self.buffer)-start < 32:
                offset = start
                break
            if struct.unpack_from('<I', self.buffer, start+28)[0] == zlib.crc32(self.buffer[start:start+28]):
                packets.append((struct.unpack_from('<I', self.buffer, start+4)[0], bytes(self.buffer[start+8:start+28])))
                offset = start+32
            else:
                offset = start+1
        if offset:
            del self.buffer[:offset]
        return packets
