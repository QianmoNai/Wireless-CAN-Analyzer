"""Validate bench metrics against installed Linux headers and a real socket."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from linux_tcp_info import FIELDS, decode, read

if sys.platform != 'linux':
    print('SKIP: Linux socket metrics')
    sys.exit(0)

# Compile the system header rather than trusting hand-written Python offsets.
members = {
    'rto_us': 'tcpi_rto', 'unacked': 'tcpi_unacked', 'lost': 'tcpi_lost',
    'retrans': 'tcpi_retrans', 'last_data_sent_ms': 'tcpi_last_data_sent',
    'last_data_received_ms': 'tcpi_last_data_recv', 'rtt_us': 'tcpi_rtt',
    'rttvar_us': 'tcpi_rttvar', 'send_cwnd_segments': 'tcpi_snd_cwnd',
    'receive_space_bytes': 'tcpi_rcv_space', 'total_retrans': 'tcpi_total_retrans',
    'bytes_acked': 'tcpi_bytes_acked', 'bytes_received': 'tcpi_bytes_received',
    'segments_out': 'tcpi_segs_out', 'segments_in': 'tcpi_segs_in',
    'notsent_bytes': 'tcpi_notsent_bytes', 'min_rtt_us': 'tcpi_min_rtt',
    'data_segments_in': 'tcpi_data_segs_in', 'data_segments_out': 'tcpi_data_segs_out',
    'bytes_sent': 'tcpi_bytes_sent', 'bytes_retrans': 'tcpi_bytes_retrans',
    'receive_out_of_order_packets': 'tcpi_rcv_ooopack',
    'peer_receive_window_bytes': 'tcpi_snd_wnd',
    'local_receive_window_bytes': 'tcpi_rcv_wnd',
}
assert members.keys() == FIELDS.keys()
with tempfile.TemporaryDirectory(prefix='can-tcp-info-') as directory:
    binary = Path(directory)/'offsets'
    def program(selected):
        source = '#include <linux/tcp.h>\n#include <stddef.h>\n#include <stdio.h>\nint main(void){\n'
        for name, member in selected.items():
            source += f'printf("{name} %zu %zu\\n",offsetof(struct tcp_info,{member}),sizeof(((struct tcp_info*)0)->{member}));\n'
        return source+'return 0;}\n'
    command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-x', 'c', '-', '-o', str(binary)]
    built = subprocess.run(command, input=program(members), text=True, capture_output=True)
    if built.returncode:
        # Older supported Linux headers end before this recently appended field.
        # The decoder still gates it on the kernel's actual returned length.
        assert 'tcpi_rcv_wnd' in built.stderr, built.stderr
        members.pop('local_receive_window_bytes')
        subprocess.run(command, input=program(members), text=True, check=True)
        print('SKIP: old header lacks local receive-window field')
    for line in subprocess.check_output([str(binary)], text=True).splitlines():
        name, offset, size = line.split()
        assert FIELDS[name] == (int(offset), 'Q' if int(size) == 8 else 'I'), line

assert decode(bytes(8)) == {'kernel_bytes': 8}
short = decode(bytes(104))
assert 'total_retrans' in short and 'bytes_received' not in short

with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0)); listener.listen(1)
    with socket.create_connection(listener.getsockname(), timeout=2) as client:
        server, _ = listener.accept()
        with server:
            server.settimeout(2)
            client.sendall(b'X'*3000)
            left = 3000
            while left:
                received = server.recv(left); assert received
                left -= len(received)
            server.sendall(b'Y'*1000)
            left = 1000
            while left:
                received = client.recv(left); assert received
                left -= len(received)
            metrics = read(client)
            assert metrics['kernel_bytes'] >= 104 and metrics['rtt_us'] > 0, metrics
            if 'bytes_received' in metrics: assert metrics['bytes_received'] >= 1000, metrics
            if 'bytes_acked' in metrics: assert metrics['bytes_acked'] >= 3000, metrics
    assert read(client).get('unavailable_errno') is not None
print('PASS: TCP_INFO field offsets match Linux headers; bounded decoding and real loopback counters')
