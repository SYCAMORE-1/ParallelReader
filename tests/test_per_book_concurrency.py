import collections
import tempfile
import threading
import unittest

from reader.storage import Store
from reader.translation import Translator, profile
from reader.translation_rules import finish_translation, translation_plan
from test_reader import until


def book_fixture(identity):
    return {'id': identity, 'title': identity, 'author': 'QA', 'language': 'en', 'cover': None, 'startPage': 0,
            'chapters': [{'id': i, 'title': f'Chapter {i + 1}', 'startPage': i * 2, 'endPage': i * 2 + 1} for i in range(6)],
            'pages': [{'id': i, 'chapter': i // 2, 'paragraphs': [i]} for i in range(12)],
            'paragraphs': [{'id': i, 'chapter': i // 2, 'text': f'Paragraph {i}.', 'kind': 'paragraph'} for i in range(12)]}


class PerBookConcurrencyTests(unittest.TestCase):
    def test_whole_book_review_has_four_chapters_per_book_and_keeps_reading_reserve(self):
        fingerprint = profile(self.store.settings())
        for book in self.books[:2]:
            for p in book['paragraphs']:
                detail = finish_translation(translation_plan(p['text'], []), ['已有译文。'])
                self.store.cache(book['id'], p['id'], fingerprint, detail['text'], detail)
        calls, lock, reading = [], threading.Lock(), threading.Event()
        def revise(book, pid, settings, snapshot, selection):
            with lock: calls.append((book['id'], pid))
            self.release.wait(10)
            return snapshot['detail']
        def translate(book, pid, settings):
            reading.set()
            return '阅读译文。'
        self.worker = Translator(self.store, reproof_fn=revise, translate_fn=translate)
        for book in self.books[:2]:
            self.worker.reproofread(book, {'scope': 'book'})
        until(lambda: len(calls) == 8)
        self.assertEqual(collections.Counter(bid for bid, pid in calls), {book['id']: 4 for book in self.books[:2]})
        for book in self.books[:2]:
            self.assertEqual(self.worker._background_count(book['id'], fingerprint), 4)
            self.assertEqual(len({pid // 2 for bid, pid in calls if bid == book['id']}), 4)
        self.worker.window(self.books[2], 11, selected=11, enabled=False)
        self.assertTrue(reading.wait(3))
        self.release.set()
        until(lambda: all(self.worker.reproof.status(book)[0]['state'] == 'completed' for book in self.books[:2]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Store(self.temp.name)
        self.books = [self.store.add(book_fixture(f'book-{i}')) for i in range(3)]
        self.release = threading.Event(); self.worker = None

    def tearDown(self):
        if self.worker: self.worker.close()
        self.release.set()
        if self.worker:
            for thread in self.worker.threads: thread.join(3)
        self.store.db.close(); self.temp.cleanup()

    def test_three_books_each_get_four_chapters_and_reading_still_has_a_slot(self):
        calls, lock = [], threading.Lock()
        def fake(book, pid, settings):
            with lock: calls.append((book['id'], pid))
            self.release.wait(10)
            return '译文'
        self.worker = Translator(self.store, translate_fn=fake)
        for book in self.books: self.worker.bulk_action(book, 'start')
        until(lambda: len(calls) == 12)
        self.assertEqual(collections.Counter(book for book, pid in calls), {book['id']: 4 for book in self.books})
        for book in self.books:
            self.assertEqual({pid // 2 for bid, pid in calls if bid == book['id']}, {0, 1, 2, 3})
        self.worker.window(self.books[0], 11, selected=11, enabled=False)
        until(lambda: (self.books[0]['id'], 11) in calls)
        self.assertEqual(len(self.worker.active), 13)
        self.assertFalse(self.release.is_set())
        self.release.set()
        until(lambda: all(self.worker.bulk_status(book)['state'] == 'completed' for book in self.books))
        self.assertEqual(len(calls), 36)
        self.assertEqual(len(set(calls)), 36)

    def test_chapter_reproof_can_run_four_chapters_per_book_without_serializing_books(self):
        fingerprint = profile(self.store.settings())
        for book in self.books[:2]:
            for paragraph in book['paragraphs']:
                detail = finish_translation(translation_plan(paragraph['text'], []), ['已有译文。'])
                self.store.cache(book['id'], paragraph['id'], fingerprint, detail['text'], detail)
        calls, lock = [], threading.Lock()
        def revise(book, pid, settings, snapshot, selection):
            with lock: calls.append((book['id'], pid))
            self.release.wait(10)
            return snapshot['detail']
        self.worker = Translator(self.store, reproof_fn=revise)
        for book in self.books[:2]:
            for chapter in range(5): self.worker.reproofread(book, {'scope': 'chapter', 'chapter': chapter})
        until(lambda: len(calls) == 8)
        self.assertEqual(collections.Counter(book for book, pid in calls), {book['id']: 4 for book in self.books[:2]})
        for book in self.books[:2]:
            self.assertEqual(self.worker._background_count(book['id'], fingerprint), 4)
            self.assertEqual({pid // 2 for bid, pid in calls if bid == book['id']}, {0, 1, 2, 3})
        self.release.set()
        until(lambda: all(job['state'] == 'completed' for job in self.worker.reproof.jobs.values()))

    def test_translation_and_reproof_share_the_same_four_chapter_budget(self):
        book = self.books[0]; fingerprint = profile(self.store.settings())
        for pid in [8, 9]:
            detail = finish_translation(translation_plan(book['paragraphs'][pid]['text'], []), ['旧译。'])
            self.store.cache(book['id'], pid, fingerprint, detail['text'], detail)
        first = threading.Event(); self.release.set()
        bulk, revised = [], threading.Event()
        def translate(book, pid, settings):
            bulk.append(pid)
            if pid == 0: first.wait(5)
            else: self.release.wait(10)
            return '译文'
        def review(book, pid, settings, snapshot, selection):
            revised.set(); self.release.wait(10); return snapshot['detail']
        self.release.clear()
        self.worker = Translator(self.store, translate_fn=translate, reproof_fn=review)
        try:
            self.worker.bulk_action(book, 'start'); until(lambda: len(bulk) == 4)
            self.worker.reproofread(book, {'scope': 'chapter', 'chapter': 4})
            self.assertFalse(revised.wait(.1))
            first.set(); self.assertTrue(revised.wait(3))
            self.assertLessEqual(self.worker._background_count(book['id'], fingerprint), 4)
        finally: first.set()


if __name__ == '__main__': unittest.main()
