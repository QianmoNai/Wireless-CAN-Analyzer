"""Offline fault injection of production TLS flow; no sockets, UART or credentials."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, marker):
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if not depth:
            return source[start:end+1]
    raise AssertionError(marker)


prelude = (ROOT / 'tests/wifi_tls_flow_fixture.c').read_text()
markers = ('static void tls_diagnostic_end(', 'static bool tls_write_count(',
           'static bool tls_write_all(', 'static bool tls_write_batch(',
           'static bool data_socket_options(', 'static void serve_client(')
current = (ROOT / 'firmware/main/network_link.c').read_text()
# Compare the actual historical production functions, not a hand-written model.
old = subprocess.check_output(['git', 'show', '0a1f3c1:firmware/main/network_link.c'],
                              cwd=ROOT, text=True)
# No batch collector can run before the identical handshake/authentication fence.
for source in (old, current):
    serve = function(source, 'static void serve_client(')
    prefix = serve.split('while(network_connected()', 1)[0]
    assert 'collect_batch(' not in prefix and 'xQueueReceive(' not in prefix
assert function(old, 'static void serve_client(').split('while(network_connected()', 1)[0] == \
       function(current, 'static void serve_client(').split('while(network_connected()', 1)[0]

with tempfile.TemporaryDirectory(prefix='wifi-tls-flow-') as directory:
    path = Path(directory)
    for name, source in [('old16', old), ('new16', current),
                         ('new32', current), ('new64', current)]:
        batch = int(name[-2:])
        functions = [function(source, marker) for marker in markers]
        if name != 'old16':
            functions.insert(-1, function(source, 'static unsigned collect_batch('))
        c = path / (name+'.c')
        c.write_text(prelude.replace('/* PRODUCTION_FUNCTIONS */', '\n'.join(functions)))
        binary = path / name
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-DCAN_WIFI_TCP_NODELAY=0', '-DCAN_WIFI_TLS_BATCH='+str(batch),
                        '-I'+str(ROOT/'firmware/main'), '-I'+str(ROOT/'tests/stubs'),
                        str(c), str(ROOT/'firmware/main/wifi_uart_diag.c'),
                        '-o', str(binary)], check=True)
        result = subprocess.check_output([str(binary)], text=True, timeout=5)
        print(name+': '+result.strip())
print('PASS: historical/current production TLS flow; mock behavior is not hardware proof')
