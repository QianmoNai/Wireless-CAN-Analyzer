"""Keep the native WebView asset allowlists aligned with both entry pages."""
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'android/app/src/main/assets'
JAVA = (ROOT / 'android/app/src/main/java/dev/qianmo/canview/MainActivity.java').read_text()


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and 'src' in attrs:
            self.paths.append(attrs['src'])
        if tag == 'link' and 'href' in attrs:
            self.paths.append(attrs['href'])


channel = set(re.findall(r'url\.equals\(origin \+ "([^"]+)"\)', JAVA))
home = set(re.findall(r'url\.equals\("https://home\.local/([^"]+)"\)', JAVA))
for entry, allowed in [('index.html', channel), ('home.html', home)]:
    assert entry in allowed, f'{entry}: native entry route missing'
    parser = Resources()
    parser.feed((ASSETS / entry).read_text())
    for resource in parser.paths:
        assert resource in allowed, f'{entry}: native WebView blocks {resource}'
        assert (ASSETS / resource).is_file(), f'{entry}: missing {resource}'
print('PASS: both native WebView asset routes allow all entry-page resources.')
