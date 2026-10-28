"""Isolated, deterministic browser fixture: never calls a real model."""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader.storage import Store
from server import make_server

parser = argparse.ArgumentParser()
parser.add_argument('--data', required=True)
args = parser.parse_args()
# Paragraph 35 intentionally fails validation to exercise the retry UI.
logging.getLogger('reader.translation').setLevel(logging.ERROR)
directory = Path(args.data)
store = Store(directory)
store.save_settings(store.settings() | {'model': 'QA-model', 'baseUrl': 'http://127.0.0.1:1/v1'})
book = {'id': 'bulk-browser-fixture', 'title': 'A Quiet Journey', 'author': 'Parallel · 预翻译测试',
        'language': 'en', 'cover': None, 'startPage': 0,
        'chapters': [{'id': i, 'title': title, 'startPage': i * 5, 'endPage': i * 5 + 4}
                     for i, title in enumerate(['The Arrival', 'The Quiet Street', 'A New Home', 'The Journey Ahead'])],
        'pages': [{'id': i, 'chapter': i // 5, 'paragraphs': list(range(i * 4, i * 4 + 4))} for i in range(20)],
        'paragraphs': [{'id': i, 'chapter': i // 20, 'kind': 'paragraph', 'text': f'On the quiet street, the traveler paused beside house number {i + 1}.'} for i in range(80)]}
store.add(book)
store.db.close()

def fake(book, pid, settings):
    time.sleep(.45)
    # One stable validation failure exercises the visible retry state. A
    # marker allows the browser to recover it without changing production APIs.
    if pid == 35 and not (directory / 'recovered').exists():
        raise RuntimeError('模型没有返回有效译文，请重试')
    text = f'旅人在安静的街道上，停在第{pid + 1}号房屋旁。'
    return {'text': text, 'sourceSentences': [book['paragraphs'][pid]['text']], 'targetSentences': [text],
            'alignment': [{'source': [0], 'target': [0]}], 'alignmentMethod': 'translation-slots'}

app = make_server(port=0, data_dir=directory, sample=directory / 'missing.epub', translate_fn=fake)
print(json.dumps({'url': f'http://127.0.0.1:{app.server_port}', 'book': book['id']}), flush=True)
try:
    app.serve_forever()
finally:
    app.translator.close()
    app.server_close()
