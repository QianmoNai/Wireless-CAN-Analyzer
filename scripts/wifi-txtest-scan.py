#!/usr/bin/env python3
"""Find the separate CANScope TX test SoftAP; never connect or edit profiles.

Active scans only. Normal user, no sudo; captured output is filtered to the
public test SSID/MAC/channel/signal. No other network names are printed.
"""
import argparse
import os
import re
import shutil
import subprocess
import time

NAME = r'CANScope-TX-[0-9a-fA-F]{4}'
MAC = r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}'


def parse_iw(text):
    rows = []
    for block in re.split(r'(?m)^BSS ', text)[1:]:
        mac = re.match(MAC, block)
        name = re.search(r'(?m)^\s*SSID: (' + NAME + r')\s*$', block)
        frequency = re.search(r'(?m)^\s*freq: (\d{4})\s*$', block)
        signal = re.search(r'(?m)^\s*signal: (-?\d+(?:\.\d+)?) dBm', block)
        if not (mac and name):
            continue
        freq = int(frequency[1]) if frequency else 0
        channel = 14 if freq == 2484 else (freq-2407)//5 if 2412 <= freq <= 2472 else None
        rows.append(dict(ssid=name[1], bssid=mac[0].lower(), channel=channel,
                         signal=signal[1] + ' dBm' if signal else None))
    return rows


def parse_nmcli(text):
    # --terse --escape no: this controlled test SSID cannot contain a colon.
    rows = []
    for line in text.splitlines():
        match = re.fullmatch('(' + NAME + '):(' + MAC + r'):(\d{1,3}):(\d{1,3})', line)
        if match and 1 <= int(match[3]) <= 233 and 0 <= int(match[4]) <= 100:
            rows.append(dict(ssid=match[1], bssid=match[2].lower(),
                             channel=int(match[3]), signal=match[4] + '%'))
    return rows


def parse_iwctl(text):
    # iwd's table lacks BSSID/channel. Do not invent them from the SSID.
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    rows = []
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(?:>\s*)?(' + NAME + r')\s+open\s+(\*{1,4}|-?\d{1,3})\s*', line)
        if match:
            rows.append(dict(ssid=match[1], bssid=None, channel=None, signal=match[2] + ' (iwd)'))
    return rows


def capture(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=25,
                                env=dict(os.environ, LC_ALL='C'))
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None  # No arbitrary stderr/exception text or neighboring SSIDs.


def scan_round(interface):
    if shutil.which('iw'):
        output = capture(['iw', 'dev', interface, 'scan'])
        if output is None:
            print('iw 主动扫描不可用（可能无权限或扫描忙）；尝试 NetworkManager。')
        else:
            rows = parse_iw(output)
            if rows:
                return rows
    if shutil.which('nmcli'):
        output = capture(['nmcli', '--terse', '--escape', 'no', '-f', 'SSID,BSSID,CHAN,SIGNAL',
                          'dev', 'wifi', 'list', 'ifname', interface, '--rescan', 'yes'])
        if output is not None:
            rows = parse_nmcli(output)
            if rows:
                return rows
        else:
            print('NetworkManager 扫描不可用；尝试 iwd。')
    if shutil.which('iwctl'):
        if capture(['iwctl', 'station', interface, 'scan']) is not None:
            time.sleep(2)  # iwd scan completion is asynchronous.
            output = capture(['iwctl', 'station', interface, 'get-networks'])
            if output is not None:
                rows = parse_iwctl(output)
                # Read scan cache, without a second active iw scan, if permitted.
                cached = capture(['iw', 'dev', interface, 'scan', 'dump']) if shutil.which('iw') else None
                detailed = parse_iw(cached) if cached is not None else []
                return detailed or rows
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', default='wlan0')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', args.interface):
        parser.error('invalid interface name')
    if not 1 <= args.rounds <= 10 or not 0 <= args.interval <= 60:
        parser.error('rounds must be 1..10; interval must be 0..60 seconds')
    if os.geteuid() == 0:
        print('请用普通用户运行；不使用 sudo。')
        return 2
    seen = False
    for index in range(args.rounds):
        rows = scan_round(args.interface)
        print(f'第 {index+1}/{args.rounds} 轮：' + ('发现测试 AP' if rows else '未发现测试 AP（或扫描不可用）'))
        for row in rows:
            print(f"SSID={row['ssid']} BSSID={row['bssid'] or '未知'} channel={row['channel'] or '未知'} signal={row['signal'] or '未知'}")
        seen |= bool(rows)
        if index+1 < args.rounds:
            time.sleep(args.interval)
    return 0 if seen else 1


if __name__ == '__main__':
    raise SystemExit(main())
