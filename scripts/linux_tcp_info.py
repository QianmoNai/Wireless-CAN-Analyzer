"""Read-only Linux socket metrics for hardware benches; no packet contents.

The offsets follow the append-only struct tcp_info in linux/tcp.h. Only fields
covered by the kernel's returned length are decoded. Sender metrics describe
the host's command direction; they do not measure ESP32 retransmissions.
"""
import socket
import struct
import sys


FIELDS = {
    'rto_us': (8, 'I'),
    'unacked': (24, 'I'),
    'lost': (32, 'I'),
    'retrans': (36, 'I'),
    'last_data_sent_ms': (44, 'I'),
    'last_data_received_ms': (52, 'I'),
    'rtt_us': (68, 'I'),
    'rttvar_us': (72, 'I'),
    'send_cwnd_segments': (80, 'I'),
    'receive_space_bytes': (96, 'I'),
    'total_retrans': (100, 'I'),
    'bytes_acked': (120, 'Q'),
    'bytes_received': (128, 'Q'),
    'segments_out': (136, 'I'),
    'segments_in': (140, 'I'),
    'notsent_bytes': (144, 'I'),
    'min_rtt_us': (148, 'I'),
    'data_segments_in': (152, 'I'),
    'data_segments_out': (156, 'I'),
    'bytes_sent': (200, 'Q'),
    'bytes_retrans': (208, 'Q'),
    'receive_out_of_order_packets': (224, 'I'),
    'peer_receive_window_bytes': (228, 'I'),
    'local_receive_window_bytes': (232, 'I'),
}


def decode(raw):
    result = {'kernel_bytes': len(raw)}
    for name, (offset, kind) in FIELDS.items():
        if offset + struct.calcsize('='+kind) <= len(raw):
            result[name] = struct.unpack_from('='+kind, raw, offset)[0]
    return result


def read(sock):
    if sys.platform != 'linux' or not hasattr(socket, 'TCP_INFO') or sock is None:
        return None
    try:
        raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 256)
        return decode(raw)
    except OSError as exc:
        return {'unavailable_errno': exc.errno}
