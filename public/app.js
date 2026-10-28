import { ReadingTools, readingTop } from './reading-tools.js';
import { ReproofTools } from './reproof-tools.js';
import { ImageTranslationTools } from './image-translation.js';
import { TranslationSources } from './translation-sources.js';
history.scrollRestoration = 'manual';
const $ = (selector) => document.querySelector(selector);
const state = { book: null, page: 0, selected: null, profile: '', settings: {}, library: [], items: {}, cache: new Map(), epoch: 0, timer: null, prefetch: localStorage.getItem('parallel.prefetch') !== 'false', windowChain: Promise.resolve(), progressChain: Promise.resolve() };
let bookTranslation = null, bulkBusy = false, bulkRevision = 0;
const bookReproofBusy = new Set();
let bookmarkBusy = false;
const libraryViews = ['list', 'covers', 'covers3'];
let libraryView = localStorage.getItem('parallel.libraryView');
if (!libraryViews.includes(libraryView)) libraryView = 'covers';
const removingBooks = new Set();
let libraryProgressBusy = false;

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', '#i-' + name); svg.append(use); return svg;
}
function element(tag, className, text) {
  const node = document.createElement(tag); if (className) node.className = className;
  if (text !== undefined) node.textContent = text; return node;
}
async function api(url, data, options = {}) {
  const response = await fetch(url, { ...(data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }), signal: AbortSignal.timeout(95000), ...options });
  const value = await response.json(); if (!response.ok) throw new Error(value.error || `请求失败 (${response.status})`); return value;
}
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6500); }
function report(error) { toast(error.message || '连接中断，请确认阅读器服务正在运行'); }
function bookUrl(suffix = '') { return '/api/books/' + state.book.id + suffix; }
const reading = new ReadingTools({ state, api, icon, element, selectParagraph, updateParagraph, openModel: showSettings });
const reproof = new ReproofTools({ state, api, toast, report, reading, refresh: () => { clearTimeout(state.timer); poll(state.epoch); } });
const imageTranslation = new ImageTranslationTools({ state, api, element });
const translationSources = new TranslationSources({ state, api });
$('#translation-source-button').onclick = async () => {
  if (!state.book) return;
  ++state.epoch; clearTimeout(state.timer);
  try {
    if (await translationSources.resolve(state.book, {force: true})) {
      state.items = {}; reading.resetPage(); reproof.reset(); renderPage(); renderBookTranslation(); scheduleWindow();
    }
  } catch (error) { report(error); }
};
let flow = localStorage.getItem('parallel.flow') === 'scroll' ? 'scroll' : 'pages';
let renderStart = 0, renderEnd = 0, pageBackward = false, scrollFrame = 0, progressTimer, lastScrollY = 0, scrollRestoring = false;
function syncFlow() {
  document.body.dataset.flow = flow;
  for (const name of ['pages', 'scroll']) $('#flow-' + name).setAttribute('aria-pressed', String(flow === name));
  $('.flow-switch').style.setProperty('--selection-index', flow === 'scroll' ? 1 : 0);
  $('#scroll-status').hidden = flow !== 'scroll';
}
syncFlow();
requestAnimationFrame(() => requestAnimationFrame(() => { $('.reading-mode-controls').dataset.ready = 'true'; }));
for (const name of ['pages', 'scroll']) $('#flow-' + name).onclick = () => {
  if (name === flow) return;
  flow = name; localStorage.setItem('parallel.flow', flow); syncFlow();
  if (state.book) { saveProgress(); ++state.epoch; reading.resetPage(); renderPage(); if (flow === 'pages') scrollToReadingPage(); scheduleWindow(); }
};
function windowEnd() { return flow === 'scroll' ? Math.min(renderEnd, state.page + 3) : renderEnd; }
function renderedIds() { return [...document.querySelectorAll('.paragraph-row')].map(row => Number(row.dataset.pid)); }
function headingKey(text) { return text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, ''); }
let illustrationViewer;
function openIllustration(src, alt, bookId, asset) {
  if (!illustrationViewer) {
    const dialog = element('dialog', 'illustration-viewer'), toolbar = element('div', 'illustration-viewer-toolbar');
    const title = element('span', '', '原书插图'), canvas = element('div', 'illustration-canvas'), image = element('img');
    const body = element('div', 'illustration-viewer-body'), side = element('div', 'illustration-image-tools');
    const fit = () => { if (image.naturalWidth) image.style.width = Math.min(image.naturalWidth, canvas.clientWidth - 24) + 'px'; };
    toolbar.append(title);
    for (const [label, action, name] of [['−', () => { image.style.width = Math.max(100, image.getBoundingClientRect().width / 1.35) + 'px'; }, '缩小插图'], ['＋', () => { image.style.width = Math.min(image.naturalWidth * 3, image.getBoundingClientRect().width * 1.35) + 'px'; }, '放大插图'], ['适应', fit, '适应窗口'], ['关闭', () => dialog.close(), '关闭插图']]) {
      const button = element('button', '', label); button.type = 'button'; button.setAttribute('aria-label', name); button.onclick = action; toolbar.append(button);
    }
    dialog.setAttribute('aria-label', '原书插图查看器'); image.alt = '原书插图'; image.onload = fit;
    canvas.append(image); body.append(canvas); dialog.append(toolbar, side, body); document.body.append(dialog);
    illustrationViewer = { dialog, image, fit, side };
    window.addEventListener('resize', () => { if (dialog.open) requestAnimationFrame(fit); });
  }
  illustrationViewer.side.replaceChildren(); illustrationViewer.image.src = src;
  illustrationViewer.translationView = imageTranslation.attach(illustrationViewer.side, bookId, asset, {
    image: illustrationViewer.image, originalSrc: src
  });
  illustrationViewer.image.alt = alt || '原书插图';
  illustrationViewer.dialog.showModal();
  illustrationViewer.translationView.load();
  requestAnimationFrame(illustrationViewer.fit);
}
function illustrationRow(index, page) {
  const item = state.book.illustrations[index], asset = state.book.images[item.asset];
  const figure = element('figure', 'illustration-row'), button = element('button', 'illustration-open'), image = element('img', 'illustration-image');
  figure.dataset.page = page; figure.dataset.illustration = index;
  image.src = bookUrl('/images/' + item.asset); image.alt = item.alt || '原书插图'; image.loading = 'lazy'; image.decoding = 'async';
  if (asset.width && asset.height) { image.width = asset.width; image.height = asset.height; }
  button.type = 'button'; button.title = '点击放大插图'; button.setAttribute('aria-label', '放大原书插图');
  const error = element('figcaption', 'illustration-error', '插图读取失败，点击重试'); error.hidden = true;
  image.onerror = () => { error.hidden = false; };
  image.onload = () => { error.hidden = true; if (flow === 'scroll') requestAnimationFrame(checkScroll); };
  button.onclick = () => {
    if (error.hidden) openIllustration(bookUrl('/images/' + item.asset), image.alt, state.book.id, item.asset);
    else { error.hidden = true; image.src = bookUrl('/images/' + item.asset) + '?retry=' + Date.now(); }
  };
  button.append(image); figure.append(button, error);
  imageTranslation.attach(figure, state.book.id, item.asset, { image, originalSrc: bookUrl('/images/' + item.asset) });
  return figure;
}
function addPage(index, prepend = false) {
  const fragment = document.createDocumentFragment(), page = state.book.pages[index];
  const chapter = state.book.chapters[page.chapter], opening = state.book.paragraphs[page.paragraphs[0]];
  const hasNativeHeading = opening && (opening.kind === 'heading' || headingKey(opening.text) === headingKey(chapter.title));
  if (index === chapter.startPage && !hasNativeHeading) {
    const marker = element('h2', 'chapter-break', chapter.title);
    marker.dataset.page = index; fragment.append(marker);
  }
  for (const content of page.content || page.paragraphs.map(id => ({ type: 'paragraph', id }))) {
    if (content.type === 'image') { fragment.append(illustrationRow(content.id, index)); continue; }
    const pid = content.id;
    const paragraph = state.book.paragraphs[pid];
    const row = element('section', 'paragraph-row' + (paragraph.kind === 'heading' ? ' heading' : ''));
    row.dataset.pid = pid; row.dataset.page = index; row.setAttribute('aria-label', '段落 ' + (pid + 1));
    const original = element('div', 'original'); original.lang = state.book.language || 'en'; original.title = '单击句子对照；双击单词查看语境释义';
    const translation = element('div', 'translation placeholder'); translation.lang = state.settings.targetLanguage === '日本語' ? 'ja' : 'zh';
    row.append(original, translation); fragment.append(row);
    const cached = state.cache.get(`${state.book.id}:${state.readingProfile || state.profile}:${pid}`); if (cached) state.items[pid] = cached;
  }
  if (prepend) $('#paragraphs').prepend(fragment); else $('#paragraphs').append(fragment);
  for (const pid of page.paragraphs) updateParagraph(pid);
}
function fillPage(backward = false) {
  if (reading.columns === 'both') return false;
  // Measure the actual typography and viewport; keep source-page IDs stable for caches/progress.
  const target = Math.max(720, innerHeight - $('.column-labels').getBoundingClientRect().height - (reading.immersive ? 90 : 180));
  let changed = false;
  while ($('#paragraphs').getBoundingClientRect().height < target && renderEnd - renderStart < 7) {
    if (backward) {
      if (renderStart === 0) break;
      addPage(--renderStart, true); state.page = renderStart;
    } else {
      if (renderEnd === state.book.pages.length - 1) break;
      addPage(++renderEnd);
    }
    changed = true;
  }
  return changed;
}
function scrollToReadingPage() {
  if (flow !== 'scroll') { window.scrollTo({ top: 0, behavior: 'instant' }); return; }
  const page = state.page, book = state.book.id;
  scrollRestoring = true;
  requestAnimationFrame(() => {
    if (flow !== 'scroll' || state.book.id !== book) { scrollRestoring = false; return; }
    const row = $(`.illustration-row[data-page="${page}"],.paragraph-row[data-page="${page}"]`);
    // Normal paged and scroll reading both open with the complete book header.
    // Only immersive reading skips the header to restore the text position.
    if (reading.immersive && row && page > 0) window.scrollTo({ top: scrollY + row.getBoundingClientRect().top - readingTop() - 4, behavior: 'instant' });
    else window.scrollTo({ top: 0, behavior: 'instant' });
    scrollRestoring = false; lastScrollY = scrollY; checkScroll();
  });
}
function saveProgress() {
  const page = state.page, id = state.book.id, url = bookUrl('/progress');
  const layoutVersion = state.book.imageVersion || 0;
  state.progressChain = state.progressChain.catch(() => {}).then(() => api(url, { page, layoutVersion })).then(result => {
    if (state.book?.id === id && result.bookmark && !bookmarkBusy && result.bookmark.updated >= (state.book.bookmark?.updated || 0)) {
      state.book.bookmark = result.bookmark; updateBookmarkButton(); renderToc();
    }
  }).catch(report);
  const summary = state.library.find(b => b.id === state.book.id);
  if (summary) { summary.currentPage = page; updateLibraryProgress(summary); }
}
function refreshLayout() {
  if (!state.book) return;
  if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; saveProgress(); }
  if (flow === 'pages') { renderPage(); scheduleWindow(); }
  else requestAnimationFrame(checkScroll);
}
document.addEventListener('reading-layout-change', refreshLayout);
window.addEventListener('resize', refreshLayout);
function checkScroll() {
  scrollFrame = 0;
  if (flow !== 'scroll' || !state.book || scrollRestoring || state.readingChoicePending) return;
  const container = $('#paragraphs'), top = $('.column-labels').getBoundingClientRect().bottom + 12;
  let changed = false;
  // In normal reading, returning to the book header takes priority over loading
  // earlier text. Compensating for a prepend before reaching it traps the user
  // below the title/progress, especially when resuming halfway through a book.
  const previousBoundary = reading.immersive ? container.getBoundingClientRect().top > top - 250 : window.scrollY <= 1;
  if (window.scrollY < lastScrollY && previousBoundary && renderStart > 0) {
    const height = container.getBoundingClientRect().height;
    addPage(--renderStart, true);
    if (reading.immersive) window.scrollBy({ top: container.getBoundingClientRect().height - height, behavior: 'instant' });
    else window.scrollTo({ top: 0, behavior: 'instant' });
    changed = true;
  }
  // Append ahead of the viewport, so swiping never requires a page-turn click.
  for (let i = 0; i < 4 && container.getBoundingClientRect().bottom < innerHeight * 2 && renderEnd < state.book.pages.length - 1; i++) {
    addPage(++renderEnd); changed = true;
  }
  const row = [...container.querySelectorAll('.paragraph-row,.illustration-row')].find(node => node.getBoundingClientRect().bottom > top);
  const headerVisible = !reading.immersive && container.getBoundingClientRect().top >= top - 16;
  const page = row && !headerVisible ? Number(row.dataset.page) : state.page;
  if (page !== state.page) {
    state.page = page; state.selected = null; ++state.epoch; clearTimeout(state.timer);
    renderPageChrome(); clearTimeout(progressTimer); progressTimer = setTimeout(() => { progressTimer = null; saveProgress(); }, 350); changed = true;
  }
  $('#scroll-status').textContent = renderEnd === state.book.pages.length - 1 ? '已到全书末尾' : '向上滑动，继续阅读';
  lastScrollY = window.scrollY;
  if (changed) { scheduleWindow(); reading.preparePage(); }
  if (renderEnd < state.book.pages.length - 1 && container.getBoundingClientRect().bottom < innerHeight * 2) scrollFrame = requestAnimationFrame(checkScroll);
}
window.addEventListener('scroll', () => {
  if (flow === 'scroll' && !scrollFrame) scrollFrame = requestAnimationFrame(checkScroll);
}, { passive: true });
window.addEventListener('pagehide', () => {
  if (state.book) navigator.sendBeacon(bookUrl('/progress'), new Blob([JSON.stringify({ page: state.page, layoutVersion: state.book.imageVersion || 0 })], { type: 'application/json' }));
});

