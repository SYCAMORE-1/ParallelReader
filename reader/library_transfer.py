"""Private library transfer, kept separate from public program packaging."""
import json
import shutil
import sqlite3
import time
from pathlib import Path

SECRETS = {'apikey', 'reviewapikey', 'wordapikey', 'dictionaryapikey', 'azureapikey'}


def scrub(value):
    if isinstance(value, dict):
        return {key: '' if key.lower() in SECRETS else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def snapshot_library(source_directory, destination):
    source_directory, destination = Path(source_directory), Path(destination)
    database = source_directory / 'reader.sqlite3'
    if not database.is_file():
        raise FileNotFoundError('没有可迁移的本地书库')
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / 'reader.sqlite3').exists():
        raise FileExistsError('目标已有书库，不会覆盖')
    source = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(destination / 'reader.sqlite3')
    try:
        source.backup(target)
        now = time.time()
        reason = '迁移副本已暂停，请确认模型服务可连接后点击继续。'
        with target:
            for key, raw in target.execute('SELECT key,value FROM settings').fetchall():
                value = '' if key.lower() in SECRETS else scrub(json.loads(raw))
                target.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(value, ensure_ascii=False), key))
            target.execute("UPDATE book_translation_jobs SET state='paused',reason=?,updated=? WHERE state IN ('running','queued')", (reason, now))
            for job_id, raw in target.execute('SELECT id,value FROM reproof_jobs').fetchall():
                value = json.loads(raw)
                if value['state'] in {'queued', 'running'}:
                    value.update(state='paused', reason=reason, updated=now)
                    target.execute('UPDATE reproof_jobs SET value=? WHERE id=?', (json.dumps(value, ensure_ascii=False), job_id))
            for key, raw in target.execute("SELECT key,value FROM language_cache WHERE key LIKE 'image-book-job:%'").fetchall():
                value = json.loads(raw)
                if value['state'] in {'running', 'queued'}:
                    value.update(state='paused', reason=reason, updated=now)
                    target.execute('UPDATE language_cache SET value=? WHERE key=?', (json.dumps(value, ensure_ascii=False), key))
        target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('VACUUM')
        return {table: target.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in ['books', 'translations', 'bookmarks', 'translation_drafts', 'translation_revisions']}
    finally:
        target.close(); source.close()


def copy_library(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source.is_relative_to(destination):
        raise ValueError('书库目标不能覆盖原书库或其父目录')
    statistics = snapshot_library(source, destination)
    for name in ['books', 'sources', 'translated-images', 'exports']:
        if (source / name).is_dir():
            shutil.copytree(source / name, destination / name, ignore=shutil.ignore_patterns('*.tmp'))
    if (source / 'library-catalog.json').is_file():
        shutil.copy2(source / 'library-catalog.json', destination / 'library-catalog.json')
    return statistics
