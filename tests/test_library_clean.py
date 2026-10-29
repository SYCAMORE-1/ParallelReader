import json
import os
from pathlib import Path
import tempfile
import unittest
from reader.epub import parse_epub
from reader.storage import Store
from reader.translation import profile
from tools.clean_library import clean_library, safe_file
from test_reader import epub_fixture


class LibraryCleanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name) / '.data'
        store = Store(self.root); data = epub_fixture(); self.book = store.add(parse_epub(data), source_bytes=data)
        self.fp = profile(store.settings())
        store.cache(self.book['id'], 0, self.fp, '当前译文。')
        store.set_bookmark(self.book['id'], 0)
        store.cache_draft(self.book['id'], 1, self.fp, {'text': '未完成初译。'})
        with store.db:
            store.db.execute('INSERT INTO translation_revisions(book,paragraph,profile,job,text,detail,created) VALUES (?,?,?,?,?,?,?)', (self.book['id'], 0, self.fp, 'old-job', '旧校对副本', None, 1))
        images = self.root / 'translated-images'; images.mkdir()
        (images / 'keep.png').write_bytes(b'kept image'); (images / 'orphan.png').write_bytes(b'obsolete image')
        store.cache_language_result('image-translation:keep', {'render': {'file': 'translated-images/keep.png'}})
        exports = self.root / 'exports'; exports.mkdir()
        self.old = exports / (self.book['id'] + '-' + 'a' * 20 + '-bilingual.epub')
        self.new = exports / (self.book['id'] + '-' + 'b' * 20 + '-bilingual.epub')
        for path, timestamp in [(self.old, 1), (self.new, 2)]:
            path.write_bytes(b'export fixture'); path.with_suffix('.json').write_text('{}'); os.utime(path, (timestamp, timestamp))
        store.db.close()

    def tearDown(self): self.temp.cleanup()

    def test_preview_does_not_modify_library(self):
        result = clean_library(self.root, prune_history=True)
        self.assertFalse(result['apply']); self.assertEqual(result['obsoleteFiles'], 3)
        self.assertTrue(self.old.exists()); self.assertTrue((self.root / 'translated-images/orphan.png').exists())

    def test_apply_retains_current_versions_state_and_referenced_images(self):
        result = clean_library(self.root, apply=True, prune_history=True)
        self.assertFalse(self.old.exists()); self.assertTrue(self.new.exists())
        self.assertTrue((self.root / 'translated-images/keep.png').exists()); self.assertFalse((self.root / 'translated-images/orphan.png').exists())
        self.assertFalse((self.root / '.maintenance-backup.sqlite3').exists()); self.assertFalse((self.root / 'maintenance.lock').exists())
        store = Store(self.root)
        try:
            self.assertEqual(store.translation(self.book['id'], 0, self.fp), '当前译文。')
            self.assertEqual(store.draft(self.book['id'], 1, self.fp)['text'], '未完成初译。')
            self.assertEqual(store.bookmark(self.book['id'])['page'], 0)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM translation_revisions').fetchone()[0], 0)
            self.assertEqual(store.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        finally: store.db.close()

    def test_running_reader_is_rejected_without_deleting_data(self):
        store = Store(self.root)
        try:
            with self.assertRaisesRegex(RuntimeError, 'Close the reader'): clean_library(self.root, apply=True, prune_history=True)
            self.assertTrue((store.book_directory(self.book) / 'exports' / self.old.name).exists()); self.assertFalse((self.root / 'maintenance.lock').exists())
        finally: store.db.close()

    def test_paths_and_existing_maintenance_marker_are_protected(self):
        with self.assertRaises(ValueError): safe_file(self.root, self.root.parent / 'outside')
        marker = self.root / 'maintenance.lock'; marker.write_text('other maintenance')
        with self.assertRaises(ValueError): clean_library(self.root, apply=True)
        with self.assertRaisesRegex(RuntimeError, '正在整理'): Store(self.root)
        self.assertEqual(marker.read_text(), 'other maintenance')