function setTab(tab) {
  hideLibraryMenu();
  const toc = tab === 'toc' && state.book;
  $('#library-panel').hidden = Boolean(toc); $('#toc-panel').hidden = !toc;
  for (const name of ['library', 'toc']) { const active = name === (toc ? 'toc' : 'library'); $('#tab-' + name).classList.toggle('active', active); $('#tab-' + name).setAttribute('aria-selected', String(active)); }
  if (!toc) { renderLibrary(); refreshLibraryProgress(); }
}
$('#tab-library').onclick = () => setTab('library');
$('#tab-toc').onclick = () => setTab('toc');

function syncLibraryView() {
  hideLibraryMenu();
  $('#library-list').dataset.view = libraryView;
  for (const view of libraryViews) $('#library-view-' + view).setAttribute('aria-pressed', String(libraryView === view));
}
for (const view of libraryViews) $('#library-view-' + view).onclick = () => {
  libraryView = view; localStorage.setItem('parallel.libraryView', view); syncLibraryView();
};
const libraryMenu = element('div', 'reproof-menu library-context-menu');
libraryMenu.id = 'library-menu'; libraryMenu.hidden = true; libraryMenu.setAttribute('role', 'menu'); libraryMenu.setAttribute('aria-label', '书籍操作');
const libraryMenuLabel = element('div', 'reproof-menu-label'), removeBookButton = element('button', '', '从书架删除图书');
removeBookButton.id = 'library-remove'; removeBookButton.type = 'button'; removeBookButton.setAttribute('role', 'menuitem');
libraryMenu.append(libraryMenuLabel, removeBookButton); document.body.append(libraryMenu);
let libraryMenuTarget, libraryMenuFocus;
function hideLibraryMenu(restore = false) {
  libraryMenu.hidden = true;
  if (restore && libraryMenuFocus?.isConnected) libraryMenuFocus.focus({ preventScroll: true });
}
function openLibraryMenu(event, button, book) {
  event.preventDefault(); reproof.hide(); reading.closeWord();
  libraryMenuTarget = book; libraryMenuFocus = button; libraryMenuLabel.textContent = book.title;
  removeBookButton.disabled = removingBooks.has(book.id); libraryMenu.hidden = false;
  const rect = button.getBoundingClientRect();
  const keyboard = event.type === 'keydown' || (!event.clientX && !event.clientY);
  libraryMenu.style.left = Math.max(8, Math.min(keyboard ? rect.left : event.clientX, innerWidth - libraryMenu.offsetWidth - 8)) + 'px';
  libraryMenu.style.top = Math.max(8, Math.min(keyboard ? rect.bottom : event.clientY, innerHeight - libraryMenu.offsetHeight - 8)) + 'px';
  removeBookButton.focus({ preventScroll: true });
}
document.addEventListener('pointerdown', event => { if (!libraryMenu.contains(event.target)) hideLibraryMenu(); });
document.addEventListener('scroll', () => hideLibraryMenu(), true);
window.addEventListener('resize', () => hideLibraryMenu());
document.addEventListener('keydown', event => {
  if (libraryMenu.hidden) return;
  if (event.key === 'Tab') { hideLibraryMenu(); return; }
  if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); hideLibraryMenu(true); }
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); event.stopImmediatePropagation(); removeBookButton.focus(); }
}, true);
removeBookButton.onclick = async () => {
  const book = libraryMenuTarget; hideLibraryMenu();
  if (!book || removingBooks.has(book.id)) return;
  removingBooks.add(book.id);
  try {
    await api('/api/books/' + book.id + '/remove', {});
    state.library = state.library.filter(item => item.id !== book.id);
    for (const key of state.cache.keys()) if (key.startsWith(book.id + ':')) state.cache.delete(key);
    if (localStorage.getItem('parallel.lastBook') === book.id) localStorage.removeItem('parallel.lastBook');
    if (state.book?.id === book.id) {
      ++state.epoch; clearTimeout(state.timer); clearTimeout(progressTimer); progressTimer = null;
      state.book = null; state.page = 0; state.items = {}; state.selected = null;
      reading.resetPage(); reproof.reset(); bookTranslation = null; ++bulkRevision;
      if (reading.immersive) reading.setImmersive(false);
      $('#paragraphs').replaceChildren(); $('#toc-list').replaceChildren();
      $('#reader').hidden = true; $('#reading-footer').hidden = true; $('#welcome').hidden = false;
      $('#tab-toc').disabled = true; $('#immersive-button').disabled = true;
      $('#header-title').textContent = '我的书架'; document.title = 'Parallel · 双语阅读';
    }
    setTab('library');
    $('#library-list .library-book')?.focus({ preventScroll: true });
    toast('已从书架删除；重新导入同一本 EPUB 可恢复译文和书签');
  } catch (error) { report(error); }
  finally { removingBooks.delete(book.id); }
};
function shelfTranslationProgress(book, base) {
  if (book.id !== state.book?.id || base?.profile !== state.profile) return base;
  const job = reproof.jobs.filter(job => job.scope === 'book').sort((a, b) => (b.round || 1) - (a.round || 1) || b.created - a.created)[0];
  if (!job) return base;
  if (base.mode === 'reproof' && ((base.round || 1) > (job.round || 1)
      || (base.round || 1) === (job.round || 1) && (base.completed > job.completed
      || base.completed === job.completed && (base.updated || 0) >= (job.updated || 0)))) return base;
  return { ...base, imageTotal: job.imageTotal, imageCompleted: job.imageCompleted, textTotal: job.textTotal, textCompleted: job.textCompleted, mode: 'reproof', reproofId: job.id, round: job.round || 1, state: job.state,
    created: job.created, updated: job.updated, completed: job.completed, total: job.total,
    percent: job.completed === job.total ? 100 : Math.min(99.9, Math.round(job.completed * 1000 / Math.max(1, job.total)) / 10) };
}
function updateLibraryProgress(book) {
  const button = [...$('#library-list').children].find(node => node.dataset.book === book.id);
  if (!button) return;
  const percent = book.pageCount > 0 ? Math.min(100, Math.max(0, ((book.currentPage || 0) + 1) / book.pageCount * 100)) : 0;
  const label = percent.toFixed(1) + '%';
  const readingBar = button.querySelector('.library-reading-progress');
  readingBar.querySelector('progress').value = percent;
  readingBar.querySelector('progress').setAttribute('aria-valuetext', label);
  readingBar.querySelector('.library-percent').textContent = label;
  readingBar.title = '阅读进度 ' + label;
  const translated = book.translationProgress, translationBar = button.querySelector('.library-translation-progress');
  const known = translated && translated.profile === state.profile;
  const translationLabel = known ? translated.percent.toFixed(1) + '%' : '—';
  translationBar.querySelector('progress').value = known ? translated.percent : 0;
  translationBar.querySelector('progress').setAttribute('aria-valuetext', known ? translationLabel : '正在读取');
  translationBar.querySelector('.library-percent').textContent = translationLabel;
  translationBar.dataset.mode = known && translated.mode === 'reproof' ? 'reproof' : 'translation';
  translationBar.querySelector('progress').setAttribute('aria-label', book.title + (translationBar.dataset.mode === 'reproof' ? ' 本轮全书校对进度' : ' 翻译 / 校对进度'));
  const reviewStates = { queued: '等待校对', running: '校对中', paused: '已暂停', failed: '部分未完成', completed: '本轮已完成' };
  translationBar.title = !known ? '正在读取翻译 / 校对进度' : translated.mode === 'reproof'
    ? `全书重新校对 · 第 ${translated.round} 轮 · ${translationLabel} · ${translated.completed} / ${translated.total} 段 · ${reviewStates[translated.state] || ''}`
    : `${translated.reviewEnabled ? '翻译 / 校对' : '翻译'}进度 ${translationLabel} · ${translated.completed} / ${translated.total} 段${translated.reviewEnabled ? `已校对 · 已有初译 ${translated.drafted} 段` : '已完成'}`;
}
async function refreshLibraryProgress() {
  if (libraryProgressBusy || document.hidden || $('#library-panel').hidden || !$('#sidebar').getClientRects().length) return;
  libraryProgressBusy = true;
  try {
    const result = await api('/api/library-progress');
    if (result.profile !== state.profile) return;
    for (const book of state.library) {
      if (result.books[book.id]) { book.translationProgress = shelfTranslationProgress(book, result.books[book.id]); updateLibraryProgress(book); }
    }
  } catch { /* Keep the last saved statistics during a temporary disconnect. */ }
  finally { libraryProgressBusy = false; }
}
setInterval(refreshLibraryProgress, 5000);
document.addEventListener('visibilitychange', refreshLibraryProgress);
function renderLibrary() {
  syncLibraryView();
  $('#library-list').replaceChildren(); $('#side-empty').hidden = state.library.length > 0;
  for (const book of state.library) {
    const button = element('button', 'library-book' + (state.book?.id === book.id ? ' active' : ''));
    button.dataset.book = book.id;
    button.title = book.title; button.setAttribute('aria-label', '阅读 ' + book.title);
    button.setAttribute('aria-haspopup', 'menu');
    button.oncontextmenu = event => openLibraryMenu(event, button, book);
    button.onkeydown = event => { if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') openLibraryMenu(event, button, book); };
    if (book.cover) { const image = element('img', 'book-cover'); image.src = book.cover; image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; button.append(image); }
    else { const cover = element('span', 'book-cover cover-placeholder'); cover.append(icon('book')); button.append(cover); }
    const info = element('span', 'library-book-info'); info.append(element('strong', '', book.title), element('small', '', book.author));
    const progressGroup = element('span', 'library-progress-group');
    for (const [kind, label] of [['reading', '阅读进度'], ['translation', '翻译 / 校对进度']]) {
      const bar = element('span', 'library-progress library-' + kind + '-progress'), progress = element('progress');
      progress.max = 100; progress.setAttribute('aria-label', book.title + ' ' + label);
      bar.append(progress, element('span', 'library-percent')); progressGroup.append(bar);
    }
    info.append(progressGroup); button.append(info);
    button.onclick = () => openBook(book.id, undefined, { atBookmark: true }).catch(report); $('#library-list').append(button);
    updateLibraryProgress(book);
  }
}
function renderToc() {
  const query = $('#toc-search').value.toLowerCase(); $('#toc-list').replaceChildren();
  if (!state.book) return;
  const current = state.book.pages[state.page].chapter;
  const bookmark = currentBookmark();
  for (const chapter of state.book.chapters) {
    if (!chapter.title.toLowerCase().includes(query)) continue;
    const button = element('button', 'toc-item' + (chapter.id === current ? ' active' : ''));
    button.dataset.chapter = chapter.id;
    if (chapter.id === current) button.setAttribute('aria-current', 'location');
    button.append(element('small', '', String(chapter.id + 1).padStart(2, '0')), element('span', '', chapter.title));
    const marked = bookmark && state.book.pages[bookmark.page]?.chapter === chapter.id;
    if (marked) {
      const mark = element('span', 'toc-bookmark'); mark.dataset.manual = String(bookmark.manual);
      const label = `${bookmark.manual ? '书签' : '上次阅读位置'}：第 ${bookmark.page + 1} 页 · 本章第 ${bookmark.page - chapter.startPage + 1} 页`;
      mark.title = label; mark.setAttribute('aria-label', label); mark.append(icon('bookmark')); button.append(mark);
      button.title = chapter.title + ' · ' + label;
    }
    button.onclick = () => { if (marked) goBookmark(bookmark.page); else goChapterEntry(chapter); document.body.classList.remove('mobile-sidebar'); };
    $('#toc-list').append(button);
  }
  reproof.decorateToc();
}
$('#toc-search').oninput = renderToc;

async function openBook(id, provided, { atBookmark = false } = {}) {
  const epoch = ++state.epoch; clearTimeout(state.timer);
  state.readingChoicePending = true; state.readingProfile = state.profile;
  const book = provided || await api('/api/books/' + id);
  if (epoch !== state.epoch || removingBooks.has(id) || !state.library.some(item => item.id === id)) return;
  const bookmarkPage = book.bookmark?.page;
  const targetPage = atBookmark && Number.isInteger(bookmarkPage) && bookmarkPage >= 0 && bookmarkPage < book.pages.length ? bookmarkPage : book.currentPage || 0;
  state.book = book; state.page = targetPage; state.selected = null; state.items = {}; reading.resetPage();
  $('#translation-source-button').hidden = false; $('#translation-source-button').textContent = '正在读取本地译文…';
  reproof.reset();
  ++bulkRevision; bookTranslation = null; renderBookTranslation();
  localStorage.setItem('parallel.lastBook', book.id);
  $('#welcome').hidden = true; $('#reader').hidden = false; $('#reading-footer').hidden = false; $('#tab-toc').disabled = false;
  $('#immersive-button').disabled = false;
  $('#header-title').textContent = book.title; $('#book-title').textContent = book.title; $('#book-author').textContent = book.author;
  document.title = book.title + ' · Parallel';
  renderLibrary(); setTab('toc');
  $('#paragraphs').replaceChildren(); renderStart = renderEnd = state.page; renderPageChrome();
  document.body.classList.remove('mobile-sidebar');
  if (book.warnings?.length) toast(`已导入；${book.warnings.length} 个章节无法解析，详细信息见书籍数据。`);
  try {
    if (await translationSources.resolve(book, {epoch})) {
      state.items = {}; reading.resetPage(); renderPage();
      if (flow === 'pages') scrollToReadingPage();
      if (atBookmark) { goBookmark(targetPage); saveProgress(); }
      scheduleWindow();
    }
  } catch (error) { report(error); }
}

function renderPageChrome() {
  const page = state.book.pages[state.page];
  const previous = state.book.chapters[page.chapter - 1], next = state.book.chapters[page.chapter + 1];
  $('#prev-chapter').disabled = !previous; $('#next-chapter').disabled = !next;
  $('#prev-chapter').title = previous ? '上一章：' + previous.title : '已是第一章';
  $('#next-chapter').title = next ? '下一章：' + next.title : '已是最后一章';
  $('#page-input').value = state.page + 1; $('#page-input').max = state.book.pages.length;
  $('#page-total').textContent = (flow === 'pages' && renderEnd > state.page ? '–' + (renderEnd + 1) + ' ' : '') + '/ ' + state.book.pages.length;
  $('#prev-page').disabled = renderStart === 0; $('#next-page').disabled = renderEnd === state.book.pages.length - 1;
  $('#immersive-prev').disabled = renderStart === 0; $('#immersive-next').disabled = renderEnd === state.book.pages.length - 1;
  const percent = ((state.page + 1) / state.book.pages.length * 100); $('#progress-text').textContent = percent.toFixed(1) + '%'; $('#reading-progress').value = percent;
  updatePrefetchButton(); renderToc();
  updateBookmarkButton();
}

function currentBookmark() {
  const mark = state.book?.bookmark;
  if (!mark) return null;
  return mark.manual ? mark : { ...mark, page: state.page };
}
function updateBookmarkButton() {
  const button = $('#bookmark-button');
  if (!button) return;
  button.disabled = !state.book || bookmarkBusy;
  const mark = currentBookmark(), pinnedHere = !!mark?.manual && mark.page === state.page;
  button.setAttribute('aria-pressed', String(pinnedHere));
  button.setAttribute('aria-label', pinnedHere ? '更新当前页书签' : '在当前页添加书签');
  button.title = !state.book ? '打开书籍后添加书签' : mark?.manual ? `书签在第 ${mark.page + 1} 页；点击更新为当前页` : '自动记住阅读进度；点击将当前页固定为书签';
}
function goBookmark(page) {
  goPage(page);
  const epoch = state.epoch, id = state.book.id;
  requestAnimationFrame(() => {
    if (epoch !== state.epoch || state.book?.id !== id) return;
    const row = $(`.illustration-row[data-page="${page}"],.paragraph-row[data-page="${page}"]`);
    if (row) window.scrollTo({ top: scrollY + row.getBoundingClientRect().top - readingTop() - 8, behavior: 'instant' });
    lastScrollY = scrollY;
    if (flow === 'scroll') checkScroll();
  });
}
$('#bookmark-button').onclick = async () => {
  if (!state.book || bookmarkBusy) return;
  const id = state.book.id, page = state.page;
  bookmarkBusy = true; updateBookmarkButton();
  try {
    await state.progressChain.catch(() => {});
    const mark = await api(`/api/books/${id}/bookmark`, { page, layoutVersion: state.book.imageVersion || 0 });
    const summary = state.library.find(book => book.id === id); if (summary) summary.bookmark = mark;
    if (state.book?.id === id) { state.book.bookmark = mark; renderToc(); }
    toast(`书签已保存：第 ${page + 1} 页`);
  } catch (error) { report(error); }
  finally { bookmarkBusy = false; updateBookmarkButton(); }
};
function renderPage(backward = false) {
  clearTimeout(progressTimer);
  pageBackward = backward;
  renderStart = state.page; renderEnd = state.page;
  $('#paragraphs').replaceChildren(); addPage(state.page);
  if (flow === 'pages') fillPage(backward);
  else {
    for (let i = 0; i < 2 && renderEnd < state.book.pages.length - 1; i++) addPage(++renderEnd);
    $('#scroll-status').textContent = renderEnd === state.book.pages.length - 1 ? '已到全书末尾' : '向上滑动，继续阅读';
  }
  renderPageChrome();
  if (flow === 'scroll') scrollToReadingPage();
}

function updateParagraph(pid) {
  const row = $(`.paragraph-row[data-pid="${pid}"]`); if (!row) return;
  const item = reading.decorate(pid, state.items[pid] || { state: 'idle' }), cell = row.querySelector('.translation');
  const signature = JSON.stringify(item);
  row.dataset.reproofreading = item.reproofreading || '';
  row.setAttribute('aria-busy', String(!!item.reproofreading));
  row.classList.toggle('selected', state.selected === pid);
  if (cell.dataset.signature === signature) { reading.highlight(); return; }
  cell.dataset.signature = signature; cell.className = 'translation';
  if (item.state === 'ready') { cell.setAttribute('aria-label', '译文'); }
  else if (item.state === 'error') {
    cell.replaceChildren(); delete cell.dataset.sentences;
    cell.classList.add('error'); cell.append(element('span', '', item.error));
    const retry = element('button', 'retry-button', '重试翻译'); retry.onclick = () => selectParagraph(pid, true); cell.append(retry);
  } else {
    cell.replaceChildren(); delete cell.dataset.sentences;
    cell.classList.add('placeholder');
    if (item.state === 'translating') { cell.classList.add('translating'); cell.append(element('span', '', item.stage === 'proofreading' ? '3.8 正在校对译文…' : '正在结合上下文翻译…')); }
    else { cell.append(icon('spark'), element('span', '', item.state === 'queued' ? '已加入翻译队列' : '点击原文，即可翻译这一段')); }
  }
  reading.render(pid, row, item);
}
function selectParagraph(pid, retry = false) {
  let moved = false;
  if (flow === 'scroll') {
    const page = Number($(`.paragraph-row[data-pid="${pid}"]`)?.dataset.page);
    if (Number.isInteger(page) && page !== state.page) { state.page = page; ++state.epoch; renderPageChrome(); saveProgress(); moved = true; }
  }
  state.selected = pid;
  for (const id of renderedIds()) updateParagraph(id);
  if (state.items[pid]?.state !== 'ready') scheduleWindow(pid, retry || state.items[pid]?.state === 'error');
  else if (moved) scheduleWindow();
}

function scheduleWindow(selected = null, retry = false) {
  if (!state.book || state.readingChoicePending) return;
  const epoch = state.epoch, book = state.book, page = state.page, endPage = windowEnd(), enabled = state.prefetch;
  clearTimeout(state.timer);
  // Serialize mutations so rapid navigation cannot restore an older window.
  state.windowChain = state.windowChain.catch(() => {}).then(async () => {
    if (epoch !== state.epoch) return;
    const result = await api('/api/books/' + book.id + '/window', { page, endPage, selected, retry, enabled });
    if (epoch !== state.epoch) return;
    state.profile = result.profile;
    if (result.needsTranslationChoice) { await translationSources.resolve(book, {epoch}); if (epoch === state.epoch) scheduleWindow(selected, retry); return; }
    await poll(epoch);
  }).catch(error => { if (epoch === state.epoch) { report(error); $('#prefetch-text').textContent = '连接中断，5 秒后重试'; state.timer = setTimeout(() => scheduleWindow(selected, retry), 5000); } });
}
async function poll(epoch) {
  if (epoch !== state.epoch || !state.book || state.readingChoicePending) return;
  const revision = bulkRevision;
  try {
    const result = await api(bookUrl('/status?page=' + state.page + '&endPage=' + windowEnd()));
    if (epoch !== state.epoch) return;
    if (result.needsTranslationChoice) { await translationSources.resolve(state.book, {epoch}); if (epoch === state.epoch) scheduleWindow(); return; }
    const restoreAnchor = reproof.anchorBeforeUpdate(result.items);
    state.profile = result.profile; state.readingProfile = result.readingProfile || state.readingProfile || result.profile;
    if (result.readingTranslation) translationSources.update(result.readingTranslation);
    Object.assign(state.items, result.items);
    for (const [pid, item] of Object.entries(result.items)) {
      if (item.state === 'ready') state.cache.set(`${state.book.id}:${state.readingProfile}:${pid}`, item);
      updateParagraph(pid);
    }
    restoreAnchor();
    if (revision === bulkRevision && !bookReproofBusy.has(state.book.id)) reproof.update(result.reproofreading || []);
    reading.preparePage();
    if (flow === 'scroll' && !scrollFrame) scrollFrame = requestAnimationFrame(checkScroll);
    if (flow === 'pages' && fillPage(pageBackward)) { renderPageChrome(); saveProgress(); scheduleWindow(); return; }
    updateStatus(result);
    if (revision === bulkRevision && !bulkBusy) { bookTranslation = result.bookTranslation; renderBookTranslation(); }
    clearTimeout(state.timer); state.timer = setTimeout(() => poll(epoch), Object.values(result.items).some(i => ['queued', 'translating'].includes(i.state)) || bookTranslation?.state === 'running' || reproof.jobs.some(job => ['queued', 'running'].includes(job.state)) ? 850 : 3000);
  } catch (error) {
    if (epoch !== state.epoch) return;
    $('#prefetch-text').textContent = '连接中断，正在重试';
    clearTimeout(state.timer); state.timer = setTimeout(() => poll(epoch), 5000);
  }
}
function updateStatus(result) {
  const total = result.pages.reduce((s, p) => s + p.total, 0), ready = result.pages.reduce((s, p) => s + p.ready, 0);
  const errors = Object.values(result.items).some(i => i.state === 'error');
  $('#prefetch-text').textContent = !state.prefetch ? '邻页预加载已暂停' : errors ? '部分翻译失败 · 点击段落重试' : ready === total ? '前后两页翻译已就绪' : `预加载中 · ${ready} / ${total} 段`;
  $('#prefetch-dot').classList.toggle('working', state.prefetch && ready < total);
  $('#prefetch-pages').replaceChildren();
  for (const page of result.pages) {
    const cell = element('span', 'prefetch-cell' + (page.ready === page.total ? ' ready' : '') + (page.page === state.page ? ' current' : ''));
    cell.title = `第 ${page.page + 1} 页：${page.ready}/${page.total} 段已缓存`; $('#prefetch-pages').append(cell);
  }
}
function goPage(page, backward = false) {
  if (!state.book || !Number.isFinite(page) || state.readingChoicePending) return;
  page = Math.max(0, Math.min(Math.trunc(page), state.book.pages.length - 1));
  if (page === state.page) { $('#page-input').value = page + 1; return; }
  state.page = page; state.selected = null; state.items = {}; ++state.epoch; clearTimeout(state.timer); reading.resetPage();
  renderPage(backward); if (flow === 'pages') scrollToReadingPage(); saveProgress();
  scheduleWindow();
}
function previousPage() { if (renderStart > 0) goPage(renderStart - 1, true); }
function nextPage() { if (renderEnd < state.book.pages.length - 1) goPage(renderEnd + 1); }
function goChapter(offset) {
  if (!state.book) return;
  const chapter = state.book.chapters[state.book.pages[state.page].chapter + offset];
  if (!chapter) return;
  goChapterEntry(chapter);
}
function goChapterEntry(chapter) {
  if (state.readingChoicePending) return;
  goPage(chapter.startPage);
  const epoch = state.epoch, book = state.book.id;
  requestAnimationFrame(() => {
    if (epoch !== state.epoch || state.book.id !== book) return;
    const start = $(`.chapter-break[data-page="${chapter.startPage}"]`) || $(`.paragraph-row[data-pid="${chapter.startParagraph}"]`) || $(`.paragraph-row[data-page="${chapter.startPage}"]`);
    if (start) window.scrollTo({ top: scrollY + start.getBoundingClientRect().top - readingTop() - 8, behavior: 'instant' });
    lastScrollY = scrollY;
    if (flow === 'scroll') checkScroll();
  });
}
$('#prev-chapter').onclick = () => goChapter(-1); $('#next-chapter').onclick = () => goChapter(1);
$('#prev-page').onclick = previousPage; $('#next-page').onclick = nextPage;
$('#immersive-prev').onclick = previousPage; $('#immersive-next').onclick = nextPage;
$('#page-input').onchange = (event) => goPage(Number(event.target.value) - 1);
$('#page-input').onkeydown = (event) => { if (event.key === 'Enter') { event.preventDefault(); goPage(Number(event.target.value) - 1); event.target.blur(); } };
document.addEventListener('keydown', (event) => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName) || $('dialog[open]') || !state.book) return;
  if (flow === 'pages' && event.key === 'ArrowLeft') { event.preventDefault(); previousPage(); }
  if (flow === 'pages' && event.key === 'ArrowRight') { event.preventDefault(); nextPage(); }
});
function updatePrefetchButton() {
  $('#prefetch-toggle').replaceChildren(icon(state.prefetch ? 'pause' : 'play'), element('span', '', state.prefetch ? '自动预翻译：开' : '自动预翻译：关'));
  $('#prefetch-toggle').title = '当前页及前后两页的预加载，独立于整本预翻译';
  $('#prefetch-toggle').setAttribute('aria-pressed', String(state.prefetch));
}
$('#prefetch-toggle').onclick = () => { state.prefetch = !state.prefetch; localStorage.setItem('parallel.prefetch', state.prefetch); updatePrefetchButton(); scheduleWindow(); };
updatePrefetchButton();

