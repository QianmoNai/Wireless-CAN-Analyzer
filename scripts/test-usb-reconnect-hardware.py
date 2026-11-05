#!/usr/bin/env python3
"""Opt-in desktop USB reconnect test across a physical ESP32-S3 watchdog reset.

Requires the explicit STM32 CAN bench and an idle analyzer in USB CAN mode.
Uses an identity-checked native USB cable, never writes CAN commands or flash.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import time

from usb_management import LocalUsb
from can_scope.engine import Channel
from can_scope.usb_transport import prepare_usb


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723','stm32h723-dual'])
    parser.add_argument('--port',type=Path,required=True)
    parser.add_argument('--esptool',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=dict(ok=False)
    recovery_required=False

    def rom(before,after):
        subprocess.run([str(args.esptool),'--chip','esp32s3','--port',str(args.port),
                        '--before',before,'--after',after,'--no-stub','read-mac'],
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)

    async def run():
        nonlocal recovery_required
        channel=Channel(lambda *args:None,dict(mode='usb'))
        try:
            await prepare_usb(str(args.port),data=0)
            usb=LocalUsb(str(args.port))
            try:
                mac=usb.identity().hex(':')
            finally:
                usb.close()
            original=str(args.port.resolve(strict=True))
            await channel.handle('connect',['usb:'+original])
            identity=channel.saved.get('usb_identity')
            if (not identity or (identity['vid'],identity['pid'])!=(0x303a,0x1001) or
                    identity['serial'].lower()!=mac):
                raise ValueError('Connected desktop identity differs from physical USB')
            report.update(before_port=original,identity=identity)
            await channel.disconnect()
            recovery_required=True
            await asyncio.to_thread(rom,'usb-reset','no-reset')
            await asyncio.to_thread(rom,'no-reset','watchdog-reset')
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                try:
                    await channel.handle('reconnect',[])
                    if channel.ready:
                        recovery_required=False
                        break
                except (OSError,ValueError,ConnectionError):
                    pass
                await asyncio.sleep(.2)
            else:
                raise TimeoutError('Identity-scoped USB reconnect did not become ready')
            report.update(after_port=channel.saved['usb_port'],ready=channel.ready,
                          same_identity=channel.saved['usb_identity']==identity)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and not channel.packets:
                await asyncio.sleep(.02)
            report['received_can']=len(channel.packets)
            if not report['received_can'] or not report['same_identity']:
                raise AssertionError('Reconnected device did not provide CAN from the fixture')
            report['ok']=True
        finally:
            await channel.close()

    try:
        asyncio.run(run())
    except Exception as error:
        report['failure']=type(error).__name__
    finally:
        if recovery_required:
            try:
                rom('no-reset','watchdog-reset')
            except Exception:
                report['reset_cleanup_failed']=True
        try:
            asyncio.run(prepare_usb(str(args.port),data=0))
            report['restored_usb_can']=True
        except Exception:
            report['restored_usb_can']=False
            report['ok']=False
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['ok'] else 1)


if __name__=='__main__':
    from hardware_bench_lock import hardware_bench_lock
    with hardware_bench_lock():
        main()
