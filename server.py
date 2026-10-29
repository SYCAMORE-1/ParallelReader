"""Parallel Reader: local EPUB library and OpenAI-compatible translation proxy."""
import argparse
import ipaddress
import json
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from reader.epub import MAX_UPLOAD, parse_epub
from reader.export import BookExporter
from reader.storage import Store
from reader.language import LanguageTools
from reader.image_translation import ImageTranslator
from reader.translation import Translator, draft_settings, profile, request_json
from reader.providers import review_enabled

ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE = ROOT / '.data' / 'sample.epub'


def validate_settings(value, previous):
    provider = value.get('translationProvider', previous.get('translationProvider', 'llm'))
    if provider not in {'llm', 'microsoft'}:
        raise ValueError('请选择支持的翻译服务')
    word_provider = value.get('wordProvider', previous.get('wordProvider', 'microsoft'))
    if word_provider not in {'microsoft', 'llm'}:
        raise ValueError('请选择支持的单词翻译服务')
    base = str(value.get("baseUrl", previous["baseUrl"])).strip().rstrip("/")
    url = urlparse(base)
    if (provider == 'llm' or base) and (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ValueError("请输入有效的 HTTP(S) 模型接口地址")
    model = str(value.get("model", previous["model"])).strip()
    if (provider == 'llm' and not model) or len(model) > 200:
        raise ValueError("请输入模型名称")
    language = value.get("targetLanguage", previous["targetLanguage"])
    if language not in ("简体中文", "繁體中文", "日本語"):
        raise ValueError("不支持的目标语言")
    review_enabled = value.get('reviewEnabled', previous.get('reviewEnabled', True))
    if type(review_enabled) is not bool:
        raise ValueError('校对开关必须是布尔值')
    review_base = str(value.get('reviewBaseUrl', previous.get('reviewBaseUrl', ''))).strip().rstrip('/')
    review_model = str(value.get('reviewModel', previous.get('reviewModel', ''))).strip()
    review_url = urlparse(review_base)
    if ((provider == 'llm' and review_enabled) or review_base) and (review_url.scheme not in ('http', 'https') or not review_url.hostname or review_url.username or review_url.password or review_url.query or review_url.fragment):
        raise ValueError('请输入有效的校对模型 HTTP(S) 地址')
    if (provider == 'llm' and review_enabled and not review_model) or len(review_model) > 200:
        raise ValueError('请输入校对模型名称')
    word_base = str(value.get('wordBaseUrl', previous.get('wordBaseUrl', ''))).strip().rstrip('/')
    word_model = str(value.get('wordModel', previous.get('wordModel', ''))).strip()
    word_url = urlparse(word_base)
    if bool(word_base) != bool(word_model) or len(word_model) > 200:
        raise ValueError('查词接口地址和模型名称需要同时填写')
    if word_provider == 'llm' and (not word_base or not word_model):
        raise ValueError('请单独填写查词 LLM 的接口地址和模型名称')
    if word_base and (word_url.scheme not in ('http', 'https') or not word_url.hostname or word_url.username or word_url.password or word_url.query or word_url.fragment):
        raise ValueError('请输入有效的查词模型 HTTP(S) 地址')
    return {"translationProvider": provider, 'wordProvider': word_provider,
            "baseUrl": base, "model": model, "targetLanguage": language, "apiKey": "" if value.get("clearApiKey") else (str(value.get("apiKey", "")).strip() or previous.get("apiKey", "")),
            'reviewEnabled': review_enabled, 'reviewBaseUrl': review_base, 'reviewModel': review_model,
            'reviewApiKey': '' if value.get('clearReviewApiKey') else (str(value.get('reviewApiKey', '')).strip() or previous.get('reviewApiKey', '')),
            'wordBaseUrl': word_base, 'wordModel': word_model,
            'wordApiKey': '' if value.get('clearWordApiKey') else (str(value.get('wordApiKey', '')).strip() or previous.get('wordApiKey', ''))}


def make_server(host="127.0.0.1", port=8765, data_dir=None, sample=DEFAULT_SAMPLE, translate_fn=None, allowed_hosts=None, reproof_fn=None, image_fn=None, image_render_fn=None):
    allowed_addresses = {'127.0.0.1'}
    for address in [*(allowed_hosts or []), *([host] if host not in {'127.0.0.1', 'localhost', '0.0.0.0'} else [])]:
        parsed_address = ipaddress.IPv4Address(address)
        if parsed_address.is_unspecified or parsed_address.is_multicast:
            raise ValueError('预览地址必须是电脑的实际 IPv4 地址')
        allowed_addresses.add(str(parsed_address))
    store = Store(data_dir or ROOT / ".data")
    image_translator = ImageTranslator(store, read_fn=image_fn, render_fn=image_render_fn)
    store.image_service = image_translator
    store.image_progress = image_translator.book_jobs.library
    translator = Translator(store, reproof_fn=reproof_fn, image_translator=image_translator, **({"translate_fn": translate_fn} if translate_fn else {}))
    language = LanguageTools(store)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            if len(args) > 1 and str(args[1]).startswith("5"):
                super().log_message(format, *args)

        def respond(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def body(self, limit=64 * 1024):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("不支持分块上传")
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > limit:
                raise ValueError("请求为空或超过大小限制")
            data = self.rfile.read(size)
            if len(data) != size:
                raise ValueError("上传未完成")
            return data

        def json_body(self):
            value = json.loads(self.body())
            if not isinstance(value, dict):
                raise ValueError("请求需要 JSON 对象")
            return value

        def guard(self):
            actual_port = self.server.server_port
            allowed = {f"{address}:{actual_port}" for address in allowed_addresses} | {f"localhost:{actual_port}"}
            host_header = self.headers.get("Host", "")
            # A wildcard listener must not expose the library through VPN or
            # other adapters when only the Wi-Fi address was enabled.
            if self.connection.getsockname()[0] not in allowed_addresses or host_header not in allowed:
                self.respond({"error": "此地址未启用阅读预览"}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host_header:
                self.respond({"error": "不允许跨站请求"}, 403)
                return False
            if self.command == "POST" and self.headers.get_content_type() not in {"application/json", "application/epub+zip"}:
                self.respond({"error": "不支持的请求格式"}, 415)
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            parsed = urlparse(self.path)
            parts = parsed.path.strip("/").split("/")
            try:
                if parsed.path == "/api/health":
                    return self.respond({"ok": True, "version": "0.1.0"})
                if parsed.path == "/api/books":
                    settings = store.settings()
                    return self.respond(store.library(profile(settings), profile(draft_settings(settings)) if review_enabled(settings) else None))
                if parsed.path == '/api/library-progress':
                    settings = store.settings()
                    fingerprint = profile(settings)
                    return self.respond({'profile': fingerprint, 'books': store.library_progress(fingerprint, profile(draft_settings(settings)) if review_enabled(settings) else None)})
                if parsed.path == "/api/sample":
                    return self.respond({"available": sample.is_file(), "name": sample.name})
                if parsed.path == "/api/settings":
                    settings = store.settings()
                    return self.respond({**settings, "apiKey": "", "hasApiKey": bool(settings.get("apiKey")),
                                         'reviewApiKey': '', 'hasReviewApiKey': bool(settings.get('reviewApiKey')),
                                         'wordApiKey': '', 'hasWordApiKey': bool(settings.get('wordApiKey')),
                                         "profile": profile(settings)})
                if parts[:2] == ['api', 'books'] and len(parts) == 5 and parts[3] == 'image-translations':
                    return self.respond(image_translator.status(store.book(parts[2]), parts[4]))
                if parts[:2] == ['api', 'books'] and len(parts) == 5 and parts[3] == 'translated-images':
                    data, digest = image_translator.rendered(store.book(parts[2]), parts[4])
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
                    self.send_header('ETag', '"' + digest + '"')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if parts[:2] == ['api', 'books'] and len(parts) == 5 and parts[3] == 'images':
                    book = store.book(parts[2])
                    data, mime = store.image(book, parts[4])
                    etag = '"' + book['id'] + '-' + parts[4] + '"'
                    if self.headers.get('If-None-Match') == etag:
                        self.send_response(304)
                        self.send_header('ETag', etag)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
                    self.send_header('ETag', etag)
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if parts[:2] == ["api", "books"] and len(parts) in (3, 4):
                    book = store.book(parts[2])
                    if len(parts) == 3:
                        return self.respond(book)
                    if parts[3] == 'translation-sources':
                        from reader.reading_sources import progress
                        return self.respond(progress(store, book, store.settings()))
                    if parts[3] == 'export':
                        kind = parse_qs(parsed.query).get('format', ['bilingual'])[0]
                        path = self.server.exporter.export(book, store.settings(), kind, reading=True)
                        data = path.read_bytes()
                        title = book['title'] + ('-双语对照.epub' if kind == 'bilingual' else '-译文.epub')
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/epub+zip')
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Content-Disposition', "attachment; filename=translated-book.epub; filename*=UTF-8''" + quote(title, safe=''))
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    if parts[3] == 'pretranslate':
                        return self.respond(translator.bulk_status(book))
                    if parts[3] == 'reproofread':
                        with translator.condition:
                            return self.respond({'jobs': translator.reproof.status(book)})
                    if parts[3] == 'bookmark':
                        return self.respond(store.bookmark(book['id']))
                    if parts[3] == "status":
                        page = int(parse_qs(parsed.query).get("page", ["0"])[0])
                        if not 0 <= page < len(book["pages"]):
                            raise ValueError("页码超出范围")
                        end_page = int(parse_qs(parsed.query).get('endPage', [str(page)])[0])
                        if not page <= end_page < min(len(book['pages']), page + 8):
                            raise ValueError('阅读范围超出限制')
                        status = translator.status(book, page, end_page=end_page)
                        settings = store.settings()
                        for pid, item in status['items'].items():
                            if item['state'] == 'ready':
                                item.update(language.view(book, pid, item['text'], settings, item.get('translationProfile')))
                        from reader.reading_sources import progress
                        status['readingTranslation'] = progress(store, book, settings)
                        return self.respond(status)
                if parsed.path.startswith("/api/"):
                    return self.respond({"error": "接口不存在"}, 404)
                allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/reading-tools.js": "reading-tools.js", "/style.css": "style.css", "/reading.css": "reading.css", "/favicon.svg": "favicon.svg"}
                allowed['/reproof-tools.js'] = 'reproof-tools.js'
                allowed['/image-translation.js'] = 'image-translation.js'
                allowed['/translation-sources.js'] = 'translation-sources.js'
                for name in ('reader-song.woff2', 'reader-sans.woff2', 'reader-kai.woff2', 'song-OFL.txt', 'sans-OFL.txt', 'kai-OFL.txt', 'NOTICE.json'):
                    allowed['/fonts/' + name] = 'fonts/' + name
                filename = allowed.get(parsed.path)
                if not filename:
                    return self.respond({"error": "文件不存在"}, 404)
                data = (ROOT / "public" / filename).read_bytes()
                self.send_response(200)
                is_font = filename.endswith('.woff2')
                self.send_header("Content-Type", 'font/woff2' if is_font else (mimetypes.guess_type(filename)[0] or "application/octet-stream") + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400" if is_font else "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
            except KeyError:
                self.respond({"error": "书籍不存在"}, 404)
            except (ValueError, TypeError) as exc:
                self.respond({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except OSError as exc:
                self.respond({'error': '读取或保存文件失败：' + str(exc)[:250]}, 502)

        def do_POST(self):
            if not self.guard():
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                if parts == ["api", "import"]:
                    if self.headers.get_content_type() != "application/epub+zip":
                        raise ValueError("请上传 EPUB 文件")
                    data = self.body(MAX_UPLOAD)
                    book = store.add(parse_epub(data), source_bytes=data)
                    return self.respond(book)
                value = self.json_body()
                if parts == ["api", "import-sample"]:
                    if not sample.is_file():
                        raise ValueError("找不到测试 EPUB，请使用“导入 EPUB”选择文件")
                    if sample.stat().st_size > MAX_UPLOAD:
                        raise ValueError("EPUB 超过 100 MB 导入限制")
                    data = sample.read_bytes()
                    return self.respond(store.add(parse_epub(data, sample.name), source_bytes=data))
                if parts == ["api", "settings"]:
                    with translator.condition:
                        settings = validate_settings(value, store.settings())
                        store.save_settings(settings)
                        translator.settings_changed()
                    return self.respond({"ok": True, "profile": profile(settings)})
                if parts == ["api", "model", "test"]:
                    settings = validate_settings(value, store.settings())
                    if settings['translationProvider'] == 'microsoft':
                        from reader.microsoft_translation import CLIENT
                        CLIENT.translate(settings, ['<p>Hello.</p>'])
                        return self.respond({'ok': True, 'provider': 'microsoft'})
                    result = request_json(settings["baseUrl"] + "/models", api_key=settings["apiKey"], timeout=12)
                    models = [m["id"] for m in result.get("data", [])]
                    if settings["model"] not in models:
                        raise ValueError("接口可连，但未找到此模型。可用模型：" + ", ".join(models))
                    review_models = []
                    if settings['reviewEnabled']:
                        result = request_json(settings['reviewBaseUrl'] + '/models', api_key=settings['reviewApiKey'], timeout=12)
                        review_models = [m['id'] for m in result.get('data', [])]
                        if settings['reviewModel'] not in review_models:
                            raise ValueError('校对接口可连，但未找到此模型。可用模型：' + ', '.join(review_models))
                    word_models = []
                    if settings.get('wordProvider') == 'llm' and settings.get('wordBaseUrl') and settings.get('wordModel'):
                        result = request_json(settings['wordBaseUrl'] + '/models', api_key=settings.get('wordApiKey', ''), timeout=12)
                        word_models = [m['id'] for m in result.get('data', [])]
                        if settings['wordModel'] not in word_models:
                            raise ValueError('查词接口可连，但未找到此模型。可用模型：' + ', '.join(word_models))
                    return self.respond({"ok": True, "models": models, 'reviewModels': review_models, 'wordModels': word_models})
                if parts[:2] == ['api', 'books'] and len(parts) == 5 and parts[3] == 'image-translations':
                    return self.respond(image_translator.request(store.book(parts[2]), parts[4], value.get('retry', False)), 202)
                if parts[:2] == ["api", "books"] and len(parts) == 4:
                    book = store.book(parts[2])
                    if parts[3] == 'translation-sources':
                        from reader.reading_sources import choose
                        with translator.condition:
                            result = choose(store, book, store.settings(), value.get('sourceProfile'), value.get('configurationProfile'))
                            translator.desired.clear(); translator.heap.clear()
                            translator.condition.notify_all()
                        return self.respond(result)
                    if parts[3] == 'remove':
                        translator.remove_book(book['id'])
                        image_translator.remove_book(book['id'])
                        return self.respond({'ok': True})
                    if parts[3] == 'pretranslate':
                        return self.respond(translator.bulk_action(book, value.get('action')))
                    if parts[3] == 'reproofread':
                        return self.respond(translator.reproofread(book, value), 202)
                    if parts[3] == 'bookmark':
                        return self.respond(store.set_bookmark(book['id'], value.get('page'), value.get('manual', True), value.get('layoutVersion')))
                    if parts[3] in ('align', 'word'):
                        pid = value.get('paragraph')
                        if type(pid) is not int or not 0 <= pid < len(book['paragraphs']):
                            raise ValueError('段落不存在')
                        settings = store.settings()
                        if parts[3] == 'word':
                            return self.respond(language.explain(book, pid, value.get('word'), settings, offset=value.get('offset')))
                        from reader.reading_sources import translations as reading_translations
                        _, saved = reading_translations(store, book, [pid], settings)
                        if pid not in saved:
                            raise ValueError('请先等待此段落的译文就绪')
                        return self.respond(language.align(book, pid, saved[pid]['text'], settings, saved[pid]['profile']))
                    page = int(value.get("page", 0))
                    if not 0 <= page < len(book["pages"]):
                        raise ValueError("页码超出范围")
                    if parts[3] == "progress":
                        store.progress(book["id"], page, value.get('layoutVersion'))
                        return self.respond({"ok": True, 'bookmark': store.bookmark(book['id'])})
                    if parts[3] == "window":
                        end_page = int(value.get('endPage', page))
                        if not page <= end_page < min(len(book['pages']), page + 8):
                            raise ValueError('阅读范围超出限制')
                        selected = value.get("selected")
                        visible_ids = {pid for index in range(page, end_page + 1) for pid in book['pages'][index]['paragraphs']}
                        if selected is not None and (type(selected) is not int or selected not in visible_ids):
                            raise ValueError("所选段落不在当前页")
                        fingerprint = translator.window(book, page, selected, bool(value.get("retry")), bool(value.get("enabled", True)), end_page=end_page)
                        from reader.reading_sources import selection
                        return self.respond({"profile": fingerprint, 'needsTranslationChoice': selection(store, book, store.settings())['selectionRequired']})
                self.respond({"error": "接口不存在"}, 404)
            except KeyError:
                self.respond({"error": "书籍不存在"}, 404)
            except (ValueError, TypeError, OverflowError) as exc:
                self.respond({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                self.respond({"error": str(exc)[:350]}, 502)

    class ReaderServer(ThreadingHTTPServer):
        def server_close(self):
            image_translator.close()
            if hasattr(self, 'exporter'):
                self.exporter.close()
            super().server_close()

    server = ReaderServer((host, port), Handler)
    server.daemon_threads = True
    server.store = store
    server.translator = translator
    server.image_translator = image_translator
    server.exporter = BookExporter(store)
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parallel Reader · 本地双语 EPUB 阅读器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；局域网预览使用 0.0.0.0")
    parser.add_argument("--allow-host", action="append", default=[], help="允许手机访问的电脑 IPv4 地址，可多次指定")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    args = parser.parse_args()
    app = make_server(host=args.host, port=args.port, sample=args.sample, allowed_hosts=args.allow_host)
    url = f"http://127.0.0.1:{app.server_port}"
    print(f"Parallel Reader ready: {url}", flush=True)
    for address in args.allow_host:
        print(f"Phone preview: http://{address}:{app.server_port}", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.translator.close()
        # Finish in-flight writes before finalising the portable book folders.
        for worker in app.translator.threads:
            worker.join()
        app.image_translator.close(wait=True)
        app.server_close()
        app.store.db.close()