function renderBookTranslation() {
  const task = bookTranslation, button = $('#book-translation-action');
  const summary = state.library.find(book => book.id === state.book?.id);
  if (summary && task) {
    const previous = summary.translationProgress;
    const base = previous?.mode === 'reproof' && previous.profile === state.profile ? previous
      : { ...task, completed: task.completed, total: task.total, percent: task.percent, drafted: task.drafted,
          reviewEnabled: task.reviewEnabled, profile: task.profile || state.profile };
    summary.translationProgress = shelfTranslationProgress(summary, base);
    updateLibraryProgress(summary);
  }
  const labels = { idle: '未开始', running: task?.reviewEnabled ? '后台翻译 / 校对中' : '后台翻译中', paused: '已暂停', completed: '全部就绪', failed: '部分未完成' };
  const actions = { idle: ['start', 'play', task?.reviewEnabled && task.drafted === task.total ? '开始校对' : '开始预翻译'], running: ['pause', 'pause', '暂停'], paused: ['resume', 'play', '继续'], failed: ['retry', 'play', '重试失败段落'] };
  const action = actions[task?.state];
  const label = task ? labels[task.state] : '正在读取';
  if ($('#book-translation-state').textContent !== label) $('#book-translation-state').textContent = label;
  $('#book-translation').dataset.state = task?.state || 'loading';
  button.disabled = bulkBusy || !action;
  button.dataset.action = action?.[0] || '';
  button.replaceChildren(icon(bulkBusy ? 'spark' : action?.[1] || 'check'), element('span', '', bulkBusy ? '处理中…' : action?.[2] || (task ? '已完成' : '正在读取')));
  $('#book-translation-progress').value = task?.completed || 0;
  $('#book-translation-progress').max = task?.total || 1;
  $('.book-translation-summary').title = task ? `整本翻译 ${task.percent.toFixed(1)}% · ${labels[task.state]} · 点击展开详情` : '整本翻译进度 · 点击展开详情';
  $('#book-translation-count').textContent = task ? (task.imageTotal ? `正文 ${task.textCompleted} / ${task.textTotal} 段 · 图片 ${task.imageCompleted} / ${task.imageTotal} 张 · ${task.percent.toFixed(1)}%` : `${task.completed.toLocaleString()} / ${task.total.toLocaleString()} 段${task.reviewEnabled ? '已校对' : ''} · ${task.percent.toFixed(1)}%${task.reviewEnabled ? ` · 已有初译 ${(task.drafted || 0).toLocaleString()} 段` : ''}`) : '—';
  $('#book-translation-model').textContent = task ? `${task.model}${task.reviewEnabled ? ' → ' + task.reviewModel + ' 校对' : ''} · ${task.targetLanguage} · 已保存到本机${['azure', 'microsoft'].includes(task.provider) ? ' · 图片保留原图' : ''}` : '';
  $('#book-translation-failed').hidden = !task?.failed;
  $('#book-translation-failed').textContent = task?.failed ? `${task.failed} ${task.imageTotal ? '项' : '段'}待重试` : '';
  $('#book-translation-chapter-count').textContent = task ? `章节 ${task.completedChapters} / ${task.totalChapters} 已完成 · 本书最多 ${task.parallelChapters} 章并行` : '';
  const active = $('#book-translation-active'); active.replaceChildren();
  for (const chapter of task?.activeChapters || []) {
    const chip = element('span', 'translation-chapter');
    chip.append(element('span', 'translation-chapter-title', chapter.title), element('small', '', `${chapter.completed} / ${chapter.total}`));
    chip.title = `${chapter.title}：${chapter.completed} / ${chapter.total} 段已保存`;
    active.append(chip);
  }
  const notes = {
    idle: '不同章节并行推进，章内按段落顺序翻译并参考相邻上下文。电脑和本地服务需保持运行。',
    running: '关闭阅读页面后仍会继续；服务重启后自动接续。电脑需保持运行，当前页与邻页优先翻译。',
    paused: '整本任务已暂停；已发出的请求仍会完成并保存。邻页预加载可独立控制。',
    completed: '整本译文已保存在本机，下次打开即可直接阅读。',
    failed: '成功译文已保存。重试仅翻译缺失的段落，不会重复翻译已完成内容。'
  };
  $('#book-translation-note').textContent = task ? (task.reason || (task.reviewEnabled && task.state !== 'completed' ? '先初译再校对，通过检查后保存最终译文。已有初译会复用；校对失败保留初译。' : '') + notes[task.state]) : '正在读取本机翻译进度…';
  $('#book-exports').hidden = task?.state !== 'completed' && !(translationSources.value?.book === state.book?.id && translationSources.value?.completed === state.book?.paragraphs.length);
  if (state.book) for (const format of ['bilingual', 'translation']) $('#download-' + format).href = bookUrl('/export?format=' + format);
  renderBookReproof();
}
function renderBookReproof() {
  const textOnly = ['azure', 'microsoft'].includes(state.settings.translationProvider) || state.readingProfile && state.readingProfile !== state.profile;
  $('#book-reproof-action').hidden = textOnly; $('#book-reproof-state').hidden = textOnly;
  if (textOnly) { $('#book-reproof-details').hidden = true; return; }
  const job = reproof.jobs.find(job => job.scope === 'book');
  const button = $('#book-reproof-action'), busy = !!state.book && bookReproofBusy.has(state.book.id);
  const complete = !!bookTranslation?.total && bookTranslation.completed === bookTranslation.total;
  const active = job && ['queued', 'running'].includes(job.state);
  const resume = job && ['paused', 'failed'].includes(job.state);
  button.dataset.action = active ? 'pause' : resume ? 'resume' : 'start';
  button.disabled = busy || !state.book || (!complete && !active && !resume);
  button.title = !complete && !job ? '整本翻译完成后可重新校对' : '使用当前校对模型重新检查已有译文，并逐段保存';
  button.replaceChildren(icon(busy ? 'spark' : active ? 'pause' : resume ? 'play' : 'spark'),
    element('span', '', busy ? '处理中…' : active ? '暂停全书校对' : resume ? (job.failed ? '继续并重试' : '继续全书校对') : '全书重新校对'));
  const labels = { queued: '等待校对', running: '校对中', paused: '已暂停', failed: '部分未完成', completed: '本轮已完成' };
  $('#book-reproof-state').textContent = job ? labels[job.state] : complete ? '可再次校对' : '';
  $('#book-reproof-details').hidden = !job;
  $('#book-reproof-progress').max = job?.total || 1;
  $('#book-reproof-progress').value = job?.completed || 0;
  $('#book-reproof-count').textContent = job ? `${job.imageTotal ? `正文 ${job.textCompleted} / ${job.textTotal} 段 · 图片 ${job.imageCompleted} / ${job.imageTotal} 张` : `${job.completed.toLocaleString()} / ${job.total.toLocaleString()} 段`} · ${(job.completed / Math.max(1, job.total) * 100).toFixed(1)}% · 章节 ${job.completedChapters} / ${job.totalChapters}${job.failed ? ` · ${job.failed} 项待重试` : ''}` : '';
  $('#book-reproof-note').textContent = job?.reason || (active ? '后台按章节并行校对，逐段保存；已有译文可继续阅读。' : job?.state === 'completed' ? '本轮校对已保存，可再次点击开始新一轮。' : complete ? '使用当前校对模型、术语表和最新语境规则，重新检查全书；关闭页面后仍会继续。' : '整本翻译完成后，可用当前校对模型重新检查全书。');
  const summary = $('#book-translation > summary'), progress = $('#book-translation-progress');
  if (job) { progress.max = job.total || 1; progress.value = job.completed; }
  progress.setAttribute('aria-label', job ? '全书重新校对进度' : '整本翻译进度');
  summary.title = job ? `全书重新校对：${labels[job.state]} · 点击展开详情` : '整本翻译进度 · 点击展开详情';
  summary.setAttribute('aria-label', job ? '展开或收起全书校对详情' : '展开或收起整本预翻译详情');
}
$('#book-reproof-action').onclick = async () => {
  if (!state.book || bookReproofBusy.has(state.book.id)) return;
  const id = state.book.id, profile = state.profile, action = $('#book-reproof-action').dataset.action;
  bookReproofBusy.add(id); ++bulkRevision; renderBookReproof();
  try {
    const job = await api(`/api/books/${id}/reproofread`, { scope: 'book', action, profile });
    if (state.book?.id === id && state.profile === profile) {
      reproof.update([job, ...reproof.jobs.filter(old => old.scope !== 'book')]);
      $('#book-translation').open = true;
      renderBookTranslation();
    }
    toast(action === 'pause' ? '全书校对已暂停，已有进度保留' : action === 'resume' ? '已继续全书校对' : '已开始新一轮全书校对，结果会逐段保存');
  } catch (error) { report(error); }
  finally {
    bookReproofBusy.delete(id); ++bulkRevision; renderBookTranslation();
    if (state.book?.id === id) reproof.refresh();
  }
};
$('#book-translation-action').onclick = async () => {
  const action = $('#book-translation-action').dataset.action;
  if (!action || bulkBusy || !state.book) return;
  const id = state.book.id;
  bulkBusy = true; ++bulkRevision; renderBookTranslation();
  try {
    const result = await api('/api/books/' + id + '/pretranslate', { action });
    if (state.book?.id === id && result.profile === state.profile) bookTranslation = result;
  } catch (error) { report(error); }
  finally { bulkBusy = false; ++bulkRevision; renderBookTranslation(); }
};

