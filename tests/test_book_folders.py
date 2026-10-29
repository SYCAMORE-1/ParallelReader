import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing

from reader.book_folders import folder_name
from reader.epub import parse_epub
from reader.storage import Store
from test_illustrations import illustrated_epub as epub_fixture


class BookFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_book_groups_original_and_transfer_state_and_excludes_credentials(self):
        store = Store(self.root / 'library')
        data = epub_fixture(); book = store.add(parse_epub(data), source_bytes=data)
        store.save_settings(store.settings() | {'apiKey': 'secret-not-for-book-folder'})
        store.cache(book['id'], 0, 'old-profile', '已有译文。', {'text': '已有译文。'})
        store.cache_draft(book['id'], 1, 'draft-profile', {'text': '初译。'})
        store.progress(book['id'], 1)
        store.set_bookmark(book['id'], 1)
        store.save_bulk_job(book['id'], 'old-profile', 'model', 'running')
        folder = store.book_directory(book)
        store.db.close()
        self.assertIn(book['title'], folder.name)
        self.assertEqual((folder / 'original.epub').read_bytes(), data)
        self.assertEqual(json.loads((folder / 'book.json').read_text(encoding='utf-8'))['id'], book['id'])
        with closing(sqlite3.connect(folder / 'book.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT text FROM translations').fetchone()[0], '已有译文。')
            self.assertEqual(db.execute('SELECT state FROM book_translation_jobs').fetchone()[0], 'paused')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='settings'").fetchone())
        self.assertNotIn(b'secret-not-for-book-folder', (folder / 'book.sqlite3').read_bytes())

    def test_copying_only_one_renamed_folder_restores_all_its_data(self):
        source = Store(self.root / 'source')
        data = epub_fixture(); book = source.add(parse_epub(data), source_bytes=data)
        second = dict(book, id='other-book', title='Leave this book behind')
        source.add(second)
        source.cache(second['id'], 0, 'profile', '不搬走的书。')
        source.cache(book['id'], 0, 'profile', '需要搬走的译文。', {'text': '需要搬走的译文。'})
        source.cache_draft(book['id'], 1, 'draft', {'text': '初译也要保留。'})
        source.set_bookmark(book['id'], 1)
        source.save_bulk_job(book['id'], 'profile', 'model', 'running')
        directory = source.book_directory(book)
        image = directory / 'translated-images'; image.mkdir()
        (image / 'sample.png').write_bytes(b'image-fixture')
        source.cache_language_result('image-translation:sample', {'render': {'file': (image / 'sample.png').relative_to(source.directory).as_posix()}}, book=book['id'])
        source.db.close()
        destination = self.root / 'destination'
        copied = destination / 'books' / 'A renamed book folder'
        shutil.copytree(directory, copied)
        restored = Store(destination)
        try:
            self.assertEqual([b['id'] for b in restored.library()], [book['id']])
            self.assertEqual(restored.translation(book['id'], 0, 'profile'), '需要搬走的译文。')
            self.assertEqual(restored.draft(book['id'], 1, 'draft')['text'], '初译也要保留。')
            self.assertEqual(restored.bookmark(book['id'])['page'], 1)
            self.assertEqual(restored.bulk_jobs()[0]['state'], 'paused')
            cached = restored.language_result('image-translation:sample')
            self.assertEqual((restored.directory / cached['render']['file']).read_bytes(), b'image-fixture')
            self.assertEqual(restored.source_path(book).read_bytes(), data)
            self.assertEqual(restored.book_directory(book).name, copied.name)
            with self.assertRaises(KeyError): restored.book(second['id'])
        finally: restored.db.close()

    def test_moving_folder_out_hides_it_without_deleting_shared_database_state(self):
        store = Store(self.root / 'library')
        data = epub_fixture(); book = store.add(parse_epub(data), source_bytes=data)
        store.cache(book['id'], 0, 'profile', '保留的译文。')
        folder = store.book_directory(book); store.db.close()
        outside = self.root / 'selected-for-transfer'
        shutil.move(str(folder), str(outside))
        store = Store(self.root / 'library')
        try:
            self.assertEqual(store.library(), [])
            self.assertEqual(store.translation(book['id'], 0, 'profile'), '保留的译文。')
            self.assertFalse(folder.exists())
        finally: store.db.close()
        shutil.move(str(outside), str(folder))
        store = Store(self.root / 'library')
        try: self.assertEqual(store.book(book['id'])['title'], book['title'])
        finally: store.db.close()

    def test_old_flat_layout_migrates_without_changing_text_or_position(self):
        store = Store(self.root / 'library')
        data = epub_fixture(); book = store.add(parse_epub(data), source_bytes=data)
        store.cache(book['id'], 0, 'profile', '迁移前译文。')
        store.progress(book['id'], 1)
        folder = store.book_directory(book); store.db.close()
        legacy = self.root / 'library' / 'sources'; legacy.mkdir()
        (folder / 'original.epub').replace(legacy / (book['id'] + '.epub'))
        shutil.rmtree(folder)
        with closing(sqlite3.connect(self.root / 'library' / 'reader.sqlite3')) as db:
            db.execute('DELETE FROM book_locations')
            db.commit()
        store = Store(self.root / 'library')
        try:
            self.assertEqual(store.source_path(book).read_bytes(), data)
            self.assertEqual(store.translation(book['id'], 0, 'profile'), '迁移前译文。')
            self.assertEqual(store.book(book['id'])['currentPage'], 1)
            self.assertFalse((legacy / (book['id'] + '.epub')).exists())
        finally: store.db.close()

    def test_names_are_safe_and_same_title_editions_stay_separate(self):
        first = {'id': 'a' * 24, 'title': 'A: Book / with? invalid*characters'}
        second = dict(first, id='b' * 24)
        self.assertNotEqual(folder_name(first), folder_name(second))
        self.assertFalse(any(c in folder_name(first) for c in '<>:"/\\|?*'))

    def test_copied_folder_with_changed_original_is_rejected(self):
        source = Store(self.root / 'source'); data = epub_fixture()
        book = source.add(parse_epub(data), source_bytes=data)
        directory = source.book_directory(book); source.db.close()
        destination = self.root / 'destination'; copied = destination / 'books' / directory.name
        shutil.copytree(directory, copied); (copied / 'original.epub').write_bytes(b'wrong book')
        with self.assertRaisesRegex(ValueError, '原书与编号不匹配'):
            Store(destination)
