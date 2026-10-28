"""Build self-hosted, OFL-licensed Chinese web fonts (one-time development tool).

Requires fontTools and brotli; the reader itself remains dependency-free.
Noto sources may be reused from Windows Fonts, or downloaded from Google Fonts.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'test-results' / 'font-build'))
from fontTools.ttLib import TTFont

SOURCES = ROOT / 'test-results' / 'font-sources'
OUTPUT = ROOT / 'public' / 'fonts'


def download(url, target):
    if not target.is_file():
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read()
        target.write_bytes(data)


def main():
    SOURCES.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fonts = [
        ('song', 'Parallel Song', 'NotoSerifSC-VF.ttf', 'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf', 'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/OFL.txt'),
        ('sans', 'Parallel Sans', 'NotoSansSC-VF.ttf', 'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf', 'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt'),
        ('kai', 'Parallel Kai', None, 'https://raw.githubusercontent.com/lxgw/LxgwWenKai-Lite/main/fonts/TTF/LXGWWenKaiLite-Regular.ttf', 'https://raw.githubusercontent.com/lxgw/LxgwWenKai-Lite/main/OFL.txt'),
    ]
    notices = []
    for key, family, installed, url, license_url in fonts:
        source = Path('C:/Windows/Fonts') / installed if installed else SOURCES / 'kai.ttf'
        if not source.is_file():
            source = SOURCES / (key + '.ttf')
            download(url, source)
        license_path = OUTPUT / (key + '-OFL.txt')
        download(license_url, license_path)
        font = TTFont(source, recalcTimestamp=False)
        original = font['name'].getDebugName(1)
        # Use distinct family names for these web-delivery derivatives.
        for record in font['name'].names:
            if record.nameID in (1, 4, 6, 16, 25):
                name = family.replace(' ', '') if record.nameID in (6, 25) else family
                record.string = name.encode(record.getEncoding())
        font.flavor = 'woff2'
        destination = OUTPUT / ('reader-' + key + '.woff2')
        font.save(destination)
        font.close()
        notices.append({'file': destination.name, 'family': family, 'original': original, 'source': url, 'license': license_path.name, 'bytes': destination.stat().st_size})
        print(json.dumps(notices[-1], ensure_ascii=False), flush=True)
    (OUTPUT / 'NOTICE.json').write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
