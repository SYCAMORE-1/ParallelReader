import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from reader.storage import Store
from reader.image_render import chinese_font
from reader.library_transfer import snapshot_library
from test_reader import queue_book

spec=importlib.util.spec_from_file_location('release_builder',Path(__file__).resolve().parents[1]/'tools'/'build_release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def test_snapshot_includes_wal_preserves_library_and_scrubs_only_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=Store(root/'.data');destination=root/'release-data';destination.mkdir()
            try:
                book=source.add(queue_book());source.progress(book['id'],4);source.set_bookmark(book['id'],7)
                source.cache(book['id'],0,'profile','已有中文译文')
                source.save_settings(source.settings()|{'apiKey':'private-primary-sentinel-XYZ','reviewApiKey':'private-review-sentinel-XYZ'})
                source.save_bulk_job(book['id'],'profile','model','running')
                source.save_reproof_job({'id':'review','state':'running'})
                source.cache_language_result('image-book-job:sample',{'state':'running'})
                self.assertGreater((root/'.data'/'reader.sqlite3-wal').stat().st_size,0)
                stats=snapshot_library(root/'.data',destination)
                self.assertEqual((stats['books'],stats['translations'],stats['bookmarks']),(1,1,1))
                target=sqlite3.connect(destination/'reader.sqlite3')
                try:
                    settings=json.loads(target.execute("SELECT value FROM settings WHERE key='model'").fetchone()[0])
                    self.assertEqual((settings['apiKey'],settings['reviewApiKey']),('',''))
                    self.assertEqual(target.execute('SELECT page FROM books').fetchone()[0],4)
                    self.assertEqual(target.execute('SELECT page FROM bookmarks').fetchone()[0],7)
                    self.assertEqual(target.execute('SELECT text FROM translations').fetchone()[0],'已有中文译文')
                    self.assertEqual(target.execute('SELECT state FROM book_translation_jobs').fetchone()[0],'paused')
                    self.assertEqual(json.loads(target.execute('SELECT value FROM reproof_jobs').fetchone()[0])['state'],'paused')
                    self.assertEqual(json.loads(target.execute('SELECT value FROM language_cache').fetchone()[0])['state'],'paused')
                finally:target.close()
                # Updates to the copied DB must leave no original credential in free pages.
                self.assertNotIn(b'private-primary-sentinel-XYZ',(destination/'reader.sqlite3').read_bytes())
                self.assertNotIn(b'private-review-sentinel-XYZ',(destination/'reader.sqlite3').read_bytes())
                self.assertEqual(source.settings()['apiKey'],'private-primary-sentinel-XYZ')
                self.assertEqual(source.bulk_jobs()[0]['state'],'running')
                self.assertEqual(source.reproof_jobs()[0]['state'],'running')
            finally:source.db.close()

    def test_bundled_font_has_no_windows_font_dependency(self):
        root=Path(__file__).resolve().parents[1]
        with patch.object(Path,'is_file',lambda path:path==root/'public'/'fonts'/'reader-sans.ttf'):
            self.assertEqual(chinese_font(),root/'public'/'fonts'/'reader-sans.ttf')

    def test_sample_path_is_inside_project(self):
        import server
        self.assertTrue(server.DEFAULT_SAMPLE.is_relative_to(server.ROOT/'.data'))