let importing = false;
async function importBook(file, sample = false) {
  if (importing) return;
  if (!sample && (!file || !file.name.toLowerCase().endsWith('.epub'))) return toast('请选择 .epub 格式的电子书');
  if (file?.size > 100 * 1024 * 1024) return toast('EPUB 超过 100 MB 导入限制');
  importing = true;
  for (const id of ['import-button', 'library-add', 'sample-button', 'welcome-import']) $('#' + id).disabled = true;
  toast('正在整理书籍和章节…');
  try {
    const book = sample ? await api('/api/import-sample', {}) : await api('/api/import', undefined, { method: 'POST', headers: { 'Content-Type': 'application/epub+zip' }, body: file });
    state.library = await api('/api/books'); await openBook(book.id, book); toast(`已导入《${book.title}》，原书已保存到本机书库`);
  } catch (error) { report(error); }
  finally {
    importing = false;
    for (const id of ['import-button', 'library-add', 'sample-button', 'welcome-import']) $('#' + id).disabled = false;
    $('#file-input').value = '';
  }
}
$('#library-add').onclick = $('#import-button').onclick = $('#welcome-import').onclick = () => $('#file-input').click();
$('#file-input').onchange = event => importBook(event.target.files[0]);
$('#sample-button').onclick = () => importBook(null, true);
let dragDepth = 0;
document.addEventListener('dragenter', event => { if ([...event.dataTransfer.types].includes('Files')) { event.preventDefault(); dragDepth++; $('#drop-overlay').hidden = false; } });
document.addEventListener('dragover', event => { if ([...event.dataTransfer.types].includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } });
document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#drop-overlay').hidden = true; } });
document.addEventListener('drop', event => { event.preventDefault(); dragDepth = 0; $('#drop-overlay').hidden = true; importBook(event.dataTransfer.files[0]); });

