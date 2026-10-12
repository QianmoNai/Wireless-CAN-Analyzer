"""Check every Chinese UI string is covered by the committed bitmap subset."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'firmware/main/board_ui_render.c').read_text()
strings = re.findall(r'"(?:\\.|[^"\\])*"', source)
needed = {ord(char) for string in strings for char in string if ord(char) > 127}
data = (ROOT/'firmware/main/board_ui_font_data.h').read_text()
entries = re.findall(r'\{0x([0-9a-f]+), \{([^}]+)\}\}', data)
codes = [int(code,16) for code,_ in entries]
assert codes == sorted(set(codes)), 'Font lookup requires unique, ordered codepoints'
assert needed <= set(codes), 'Missing UI glyphs: '+''.join(map(chr,sorted(needed-set(codes))))
for code, rows in entries:
    values = [int(value.strip(),16) for value in rows.split(',')]
    assert len(values)==16 and all(0<=v<=0xffff for v in values) and any(values), code
license_text = (ROOT/'firmware/fonts/OFL-SourceHanSans.txt').read_text()
assert 'SIL OPEN FONT LICENSE Version 1.1' in license_text
assert 'Copyright 2014-2025 Adobe' in license_text
print(f'PASS: all {len(needed)} UI glyphs covered, 16x16 row bounds, sorted lookup and font license.')
