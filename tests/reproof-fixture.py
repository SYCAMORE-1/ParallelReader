"""Real HTTP/storage/worker fixture, with a local deterministic editor."""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader.reproof import selected_slots
from reader.storage import Store
from reader.translation import profile
from reader.translation_rules import finish_translation, translation_plan
from server import make_server

parser = argparse.ArgumentParser(); parser.add_argument('--data', required=True); args = parser.parse_args()
directory = Path(args.data)
store = Store(directory)
store.save_settings(store.settings() | {'model': 'QA-primary', 'reviewModel': 'QA-reviewer'})
book = {'id': 'reproof-browser', 'title': 'A Quiet Return', 'author': 'Parallel · 就地校对', 'language': 'en', 'cover': None, 'startPage': 0,
        'chapters': [{'id': i, 'title': f'Chapter {i + 1}', 'startPage': i * 3, 'endPage': i * 3 + 2} for i in range(2)],
        'pages': [{'id': i, 'chapter': i // 3, 'paragraphs': [i * 2, i * 2 + 1]} for i in range(6)],
        'paragraphs': [{'id': i, 'chapter': i // 6, 'kind': 'paragraph', 'text': f'😀 Morgan waited beside door number {i + 1}. He listened to the quiet street. Then he left.'} for i in range(12)]}
store.add(book); store.progress(book['id'], 1)
for p in book['paragraphs']:
    result = finish_translation(translation_plan(p['text'], ['Morgan']), [f'😀 Morgan在第{p["id"] + 1}扇门旁等着。', '他听着安静街道上的声音。', '随后他走了。'])
    store.cache(book['id'], p['id'], profile(store.settings()), result['text'], result)
store.db.close()

def revise(book, pid, settings, snapshot, selection):
    time.sleep(.6)
    if (directory / 'fail-review').exists():
        raise RuntimeError('模拟校对连接中断')
    detail = copy.deepcopy(snapshot['detail'])
    indices = selected_slots(book, pid, detail, selection) if selection else range(len(detail['targetSentences']))
    for index in indices:
        detail['targetSentences'][index] = f'原地新译{pid}-{index}。'
    detail['text'] = ''.join(detail['targetSentences'])
    return detail

app = make_server(port=0, data_dir=directory, sample=directory / 'missing.epub', reproof_fn=revise)
print(json.dumps({'url': f'http://127.0.0.1:{app.server_port}', 'book': book['id']}), flush=True)
try:
    app.serve_forever()
finally:
    app.translator.close(); app.server_close()
