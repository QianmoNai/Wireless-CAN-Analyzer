"""A temporary, target-only terminal agent on Bleak's own BlueZ connection.

Never request the default-agent role: BlueZ selects an application's agent by
the D-Bus sender of Device1.Pair. A separate MessageBus would not be sufficient.
"""
import asyncio
import os
import re
import sys

from dbus_fast import DBusError, Message, MessageType
from dbus_fast.service import ServiceInterface, method

AGENT_INTERFACE = 'org.bluez.Agent1'
MANAGER_INTERFACE = 'org.bluez.AgentManager1'
REJECTED = 'org.bluez.Error.Rejected'
CANCELED = 'org.bluez.Error.Canceled'


class AgentSetupError(Exception):
    """Fixed text only; third-party exception payloads must never be formatted."""


async def read_passkey(timeout=60):
    """Cancellable input(), with terminal echo disabled as in getpass.

    Do not put blocking getpass in a worker: a canceled to_thread(getpass) would
    leave a reader alive and can hang asyncio.run() or consume the next SSID.
    Canonical tty readiness means input() is called only after a complete line.
    """
    import termios
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        raise AgentSetupError('配对码必须在交互式终端输入。')
    original = termios.tcgetattr(fd)
    if not original[3] & termios.ICANON:
        raise AgentSetupError('配对码输入需要正常的终端行模式。')
    hidden = list(original)
    hidden[3] &= ~(termios.ECHO | termios.ECHONL)
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def ready():
        loop.remove_reader(fd)
        if future.done():
            return
        try:
            future.set_result(input())
        except (EOFError, KeyboardInterrupt):
            future.set_exception(DBusError(CANCELED, 'Terminal input canceled'))
        except Exception:
            future.set_exception(AgentSetupError('无法安全读取终端配对码。'))

    try:
        termios.tcsetattr(fd, termios.TCSAFLUSH, hidden)
        print('ESP32 六位配对码（在本终端输入，不回显；回车取消，60 秒超时）：', end='', flush=True)
        loop.add_reader(fd, ready)
        return await asyncio.wait_for(future, timeout)
    finally:
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSAFLUSH, original)
        print(flush=True)


class PasskeyAgent(ServiceInterface):
    def __init__(self, device_path, reader=None):
        super().__init__(AGENT_INTERFACE)
        self.device_path = device_path
        self.reader = reader or read_passkey
        self.active = True
        self.pending = None
        self.requested = False

    def check_device(self, device):
        if not self.active or device != self.device_path:
            raise DBusError(REJECTED, 'Only the selected device is allowed')

    def cancel_request(self):
        if self.pending and not self.pending.done():
            self.pending.cancel()

    @method()
    def Release(self):
        self.active = False
        self.cancel_request()

    @method()
    def Cancel(self):
        self.cancel_request()

    @method()
    async def RequestPasskey(self, device: 'o') -> 'u':
        self.check_device(device)
        if self.requested:
            raise DBusError(REJECTED, 'A passkey was already requested')
        self.requested = True
        self.pending = asyncio.current_task()
        value = ''
        try:
            value = await self.reader()
            self.check_device(device)
            if not re.fullmatch(r'[0-9]{6}', value):
                raise DBusError(CANCELED, 'Six decimal digits required')
            return int(value)
        except (asyncio.CancelledError, TimeoutError, EOFError, KeyboardInterrupt):
            raise DBusError(CANCELED, 'Passkey input canceled or timed out') from None
        except DBusError:
            raise
        except Exception:
            raise DBusError(CANCELED, 'Terminal passkey input unavailable') from None
        finally:
            value = ''
            self.pending = None

    # KeyboardOnly + the ESP32's DisplayOnly must negotiate passkey entry.
    # Do not silently accept Just Works, numeric confirmation or legacy PINs.
    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        self.check_device(device)
        raise DBusError(REJECTED, 'Legacy PIN pairing is not supported')

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):
        self.check_device(device)
        raise DBusError(REJECTED, 'Passkey entry on this terminal is required')

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
        self.check_device(device)
        raise DBusError(REJECTED, 'Passkey entry on this terminal is required')

    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u'):
        self.check_device(device)
        raise DBusError(REJECTED, 'Passkey entry on this terminal is required')

    @method()
    def RequestAuthorization(self, device: 'o'):
        self.check_device(device)
        raise DBusError(REJECTED, 'Unauthenticated pairing is not supported')

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'):
        self.check_device(device)
        raise DBusError(REJECTED, 'Unsolicited service authorization is not supported')


class TerminalPairingAgent:
    """Register only while the caller authenticates its selected Bleak client.

    _bus/_device_path are private, verified Bleak 3.0.1 BlueZ backend fields.
    Fail closed if a future backend no longer exposes the same connection.
    """
    def __init__(self, client, address):
        backend = getattr(client, '_backend', None)
        self.bus = getattr(backend, '_bus', None)
        device_path = getattr(backend, '_device_path', '')
        suffix = '/dev_' + address.upper().replace(':', '_')
        if (self.bus is None or not re.fullmatch(r'/org/bluez/hci[0-9]+/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}', device_path)
                or not device_path.endswith(suffix) or not client.is_connected):
            raise AgentSetupError('无法取得已连接目标的 Bleak BlueZ D-Bus 会话；未启动配对。')
        self.agent = PasskeyAgent(device_path)
        self.path = '/org/can_scope/provision/agent_' + str(os.getpid())
        self.owner = None
        self.exported = self.guarded = self.attempted = False

    def guard(self, message):
        if (message.message_type == MessageType.METHOD_CALL and message.path == self.path
                and message.interface in (None, AGENT_INTERFACE) and message.sender != self.owner):
            return Message.new_error(message, REJECTED, 'Only bluetoothd may call this agent')
        return None

    async def manager_call(self, member, signature, body):
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination='org.bluez', path='/org/bluez', interface=MANAGER_INTERFACE,
            member=member, signature=signature, body=body)), 5)
        if reply.message_type == MessageType.ERROR:
            # The error NAME is structured; never propagate the backend body.
            raise DBusError(reply.error_name, 'BlueZ agent registration or cleanup failed')
        return reply

    async def __aenter__(self):
        try:
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                interface='org.freedesktop.DBus', member='GetNameOwner', signature='s', body=['org.bluez'])), 5)
            if reply.message_type != MessageType.METHOD_RETURN or not reply.body:
                raise AgentSetupError('无法核实 bluetoothd 的 D-Bus 身份；未启动配对。')
            self.owner = reply.body[0]
            self.bus.add_message_handler(self.guard)
            self.guarded = True
            self.bus.export(self.path, self.agent)
            self.exported = True
            self.attempted = True
            await self.manager_call('RegisterAgent', 'os', [self.path, 'KeyboardOnly'])
            return self
        except BaseException:
            await self.__aexit__(*sys.exc_info())
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        self.agent.active = False
        pending = self.agent.pending
        self.agent.cancel_request()
        try:
            if pending and pending is not asyncio.current_task():
                await asyncio.gather(pending, return_exceptions=True)
            if self.attempted:
                await self.manager_call('UnregisterAgent', 'o', [self.path])
        except Exception:
            # The owning Bleak bus is closed by the caller's finally, which also
            # removes its registrations. Do not hide an original pairing error.
            if exc_type is None:
                raise AgentSetupError('代理注销未确认；已停止后续配网并关闭 BLE 会话。') from None
        finally:
            if self.exported:
                self.bus.unexport(self.path, self.agent)
                self.exported = False
            if self.guarded:
                self.bus.remove_message_handler(self.guard)
                self.guarded = False
            self.attempted = False
