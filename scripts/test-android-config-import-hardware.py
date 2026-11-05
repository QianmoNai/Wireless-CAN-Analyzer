#!/usr/bin/env python3
"""Opt-in native SAF import in the independent Android bench app, using keys.

Requires an existing numeric-name export in Download and its local JSON copy.
Refuses a CDP forward that does not belong to the explicit bench process, any
connected channel, or a backup differing from the current motor roster. Native
import clears network trust; a subsequent authenticated BLE connection restores
it. The normal Android app is never targeted.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright
from hardware_bench_lock import hardware_bench_lock


def main(args):
    report = dict(ok=False, scope='independent bench app; native SAF and confirmation by keyboard')
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def adb(*items):
        return subprocess.check_output(['adb', '-s', args.phone, *items],
                                       stderr=subprocess.DEVNULL, timeout=15)

    def key(*codes):
        adb('shell', 'input', 'keyevent', *map(str, codes))

    def dump():
        adb('shell', 'uiautomator', 'dump', '/sdcard/can-config-window.xml')
        raw = adb('exec-out', 'cat', '/sdcard/can-config-window.xml')
        return ET.fromstring(raw[raw.find(b'<?xml'):raw.rfind(b'</hierarchy>')+12])

    def one(nodes):
        rows = list(nodes)
        if len(rows) != 1:
            raise RuntimeError('Expected exactly one native bench control')
        return rows[0]

    try:
        pid = adb('shell', 'pidof', args.bench_app).decode().strip()
        if not pid.isdecimal():
            raise RuntimeError('Expected one running bench process')
        url = urlparse(args.cdp)
        forward = [line.split() for line in adb('forward', '--list').decode().splitlines()]
        expected = [args.phone, f'tcp:{url.port}', f'localabstract:webview_devtools_remote_{pid}']
        if expected not in forward:
            raise ValueError('CDP forward does not target the independent bench process')
        backup = json.loads(args.backup.read_text())
        remote = json.loads(adb('exec-out', 'cat', '/sdcard/Download/'+args.file))
        if remote != backup or backup.get('format') != 'can-scope-config':
            raise ValueError('Phone export differs from the explicit backup')
        channels = backup['channels']
        if not 1 <= len(channels) <= 4 or len({c['slot'] for c in channels}) != len(channels):
            raise ValueError('Invalid backup channel count')
        adb('shell', 'am', 'start', '-n', args.bench_app+'/dev.qianmo.canview.MainActivity')
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(args.cdp, no_defaults=True)
            pages = [p for context in browser.contexts for p in context.pages]
            home = one(p for p in pages if p.url == 'https://home.local/home.html')
            selected = {}
            original_pages = []
            for c in channels:
                slot = c['slot']
                if type(slot) is not int or not 1 <= slot <= 4:
                    raise ValueError('Invalid bench channel slot')
                address = 'https://app.local/index.html' if slot == 1 else f'https://channel-{slot}.local/index.html'
                page = one(p for p in pages if p.url == address)
                state = page.evaluate('({connected,motors:watchedMotors.map(m=>m.config)})')
                if state['connected'] or state['motors'] != c['motors']:
                    raise ValueError('Import requires a disconnected, identical bench roster')
                selected[slot] = address
                original_pages.append(page)
            if len(pages) != len(channels)+1:
                raise ValueError('Backup does not cover every bench channel')
            home.evaluate('HomeAndroid.importConfig()')
            deadline = time.monotonic()+15
            while True:
                ui = dump()
                if any(n.attrib.get('resource-id') == 'com.google.android.documentsui:id/option_menu_search'
                       for n in ui.iter('node')):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Native file picker did not open')
            key(84)  # System DocumentsUI search; touch injection is unnecessary.
            ui = dump()
            edit = one(n for n in ui.iter('node') if n.attrib.get('class') == 'android.widget.EditText')
            if edit.attrib.get('focused') != 'true':
                raise RuntimeError('Native search field lacks keyboard focus')
            key(123, *([67]*len(edit.attrib.get('text', ''))))
            stem = Path(args.file).stem
            adb('shell', 'input', 'text', stem)
            ui = dump()
            edit = one(n for n in ui.iter('node') if n.attrib.get('class') == 'android.widget.EditText')
            if edit.attrib.get('text') != stem:
                raise RuntimeError('IME changed the numeric search text')
            key(66)
            deadline = time.monotonic()+15
            while True:
                ui = dump()
                targets = [n for n in ui.iter('node') if n.attrib.get('text') == args.file]
                if targets:
                    one(targets)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Explicit bench export missing from native search')
            focused = one(n for n in ui.iter('node') if n.attrib.get('focused') == 'true')
            if not any(n.attrib.get('text') == args.file for n in focused.iter('node')):
                raise RuntimeError('Native search focused another result; no file opened')
            key(66)
            deadline = time.monotonic()+15
            while True:
                ui = dump()
                if any(n.attrib.get('text') == '确认恢复配置' for n in ui.iter('node')):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Native import preview missing')
            for _ in range(8):
                positive = one(n for n in ui.iter('node') if n.attrib.get('resource-id') == 'android:id/button1')
                if positive.attrib.get('text') != '替换并导入':
                    raise RuntimeError('Unexpected native confirmation')
                if positive.attrib.get('focused') == 'true':
                    break
                key(61)
                ui = dump()
            else:
                raise RuntimeError('Native confirmation lacks keyboard focus')
            key(66)  # Exactly one native import submission.
            report['native_submitted'] = True
            deadline = time.monotonic()+15
            while True:
                pages = [p for context in browser.contexts for p in context.pages]
                # The importer destroys and recreates channel WebViews.
                if all(p.is_closed() for p in original_pages) and all(
                        any(p.url == address and not p.is_closed() for p in pages)
                        for address in selected.values()):
                    try:
                        matches = []
                        for c in channels:
                            page = one(p for p in pages if p.url == selected[c['slot']])
                            page.wait_for_function("typeof watchedMotors!=='undefined'", polling=100, timeout=1000)
                            state = page.evaluate('({connected,motors:watchedMotors.map(m=>m.config)})')
                            matches.append(dict(slot=c['slot'], motors=len(state['motors']),
                                                exact=state['motors'] == c['motors'], connected=state['connected']))
                        if all(x['exact'] and not x['connected'] for x in matches):
                            report.update(ok=True, channels=matches)
                            break
                    except Exception:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError('Imported bench WebViews did not restore exact configs')
                home.wait_for_timeout(100)
    except Exception as error:
        report.update(ok=False, failure=type(error).__name__, message=str(error)[:300])
    finally:
        try:
            adb('shell', 'rm', '-f', '/sdcard/can-config-window.xml')
        except Exception:
            pass
        args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    return report['ok']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phone', required=True)
    parser.add_argument('--bench-app', required=True, choices=['dev.qianmo.canview.bench'])
    parser.add_argument('--cdp', required=True)
    parser.add_argument('--file', required=True, help='Numeric .json basename already exported to Download')
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]{8,20}\.json', args.file):
        parser.error('--file must be a numeric JSON basename')
    url = urlparse(args.cdp)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or not url.port:
        parser.error('--cdp must be a local ADB-forwarded HTTP endpoint')
    with hardware_bench_lock():
        sys.exit(0 if main(args) else 1)
