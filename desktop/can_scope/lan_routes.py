"""Linux multi-interface LAN routing without changing the system route table.

Discovery is a routing hint only. TLS still checks the saved certificate pin
before sending authentication, independently of any UDP reply.
"""
import asyncio
import socket
import struct
import sys


def interfaces():
    if sys.platform != 'linux' or not hasattr(socket, 'SO_BINDTODEVICE'):
        return []
    import fcntl
    result = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _, name in socket.if_nameindex():
            request = struct.pack('256s', name.encode()[:15])
            try:
                flags = struct.unpack_from('H', fcntl.ioctl(probe, 0x8913, request), 16)[0]
                # UP, BROADCAST and RUNNING; omit loopback/point-to-point links.
                if flags & 0x43 != 0x43 or flags & 0x18:
                    continue
                fcntl.ioctl(probe, 0x8915, request)  # Has an IPv4 address.
                result.append(name)
            except OSError:
                continue
    return result


def bind_device(sock, interface):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode()+b'\0')


async def _probe(interface, ip, identity, port, timeout):
    loop = asyncio.get_running_loop()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            bind_device(sock, interface)
            sock.bind(('', 0))
            for request in (b'CAN_SCOPE_DISCOVER_1', b'CAN_SCOPE_DISCOVER_'):
                await loop.sock_sendto(sock, request, (ip, port))
            deadline = loop.time()+timeout
            while loop.time() < deadline:
                data, address = await asyncio.wait_for(loop.sock_recvfrom(sock, 128), deadline-loop.time())
                if address == (ip, port) and data.decode('ascii', errors='ignore').split() == ['CAN_SCOPE_1', identity, str(port)]:
                    return interface
    except (OSError, TimeoutError):
        pass
    return None


async def interface_for(ip, identity, port, timeout=.75):
    choices = interfaces() if identity else []
    if len(choices) < 2:
        return None
    tasks = {asyncio.create_task(_probe(name, ip, identity, port, timeout)) for name in choices}
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if result := task.result():
                    return result
        return None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def open_connection(ip, port, context, interface):
    if not interface:
        return await asyncio.open_connection(ip, port, ssl=context)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        try:
            bind_device(sock, interface)
        except OSError:
            # A policy-restricted host or disappearing adapter keeps the
            # normal OS routing path; no privilege escalation is needed.
            sock.close()
            return await asyncio.open_connection(ip, port, ssl=context)
        await asyncio.get_running_loop().sock_connect(sock, (ip, port))
        return await asyncio.open_connection(sock=sock, ssl=context, server_hostname=ip)
    except BaseException:
        sock.close()
        raise
