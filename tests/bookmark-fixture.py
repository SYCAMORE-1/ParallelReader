import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader.storage import Store
from reader.translation import profile
from reader.text import split_sentences
from server import make_server

parser = argparse.ArgumentParser(); parser.add_argument('--data', required=True); args = parser.parse_args()
directory = Path(args.data); store = Store(directory)
for name in ['a', 'b']:
    book = {'id': 'bookmark-' + name, 'title': 'Bookmark ' + name.upper(), 'author': 'Parallel · 单书签测试', 'cover': None, 'language': 'en', 'startPage': 0,
            'chapters': [{'id': i, 'title': f'Chapter {i + 1}', 'startPage': i * 5, 'endPage': i * 5 + 4} for i in range(2)],
            'pages': [{'id': i, 'chapter': i // 5, 'paragraphs': [i]} for i in range(10)],
            'paragraphs': [{'id': i, 'chapter': i // 5, 'kind': 'paragraph', 'text': f'At house number {i + 1}, the traveler waited by the garden gate. ' + 'He watched the quiet street and listened to the wind moving through the trees. ' * 5} for i in range(10)]}
    store.add(book); store.progress(book['id'], 6 if name == 'a' else 2)
    for paragraph in book['paragraphs']:
        source = split_sentences(paragraph['text'])
        target = ['旅人在第' + str(paragraph['id'] + 1) + '栋房子的花园门边等候。'] + ['他望着安静的街道，听着风吹过树梢。' for _ in source[1:]]
        result = {'text': ''.join(target), 'sourceSentences': source, 'targetSentences': target,
                  'alignment': [{'source': [i], 'target': [i]} for i in range(len(source))], 'alignmentMethod': 'translation-slots'}
        store.cache(book['id'], paragraph['id'], profile(store.settings()), result['text'], result)
store.db.close()
app = make_server(port=0, data_dir=directory, sample=directory / 'missing.epub')
print(json.dumps({'url': f'http://127.0.0.1:{app.server_port}'}), flush=True)
try: app.serve_forever()
finally: app.translator.close(); app.server_close()
