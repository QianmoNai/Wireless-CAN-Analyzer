#!/usr/bin/env python3
"""Authenticate one physically attached analyzer over BLE without screen input.

Firmware 1.24.4 required. Verifies USB identity, reads only its active local
pairing display and verifies the protected BLE identity before caching trust.
Does not configure Wi-Fi. Never prints PIN, fingerprint or authentication token.
Close other clients first. Run with desktop/.venv/bin/python on Linux.
"""
import argparse
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import resource
import sys
import time

from usb_management import LocalUsb
from can_scope.bluez_agent import TerminalPairingAgent
from can_scope.connection_store import connection_folder, load_connection, save_connection
from can_scope.transports import BleTransport
from can_scope.usb_transport import prepare_usb


async def run(args):
    spec = importlib.util.spec_from_file_location('dual_pair', Path(__file__).with_name('dual-can-pair.py'))
    mode = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mode)
    usb = link = None
    report = dict(ok=False, scope='physical USB display and authenticated BLE identity', channel=args.channel)
    try:
        await mode.select(args.port, 0)
        await asyncio.sleep(1)
        usb = LocalUsb(args.port)
        identity = usb.identity()
        if identity.hex() != args.identity.lower().replace(':', ''):
            raise ValueError('Physical USB identity differs from requested analyzer')
        link = BleTransport(lambda _: None, lambda _: None)
        await link.connect(args.address)
        agent = TerminalPairingAgent(link.client, args.address)

        async def reader():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                value = await asyncio.to_thread(usb.pairing_display)
                if value is not None:
                    return value
                await asyncio.sleep(.1)
            raise TimeoutError('No active physical pairing display')

        agent.agent.reader = reader
        rows = []
        async with agent:
            await asyncio.wait_for(link.provision(1, '', '', rows.append, lambda _: None), 70)
        value = rows[-1]
        if value['id'] != identity.hex():
            raise ValueError('Protected BLE identity differs from physical USB')
        path = connection_folder() / f'connection-{args.channel}.json'
        saved = load_connection(path)
        if saved.get('id') and saved['id'] != value['id']:
            raise ValueError('Channel belongs to a different analyzer')
        saved.update({k: value[k] for k in ('id', 'fp', 'token')})
        if value.get('ip'):
            saved['ip'] = value['ip']
        saved.update(address=args.address, usb_port=args.port, mode='ble')
        save_connection(path, saved)
        report.update(ok=True, identity_checked=True, authenticated_status=True,
                      pairing_reader_used=agent.agent.requested)
    except Exception as exc:
        report['failure'] = type(exc).__name__
    finally:
        if link:
            try:
                await link.close()
            except Exception as exc:
                report.update(ok=False, close_failed=type(exc).__name__)
        if usb:
            usb.close()
        try:
            await prepare_usb(args.port, data=0)
            report['restored_usb_can'] = True
        except Exception as exc:
            report.update(ok=False, restore_failed=type(exc).__name__)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--identity', required=True, help='Expected STA MAC / device ID')
    parser.add_argument('--address', required=True, help='Explicit known BLE address; never inferred from STA MAC')
    parser.add_argument('--channel', type=int, choices=range(1, 5), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Backend exception/debug strings may contain protected GATT payloads.
    logging.disable(sys.maxsize)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
