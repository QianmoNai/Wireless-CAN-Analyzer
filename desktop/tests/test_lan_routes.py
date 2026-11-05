import asyncio
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope import lan_routes
from can_scope import transports


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_reply_and_identity_gate_close_probe_socket(self):
        identity = '0123456789ab'
        class Replies(asyncio.DatagramProtocol):
            def connection_made(self, transport):self.transport = transport
            def datagram_received(self, data, address):
                port = self.transport.get_extra_info('sockname')[1]
                reply = f'CAN_SCOPE_1 {identity if data == b"CAN_SCOPE_DISCOVER_" else "wrong"} {port}'.encode()
                self.transport.sendto(reply, address)
        loop = asyncio.get_running_loop()
        server, _ = await loop.create_datagram_endpoint(Replies, local_addr=('127.0.0.1', 0))
        port = server.get_extra_info('sockname')[1]
        original = socket.socket
        sockets = []
        def opened(*args, **kwargs):
            sock = original(*args, **kwargs)
            sockets.append(sock)
            return sock
        try:
            with patch.object(lan_routes, 'bind_device'), patch.object(lan_routes.socket, 'socket', opened):
                self.assertEqual(await lan_routes._probe('test', '127.0.0.1', identity, port, .5), 'test')
                self.assertIsNone(await lan_routes._probe('test', '127.0.0.1', 'another', port, .03))
            self.assertTrue(all(s.fileno() == -1 for s in sockets))
        finally:
            server.close()

    async def test_selects_responder_and_cancels_other_interfaces(self):
        stopped = asyncio.Event()
        async def probe(name, *_):
            if name == 'offline':return None
            if name == 'wifi':
                await asyncio.sleep(0)
                return name
            try:await asyncio.Event().wait()
            finally:stopped.set()
        with patch.object(lan_routes, 'interfaces', return_value=['offline', 'wifi', 'slow']), patch.object(lan_routes, '_probe', probe):
            self.assertEqual(await lan_routes.interface_for('192.168.1.2', 'id', 32323), 'wifi')
        self.assertTrue(stopped.is_set())

    async def test_default_route_without_identity_or_with_one_interface(self):
        with patch.object(lan_routes, 'interfaces', return_value=['wifi']), patch.object(lan_routes, '_probe', new=AsyncMock()) as probe:
            self.assertIsNone(await lan_routes.interface_for('192.168.1.2', 'id', 32323))
            self.assertIsNone(await lan_routes.interface_for('192.168.1.2', None, 32323))
            probe.assert_not_called()

    async def test_cancel_closes_all_interface_probes(self):
        began = asyncio.Event()
        cancelled = []
        async def probe(name, *_):
            began.set()
            try:await asyncio.Event().wait()
            finally:cancelled.append(name)
        with patch.object(lan_routes, 'interfaces', return_value=['wired', 'wifi']), patch.object(lan_routes, '_probe', probe):
            task = asyncio.create_task(lan_routes.interface_for('192.168.1.2', 'id', 32323))
            await began.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertCountEqual(cancelled, ['wired', 'wifi'])

    async def test_discovery_probes_both_interfaces_deduplicates_and_tolerates_loss(self):
        used = []
        async def probe(identity, found, broadcast, timeout, interface=None):
            used.append(interface)
            self.assertEqual((identity, broadcast, timeout), ('id', '255.255.255.255', .1))
            if interface == 'wired':
                raise OSError('adapter disappeared')
            found(dict(address='wifi:192.168.1.42'))
        rows = []
        with patch.object(lan_routes, 'interfaces', return_value=['wired', 'wifi']), patch.object(transports, '_discover_interface', probe):
            await transports.discover('id', rows.append, timeout=.1)
        self.assertCountEqual(used, [None, 'wired', 'wifi'])
        self.assertEqual(rows, [dict(address='wifi:192.168.1.42')])

    async def test_cancel_discovery_finishes_all_interface_tasks(self):
        began = asyncio.Event()
        closed = []
        async def probe(identity, found, broadcast, timeout, interface=None):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(interface)
        with patch.object(lan_routes, 'interfaces', return_value=['wired', 'wifi']), patch.object(transports, '_discover_interface', probe):
            task = asyncio.create_task(transports.discover('id', lambda _: None))
            await began.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertCountEqual(closed, [None, 'wired', 'wifi'])

    async def test_discovery_rejects_wrong_source_port_and_closes_socket(self):
        loop = asyncio.get_running_loop()
        wrong, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=('127.0.0.1', 0))
        class Replies(asyncio.DatagramProtocol):
            def connection_made(self, transport):self.transport = transport
            def datagram_received(self, data, address):
                port = self.transport.get_extra_info('sockname')[1]
                wrong.sendto(f'CAN_SCOPE_1 id {port}'.encode(), address)
        server, _ = await loop.create_datagram_endpoint(Replies, local_addr=('127.0.0.1', 0))
        port = server.get_extra_info('sockname')[1]
        original = socket.socket
        sockets = []
        def opened(*args, **kwargs):
            sock = original(*args, **kwargs);sockets.append(sock);return sock
        rows = []
        try:
            with patch.object(transports, 'PORT', port), patch.object(transports.socket, 'socket', opened):
                # Valid identity and advertised port from the wrong UDP source.
                await transports._discover_interface('id', rows.append, '127.0.0.1', .03)
            self.assertEqual(rows, [])
            self.assertTrue(all(s.fileno() == -1 for s in sockets))
        finally:
            server.close()
            wrong.close()