async function showSettings() {
  try {
    const settings = await api('/api/settings'); state.settings = settings;
    const form = $('#settings-form'); for (const key of ['baseUrl', 'model', 'targetLanguage', 'apiKey', 'reviewBaseUrl', 'reviewModel', 'reviewApiKey', 'wordBaseUrl', 'wordModel', 'wordApiKey']) form.elements[key].value = settings[key] || '';
    form.elements.translationProvider.value = settings.translationProvider || 'llm';
    form.elements.wordProvider.value = settings.wordProvider || 'microsoft';
    form.elements.reviewEnabled.checked = !!settings.reviewEnabled;
    form.elements.clearReviewApiKey.checked = false; form.elements.clearWordApiKey.checked = false;
    form.elements.wordApiKey.placeholder = settings.hasWordApiKey ? '已保存，留空以保留' : '按服务要求填写';
    syncModelRequirements();
    form.elements.reviewApiKey.placeholder = settings.hasReviewApiKey ? '已保存，留空以保留' : '按服务要求填写';
    form.elements.clearApiKey.checked = false;
    form.elements.apiKey.placeholder = settings.hasApiKey ? '已保存，留空以保留' : '按服务要求填写';
    $('#connection-result').textContent = ''; $('#settings-dialog').showModal();
  } catch (error) { report(error); }
}
function syncModelRequirements() {
  const form = $('#settings-form'), provider = form.elements.translationProvider.value, llm = provider === 'llm';
  $('#microsoft-provider-settings').hidden = provider !== 'microsoft';
  for (const [id, active] of [['llm-provider-settings', llm], ['word-provider-settings', form.elements.wordProvider.value === 'llm']]) {
    const group = $('#' + id); group.hidden = !active;
    for (const control of group.querySelectorAll('input, select')) control.disabled = !active;
  }
  for (const key of ['baseUrl', 'model']) form.elements[key].required = llm;
  for (const key of ['reviewBaseUrl', 'reviewModel']) form.elements[key].required = llm && form.elements.reviewEnabled.checked;
  for (const key of ['wordBaseUrl', 'wordModel']) form.elements[key].required = form.elements.wordProvider.value === 'llm';
  $('#test-model').title = !llm ? '发送一句简短测试文本以验证翻译服务' : '验证模型接口连接';
}
$('#settings-form').elements.reviewEnabled.addEventListener('change', syncModelRequirements);
$('#translation-provider').addEventListener('change', syncModelRequirements);
$('#word-provider').addEventListener('change', syncModelRequirements);
$('#model-badge').onclick = showSettings;
for (const button of document.querySelectorAll('.close-dialog')) button.onclick = () => button.closest('dialog').close();
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => { const r = dialog.getBoundingClientRect(); if (event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) dialog.close(); });
function formSettings() { const form = $('#settings-form'), data = Object.fromEntries(new FormData(form)); for (const key of ['clearApiKey', 'reviewEnabled', 'clearReviewApiKey', 'clearWordApiKey']) { if (!form.elements[key].disabled) data[key] = form.elements[key].checked; } return data; }
$('#test-model').onclick = async () => {
  const button = $('#test-model'), result = $('#connection-result'); button.disabled = true; result.classList.remove('error'); result.textContent = '正在连接翻译服务…';
  try { const settings = formSettings(); await api('/api/model/test', settings); result.textContent = settings.translationProvider === 'microsoft' ? '连接成功，Azure Translate 可用。' : settings.reviewEnabled ? '连接成功，初译和校对模型均可用。' : '连接成功，模型可用。'; }
  catch (error) { result.classList.add('error'); result.textContent = error.message; }
  finally { button.disabled = false; }
};
$('#settings-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  let saved = false;
  try {
    const preferred = state.readingProfile;
    state.readingChoicePending = true; ++state.epoch; clearTimeout(state.timer);
    await api('/api/settings', formSettings()); state.settings = await api('/api/settings'); state.profile = state.settings.profile;
    saved = true;
    ++bulkRevision; reproof.reset(); bookTranslation = null; renderBookTranslation();
    state.cache.clear(); state.items = {}; applySettings(); $('#settings-dialog').close(); toast('翻译配置已保存，已有译文保留');
    if (state.book) {
      if (await translationSources.resolve(state.book, {preferred})) { reading.resetPage(); renderPage(); renderBookTranslation(); scheduleWindow(); }
    } else state.readingChoicePending = false;
  } catch (error) { if (!saved) { state.readingChoicePending = false; scheduleWindow(); } report(error); }
  finally { button.disabled = false; }
};
function applySettings() {
  const provider = state.settings.translationProvider;
  const name = provider === 'microsoft' ? 'Azure Translate' : '';
  $('#model-badge span:last-child').textContent = name || (state.settings.model ? state.settings.model + (state.settings.reviewEnabled ? ' + 校对' : '') : '配置模型');
  $('#model-badge').title = name ? name + '：正文翻译，插图保留原图' : state.settings.reviewEnabled ? `${state.settings.model} 初译 → ${state.settings.reviewModel} 校对` : state.settings.model;
}

$('#sidebar-toggle').onclick = () => { if (window.innerWidth <= 720) document.body.classList.toggle('mobile-sidebar'); else document.body.classList.toggle('sidebar-collapsed'); refreshLibraryProgress(); };
document.addEventListener('click', event => { if (window.innerWidth <= 720 && !event.target.closest('#sidebar') && !event.target.closest('#sidebar-toggle') && !event.target.closest('#library-menu')) document.body.classList.remove('mobile-sidebar'); });
window.addEventListener('online', () => { if (state.book) scheduleWindow(); });
try {
  const [settings, books, sample] = await Promise.all([api('/api/settings'), api('/api/books'), api('/api/sample')]);
  state.settings = settings; state.profile = settings.profile; state.library = books; applySettings(); renderLibrary();
  $('#sample-button').hidden = !sample.available;
  const last = localStorage.getItem('parallel.lastBook');
  if (books.length) await openBook(books.some(b => b.id === last) ? last : books[0].id);
} catch (error) { report(error); }
