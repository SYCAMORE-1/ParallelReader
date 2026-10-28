const path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(path.join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
(async () => {
  const url = process.env.PARALLEL_BASE_URL || 'http://127.0.0.1:8765';
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const profile = 'shelf-reproof-test', errors = [], unexpected = [];
    const baseProgress = { completed: 100, total: 100, percent: 100, drafted: 100, reviewEnabled: true, profile };
    const books = [0, 1, 2].map(id => ({ id: 'book-' + id, title: 'Book ' + id, author: 'QA', cover: null, currentPage: 0, pageCount: 100, translationProgress: baseProgress }));
    const task = { ...baseProgress, state: 'completed', failed: 0, model: 'QA', targetLanguage: '简体中文', completedChapters: 1, totalChapters: 1, parallelChapters: 4, activeChapters: [] };
    let review = null, staleShelf = false, polls = 0;
    const shelf = () => review ? { ...baseProgress, mode: 'reproof', reproofId: review.id, round: review.round, state: review.state,
      completed: review.completed, percent: review.completed, created: review.created, updated: review.updated } : baseProgress;
    const revise = values => { review = { ...review, ...values, updated: (review?.updated || 0) + 1 }; };
    await context.addInitScript(() => localStorage.setItem('parallel.prefetch', 'false'));
    await context.route('**/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      let value;
      if (endpoint === '/api/settings') value = { model: 'QA', profile, reviewEnabled: true, targetLanguage: '简体中文' };
      else if (endpoint === '/api/books') value = books.map((book, id) => ({ ...book, translationProgress: id === 0 ? shelf() : baseProgress }));
      else if (endpoint === '/api/library-progress') value = { profile, books: Object.fromEntries(books.map((book, id) => [book.id, id === 0 && !staleShelf ? shelf() : baseProgress])) };
      else if (endpoint === '/api/sample') value = { available: false };
      else if (endpoint.endsWith('/reproofread') && request.method() === 'POST') {
        const { action } = request.postDataJSON();
        if (action === 'start') {
          review = { id: 'round-' + ((review?.round || 0) + 1), scope: 'book', round: (review?.round || 0) + 1, chapter: null,
            state: 'queued', completed: 0, total: 100, failed: 0, created: Date.now() / 1000, updated: Date.now() / 1000,
            chapters: [], completedChapters: 0, totalChapters: 1, reason: '', model: 'QA', errors: [] };
        } else revise({ state: action === 'pause' ? 'paused' : 'running' });
        value = review;
      } else if (endpoint.endsWith('/pretranslate')) value = task;
      else if (endpoint.endsWith('/window')) value = { profile };
      else if (endpoint.endsWith('/progress')) value = { ok: true };
      else if (endpoint.endsWith('/status')) {
        polls++;
        value = { profile, pages: [], items: {}, bookTranslation: task, reproofreading: review ? [review] : [] };
      } else if (endpoint.startsWith('/api/books/book-')) {
        const id = Number(endpoint.split('/')[3].slice(-1));
        value = { ...books[id], language: 'en', chapters: [{ id: 0, title: 'Chapter', startPage: 0, endPage: 99, startParagraph: 0 }],
          pages: Array.from({ length: 100 }, (_, id) => ({ id, chapter: 0, paragraphs: [id] })),
          paragraphs: Array.from({ length: 100 }, (_, id) => ({ id, chapter: 0, kind: 'paragraph', text: 'This is a paragraph.' })) };
      } else { unexpected.push(endpoint); value = {}; }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.locator('#tab-library').click();
    const percent = page.locator('.library-book[data-book="book-0"] .library-translation-progress .library-percent');
    const waitPercent = value => page.waitForFunction(value => document.querySelector('.library-book[data-book="book-0"] .library-translation-progress .library-percent').textContent === value, value);
    assert.equal(await percent.textContent(), '100.0%');
    await page.locator('#book-translation').evaluate(details => { details.open = true; });
    await page.locator('#book-reproof-action').click();
    await waitPercent('0.0%');
    assert.equal(await page.locator('.library-translation-progress').first().getAttribute('data-mode'), 'reproof');
    const before = polls;
    await page.waitForResponse(response => response.url().includes('/status?'));
    assert.ok(polls > before); assert.equal(await percent.textContent(), '0.0%', 'Completed translation polling cannot reset review to 100%');
    revise({ state: 'running', completed: 23 }); staleShelf = true;
    await waitPercent('23.0%');
    await page.waitForResponse(response => response.url().endsWith('/api/library-progress'));
    assert.equal(await percent.textContent(), '23.0%', 'A stale shelf request cannot overwrite the new review pass');
    staleShelf = false;
    await page.locator('#book-reproof-action').click();
    await page.waitForFunction(() => document.querySelector('#book-reproof-state').textContent === '已暂停');
    assert.equal(await percent.textContent(), '23.0%');
    await page.reload({ waitUntil: 'networkidle' }); await page.locator('#tab-library').click();
    assert.equal(await percent.textContent(), '23.0%');
    await page.locator('#book-translation').evaluate(details => { details.open = true; });
    await page.locator('#book-reproof-action').click();
    await page.waitForFunction(() => document.querySelector('#book-reproof-state').textContent === '校对中');
    assert.equal(await percent.textContent(), '23.0%');
    revise({ completed: 100, state: 'completed', completedChapters: 1 }); await waitPercent('100.0%');
    await page.locator('#book-reproof-action').click(); await waitPercent('0.0%');
    assert.equal(review.round, 2);
    assert.equal(await page.locator('.library-book[data-book="book-0"] .library-reading-progress .library-percent').textContent(), '1.0%');
    assert.equal(await page.locator('.library-book[data-book="book-1"] .library-translation-progress .library-percent').textContent(), '100.0%');
    await page.setViewportSize({ width: 390, height: 844 }); await page.locator('#sidebar-toggle').click();
    await page.locator('#library-view-covers3').click();
    assert.equal(await percent.textContent(), '0.0%');
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    console.log(JSON.stringify({ newPass: '0%', advance: '23%', polling: 'no reset to 100%', pauseReloadResume: '23%', complete: '100%', nextRound: '0%', readingAndOtherBooks: 'unchanged', mobile: true }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
