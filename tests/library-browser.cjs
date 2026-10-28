/* Shelf operations use isolated API data; production is read only for covers. */
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(path.join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
(async () => {
  const url = process.env.PARALLEL_BASE_URL || 'http://127.0.0.1:8765';
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const original = await (await context.request.get(url + '/api/books')).json();
    let books = original.slice(0, 6).map((book, id) => ({ ...book, id: 'shelf-' + id, pageCount: 100, currentPage: id * 15,
      bookmark: { page: 37, manual: true, updated: 1 },
      translationProgress: { completed: id ? id * 10 : 100, total: 100, percent: id ? id * 10 : 100, drafted: 100, reviewEnabled: true, profile: 'shelf-qa' } }));
    const errors = [], mutations = [], unexpected = [], profile = 'shelf-qa';
    const task = { state: 'completed', total: 100, completed: 100, percent: 100, failed: 0, model: 'QA',
      targetLanguage: '简体中文', completedChapters: 4, totalChapters: 4, parallelChapters: 4, activeChapters: [] };
    let rejectRemoval = false;
    await context.addInitScript(() => { localStorage.setItem('parallel.prefetch', 'false'); localStorage.setItem('parallel.theme', 'green'); });
    await context.route('**/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (endpoint.includes('/images/') && request.method() === 'GET') return route.continue();
      const id = endpoint.split('/')[3], summary = books.find(book => book.id === id);
      let value, status = 200;
      if (endpoint === '/api/settings') value = { model: 'QA', profile, targetLanguage: '简体中文' };
      else if (endpoint === '/api/books') value = books;
      else if (endpoint === '/api/library-progress') value = { profile, books: Object.fromEntries(books.map(book => [book.id, book.translationProgress])) };
      else if (endpoint === '/api/sample') value = { available: false };
      else if (!summary) { value = { error: '书籍不存在' }; status = 404; }
      else if (endpoint.endsWith('/remove')) {
        mutations.push(id);
        if (rejectRemoval) { value = { error: '测试：保存失败' }; status = 502; }
        else { books = books.filter(book => book.id !== id); value = { ok: true }; }
      } else if (endpoint.endsWith('/progress')) { summary.currentPage = request.postDataJSON().page; value = { ok: true }; }
      else if (endpoint.endsWith('/window')) value = { profile };
      else if (endpoint.endsWith('/pretranslate')) value = task;
      else if (endpoint.endsWith('/status')) value = { profile, pages: [], bookTranslation: task, items: Object.fromEntries(Array.from({ length: 100 }, (_, id) => [id,
        { state: 'ready', text: '这是阅读内容。', sourceSentences: ['This is a reading paragraph.'], targetSentences: ['这是阅读内容。'], alignment: [{ source: [0], target: [0] }] }])) };
      else if (endpoint === '/api/books/' + id) value = { ...summary, language: 'en',
        chapters: Array.from({ length: 4 }, (_, id) => ({ id, title: 'Chapter ' + (id + 1), startPage: id * 25, endPage: id * 25 + 24, startParagraph: id * 25 })),
        pages: Array.from({ length: 100 }, (_, id) => ({ id, chapter: Math.floor(id / 25), paragraphs: [id] })),
        paragraphs: Array.from({ length: 100 }, (_, id) => ({ id, chapter: Math.floor(id / 25), kind: 'paragraph', text: 'This is a reading paragraph.' })) };
      else { unexpected.push(endpoint); value = { error: 'Unexpected fixture request' }; status = 404; }
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(url, { waitUntil: 'networkidle' }); await page.locator('#tab-library').click();
    await page.locator('#library-view-covers3').click();
    const columns = () => page.locator('#library-list').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length);
    assert.equal(await columns(), 3);
    assert.equal(await page.locator('.library-book-info strong').first().isVisible(), false);
    assert.match(await page.locator('.library-percent').first().textContent(), /^\d+\.\d%$/);
    assert.equal(await page.locator('.library-book').first().locator('progress').count(), 2);
    assert.equal(await page.locator('.library-book[data-book="shelf-1"] .library-translation-progress .library-percent').textContent(), '10.0%');
    books[1].translationProgress = { ...books[1].translationProgress, completed: 65, percent: 65.0 };
    await page.waitForFunction(() => document.querySelector('.library-book[data-book="shelf-1"] .library-translation-progress .library-percent').textContent === '65.0%');
    assert.ok(await page.locator('.library-book .library-progress').evaluateAll(rows => rows.every(row => {
      const bar = row.querySelector('progress').getBoundingClientRect(), percent = row.querySelector('.library-percent').getBoundingClientRect();
      return percent.left >= bar.right && Math.abs(percent.top + percent.height / 2 - bar.top - bar.height / 2) < 2;
    })), 'Each percentage follows its own progress bar on the same row');
    await page.reload({ waitUntil: 'networkidle' }); await page.locator('#tab-library').click();
    assert.equal(await page.locator('#library-list').getAttribute('data-view'), 'covers3');
    const directory = path.resolve('test-results/library-view'); fs.mkdirSync(directory, { recursive: true });
    await page.waitForFunction(() => [...document.querySelectorAll('.book-cover')].every(image => image.complete && image.naturalWidth > 0));
    await page.screenshot({ path: path.join(directory, 'three-columns-desktop.png') });
    await page.locator('.library-book').first().click({ button: 'right' });
    await page.locator('#library-menu').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape'); assert.equal(await page.locator('#library-menu').isVisible(), false);
    await page.locator('.library-book[data-book="shelf-1"]').focus(); await page.keyboard.press('Shift+F10');
    await page.locator('#library-remove').waitFor({ state: 'visible' });
    rejectRemoval = true; await page.locator('#library-remove').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('保存失败'));
    assert.equal(await page.locator('.library-book').count(), 6);
    rejectRemoval = false;
    await page.locator('.library-book[data-book="shelf-1"]').click({ button: 'right' }); await page.locator('#library-remove').click();
    await page.locator('.library-book[data-book="shelf-1"]').waitFor({ state: 'detached' });
    assert.equal(await page.locator('#reader').isVisible(), true, 'Removing another book keeps current reading open');
    await page.locator('.library-book[data-book="shelf-0"]').click({ button: 'right' }); await page.locator('#library-remove').click();
    await page.locator('#welcome').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#tab-toc').isDisabled(), true);
    await page.locator('.library-book').first().click();
    await page.waitForFunction(() => document.querySelector('#page-input').value === '38');
    await page.locator('#tab-library').click();
    await page.setViewportSize({ width: 390, height: 844 }); await page.locator('#sidebar-toggle').click();
    assert.equal(await columns(), 3);
    assert.ok(await page.locator('#library-list').evaluate(node => node.scrollWidth <= node.clientWidth));
    await page.screenshot({ path: path.join(directory, 'three-columns-mobile.png') });
    for (const view of ['list', 'covers', 'covers3']) {
      await page.locator('#library-view-' + view).click();
      await page.locator('.library-book').first().click({ button: 'right' });
      await page.locator('#library-remove').waitFor({ state: 'visible' });
      await page.keyboard.press('Escape');
    }
    while (await page.locator('.library-book').count()) {
      await page.locator('.library-book').first().click({ button: 'right' });
      const count = await page.locator('.library-book').count();
      await page.locator('#library-remove').click();
      await page.waitForFunction(count => document.querySelectorAll('.library-book').length === count - 1, count);
    }
    assert.equal(await page.locator('#side-empty').isVisible(), true);
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.locator('.library-book').count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    console.log(JSON.stringify({ threeColumns: 'desktop/mobile', preference: 'persisted', rightClick: 'all views', keyboard: 'supported', failedRemoval: 'preserves book', currentBookRemoval: 'returns to shelf', allBooksRemoved: 'empty state', mutations: mutations.length }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
