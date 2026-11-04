"""No real Bluetooth pairing: fake D-Bus peers and a local pseudo-terminal."""
import asyncio
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

if sys.platform != 'linux':
    raise unittest.SkipTest('Linux-only BlueZ terminal pairing agent')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dbus_fast import DBusError, Message, MessageType
from can_scope.bluez_agent import AgentSetupError, PasskeyAgent, TerminalPairingAgent, read_passkey

DEVICE = '/org/bluez/hci0/dev_90_DA_72_73_72_2E'
ADDRESS = '90:DA:72:73:72:2E'


def invoke(agent, name, *args):
    # dbus-fast's public decorator wrapper discards Python return values;
    # D-Bus dispatch invokes the original function, as these tests do.
    return getattr(type(agent), name).__wrapped__(agent, *args)


class AgentMethodTests(unittest.IsolatedAsyncioTestCase):
    async def test_passkey_entry_preserves_leading_zero_and_is_single_use(self):
        reader = AsyncMock(return_value='001234')
        agent = PasskeyAgent(DEVICE, reader)
        self.assertEqual(await invoke(agent, 'RequestPasskey', DEVICE), 1234)
        with self.assertRaises(DBusError):
            await invoke(agent, 'RequestPasskey', DEVICE)
        reader.assert_awaited_once()
        self.assertIsNone(agent.pending)

    async def test_other_device_is_rejected_before_reading(self):
        reader = AsyncMock()
        agent = PasskeyAgent(DEVICE, reader)
        with self.assertRaises(DBusError):
            await invoke(agent, 'RequestPasskey', '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF')
        reader.assert_not_awaited()

    async def test_invalid_input_eof_and_backend_error_are_fixed_errors(self):
        for value in ('', '12345', '1234567', '１２３４５６', 'SYNTHETIC-PRIVATE'):
            agent = PasskeyAgent(DEVICE, AsyncMock(return_value=value))
            with self.assertRaises(DBusError) as caught:
                await invoke(agent, 'RequestPasskey', DEVICE)
            self.assertNotIn(value or 'NEVER-PRESENT', str(caught.exception))
        for error in (EOFError(), TimeoutError(), ValueError('SYNTHETIC-PRIVATE')):
            agent = PasskeyAgent(DEVICE, AsyncMock(side_effect=error))
            with self.assertRaises(DBusError) as caught:
                await invoke(agent, 'RequestPasskey', DEVICE)
            self.assertNotIn('SYNTHETIC-PRIVATE', str(caught.exception))

    async def test_bluez_cancel_stops_pending_input(self):
        started = asyncio.Event()
        async def reader():
            started.set()
            await asyncio.Future()
        agent = PasskeyAgent(DEVICE, reader)
        task = asyncio.create_task(invoke(agent, 'RequestPasskey', DEVICE))
        await started.wait()
        invoke(agent, 'Cancel')
        with self.assertRaises(DBusError):
            await task
        self.assertIsNone(agent.pending)

    async def test_no_just_works_or_legacy_fallback(self):
        agent = PasskeyAgent(DEVICE)
        for name, args in (('RequestAuthorization', (DEVICE,)),
                           ('RequestConfirmation', (DEVICE, 123456)),
                           ('RequestPinCode', (DEVICE,)),
                           ('DisplayPasskey', (DEVICE, 123456, 0)),
                           ('DisplayPinCode', (DEVICE, '123456')),
                           ('AuthorizeService', (DEVICE, 'unrequested-service'))):
            with self.assertRaises(DBusError):
                invoke(agent, name, *args)


class FakeBus:
    def __init__(self, fail_registration=False):
        self.calls, self.exports, self.handlers = [], {}, []
        self.fail_registration = fail_registration
    async def call(self, message):
        self.calls.append(message)
        if message.member == 'GetNameOwner':
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[':1.42'])
        if message.member == 'RegisterAgent' and self.fail_registration:
            return SimpleNamespace(message_type=MessageType.ERROR,
                                   error_name='org.freedesktop.DBus.Error.AccessDenied',
                                   body=['SYNTHETIC-PRIVATE-BODY'])
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[])
    def export(self, path, agent): self.exports[path] = agent
    def unexport(self, path, agent): del self.exports[path]
    def add_message_handler(self, handler): self.handlers.append(handler)
    def remove_message_handler(self, handler): self.handlers.remove(handler)


class AgentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make(self, fail=False):
        bus = FakeBus(fail)
        client = SimpleNamespace(is_connected=True,
                                 _backend=SimpleNamespace(_bus=bus, _device_path=DEVICE))
        return bus, TerminalPairingAgent(client, ADDRESS)

    async def test_same_bus_target_scope_no_default_and_immediate_unregister(self):
        bus, context = self.make()
        async with context:
            self.assertIs(context.bus, bus)
            self.assertEqual(context.agent.device_path, DEVICE)
            self.assertIn(context.path, bus.exports)
            self.assertEqual(bus.calls[-1].body, [context.path, 'KeyboardOnly'])
            message = Message(path=context.path, interface='org.bluez.Agent1', member='RequestPasskey',
                              signature='o', body=[DEVICE], sender=':1.99', serial=1)
            reply = context.guard(message)
            self.assertEqual(reply.message_type, MessageType.ERROR)
            message.interface = None
            self.assertEqual(context.guard(message).message_type, MessageType.ERROR)
            message.sender = ':1.42'
            self.assertIsNone(context.guard(message))
        self.assertEqual([call.member for call in bus.calls], ['GetNameOwner', 'RegisterAgent', 'UnregisterAgent'])
        self.assertEqual(bus.exports, {})
        self.assertEqual(bus.handlers, [])
        self.assertFalse(context.agent.active)

    async def test_registration_error_and_pair_failure_cleanup(self):
        bus, context = self.make(fail=True)
        with self.assertRaises(DBusError) as caught:
            async with context:
                self.fail('must stop before Pair')
        self.assertNotIn('SYNTHETIC-PRIVATE', str(caught.exception))
        self.assertEqual(bus.exports, {})
        self.assertEqual(bus.handlers, [])
        self.assertEqual(bus.calls[-1].member, 'UnregisterAgent')
        bus, context = self.make()
        with self.assertRaises(TimeoutError):
            async with context:
                raise TimeoutError()
        self.assertEqual(bus.calls[-1].member, 'UnregisterAgent')
        self.assertEqual(bus.exports, {})

    async def test_cancellation_unregisters_and_unexports(self):
        bus, context = self.make()
        started = asyncio.Event()
        async def run():
            async with context:
                started.set()
                await asyncio.Future()
        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(bus.calls[-1].member, 'UnregisterAgent')
        self.assertEqual(bus.exports, {})
        self.assertEqual(bus.handlers, [])

    async def test_wrong_backend_or_path_fails_before_register(self):
        for client in (SimpleNamespace(is_connected=True),
                       SimpleNamespace(is_connected=True, _backend=SimpleNamespace(_bus=FakeBus(), _device_path=DEVICE))):
            with self.assertRaises(AgentSetupError):
                TerminalPairingAgent(client, 'AA:BB:CC:DD:EE:FF')


@unittest.skipUnless(sys.platform == 'linux', 'Linux terminal behavior')
class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_echo_and_terminal_restored_after_entry(self):
        import termios
        master, slave = os.openpty()
        try:
            original = termios.tcgetattr(slave)
            with os.fdopen(os.dup(slave), 'r') as terminal, patch.object(sys, 'stdin', terminal), \
                 redirect_stdout(io.StringIO()) as output:
                asyncio.get_running_loop().call_later(.01, os.write, master, b'001234\n')
                value = await read_passkey(timeout=1)
            self.assertTrue(value == '001234')
            self.assertNotIn('001234', output.getvalue())
            self.assertEqual(termios.tcgetattr(slave), original)
            os.set_blocking(master, False)
            with self.assertRaises(BlockingIOError):
                os.read(master, 128)
        finally:
            os.close(master)
            os.close(slave)

    async def test_timeout_restores_echo_and_removes_reader(self):
        import termios
        master, slave = os.openpty()
        try:
            original = termios.tcgetattr(slave)
            with os.fdopen(os.dup(slave), 'r') as terminal, patch.object(sys, 'stdin', terminal), \
                 redirect_stdout(io.StringIO()):
                fd = terminal.fileno()
                with self.assertRaises(TimeoutError):
                    await read_passkey(timeout=.01)
                self.assertEqual(termios.tcgetattr(slave), original)
                with self.assertRaises(KeyError):
                    asyncio.get_running_loop()._selector.get_key(fd)
        finally:
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
