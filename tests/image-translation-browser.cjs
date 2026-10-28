/* Deterministic image OCR UI; no model calls or production progress changes. */
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(path.join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
(async () => {
  const url = process.env.PARALLEL_BASE_URL || 'http://127.0.0.1:8765';
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.addInitScript(() => localStorage.setItem('parallel.prefetch', 'false'));
    const profile = 'image-ui-qa', asset = 'sample', errors = [], unexpected = [];
    const book = { id: 'image-ui-qa', title: 'Illustrated Reader', author: 'QA', language: 'en', cover: null, currentPage: 0,
      chapters: [{ id: 0, title: 'A Quiet Return', startPage: 0, endPage: 0, startParagraph: 0 }],
      paragraphs: [{ id: 0, chapter: 0, kind: 'paragraph', text: 'The rain had stopped.' }, { id: 1, chapter: 0, kind: 'paragraph', text: 'He opened the door.' }],
      pages: [{ id: 0, chapter: 0, paragraphs: [0, 1], content: [{ type: 'paragraph', id: 0 }, { type: 'image', id: 0 }, { type: 'paragraph', id: 1 }] }],
      images: { [asset]: { width: 1388, height: 2000 } }, illustrations: [{ id: 0, asset, alt: 'Annotated clothing', beforeParagraph: 1 }] };
    const final = { imageUrl: '/api/books/image-ui-qa/translated-images/sample?v=qa', has_text: true, reviewed: true, sourceModel: 'Qwen3.6-27B', reviewModel: 'Qwen/Qwen3.8-27B-FP8', language: '简体中文',
      blocks: [{ source: 'Sunglasses', translation: '太阳镜', uncertain: false },
        { source: 'A <script>bad()</script>', translation: '<img src=x onerror=bad()>', uncertain: true }], warnings: ['一处小字识别存疑'] };
    const task = { state: 'completed', completed: 2, total: 2, percent: 100, failed: 0, model: 'QA', targetLanguage: '简体中文', profile,
      completedChapters: 1, totalChapters: 1, parallelChapters: 4, activeChapters: [] };
    let imageState = { state: 'idle' }, posts = 0, failNext = false, firstGet = true, releaseGet;
    await context.route('**/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (endpoint.endsWith('/translation-sources')) return route.fulfill({contentType:'application/json',body:JSON.stringify({configurationProfile:profile,selectedProfile:profile,selectionRequired:false,sources:[]})});
      if (endpoint.endsWith('/images/' + asset) || endpoint.endsWith('/translated-images/' + asset)) return route.fulfill({ contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="1388" height="2000"><rect width="1388" height="2000" fill="white"/><text x="80" y="160" font-size="80">Sunglasses</text></svg>' });
      let value;
      if (endpoint === '/api/settings') value = { model: 'QA', reviewEnabled: true, reviewModel: 'QA-review', targetLanguage: '简体中文', profile };
      else if (endpoint === '/api/books') value = [{ ...book, pageCount: 1 }];
      else if (endpoint === '/api/sample') value = { available: false };
      else if (endpoint === '/api/library-progress') value = { profile, books: {} };
      else if (endpoint.endsWith('/image-translations/' + asset)) {
        if (request.method() === 'POST') {
          posts++;
          releaseGet?.();
          if (failNext) { failNext = false; imageState = { state: 'error', error: '测试：图片校对失败', result: final }; }
          else {
            imageState = { state: 'queued' };
            setTimeout(() => { imageState = { state: 'proofreading', result: { ...final, reviewed: false, reviewModel: '' } }; }, 700);
            setTimeout(() => { imageState = { state: 'ready', cached: true, result: final }; }, 2000);
          }
          value = imageState;
        } else if (firstGet) {
          firstGet = false;
          await new Promise(resolve => { releaseGet = resolve; });
          value = { state: 'idle' }; // A stale status must not overwrite the just-started job.
        } else value = imageState;
      } else if (endpoint.endsWith('/status')) value = { profile, pages: [], items: {}, bookTranslation: task };
      else if (endpoint.endsWith('/window')) value = { profile };
      else if (endpoint.endsWith('/progress')) value = { ok: true };
      else if (endpoint.endsWith('/pretranslate')) value = task;
      else if (endpoint === '/api/books/' + book.id) value = book;
      else { unexpected.push(endpoint); value = {}; }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    const figure = page.locator('.illustration-row'), action = figure.locator('.image-translation-action');
    await action.scrollIntoViewIfNeeded();
    await page.waitForTimeout(100);
    await action.click();
    await page.waitForFunction(() => document.querySelector('.illustration-row .image-translation-action').disabled);
    await page.waitForFunction(() => document.querySelector('.illustration-row .image-translation-status').textContent.includes('已校对并保存'));
    assert.equal(posts, 1);
    assert.equal(await figure.locator('.image-text-block, .image-translation-panel').count(), 0, 'No separate text list');
    for (const mode of ['original', 'translation', 'both', 'follow']) {
      await page.locator('#show-' + mode).click();
      const src = await figure.locator('img').getAttribute('src');
      assert.equal(src.includes('/translated-images/'), mode !== 'original');
    }
    await figure.locator('.image-original').click(); assert.ok((await figure.locator('img').getAttribute('src')).includes('/images/'));
    await figure.locator('.image-translated').click(); assert.ok((await figure.locator('img').getAttribute('src')).includes('/translated-images/'));
    await page.reload({ waitUntil: 'domcontentloaded' }); await figure.locator('.image-translation-action').scrollIntoViewIfNeeded();
    await figure.locator('.image-translated').waitFor(); assert.equal(posts, 1, 'Reload reuses the stored result without a new model request');
    failNext = true; await figure.locator('.image-translation-action').click();
    await page.waitForFunction(() => document.querySelector('.illustration-row .image-translation-status').textContent.includes('校对失败'));
    assert.ok((await figure.locator('img').getAttribute('src')).includes('/translated-images/'), 'A failed refresh preserves the previous image');
    await figure.locator('.image-translation-action').click();
    await page.waitForFunction(() => document.querySelector('.illustration-row .image-translation-status').textContent.includes('已校对并保存'));
    await page.locator('#show-both').click();
    await figure.locator('.illustration-open').click();
    const dialog = page.locator('.illustration-viewer[open]');
    await dialog.locator('.image-translated').waitFor();
    fs.mkdirSync('test-results/image-translation', { recursive: true });
    await page.screenshot({ path: 'test-results/image-translation/desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => { const canvas = document.querySelector('.illustration-viewer[open] .illustration-canvas'); return canvas.querySelector('img').getBoundingClientRect().width <= canvas.clientWidth; });
    await page.locator('[data-theme="dark"]').evaluate(button => button.click());
    const layout = await dialog.evaluate(node => {
      return { scrollWidth: node.scrollWidth, width: node.clientWidth };
    });
    assert.ok(layout.scrollWidth <= layout.width);
    await page.screenshot({ path: 'test-results/image-translation/mobile-dark.png' });
    await page.keyboard.press('Escape');
    await page.locator('#immersive-button').click(); await figure.locator('.illustration-open').click();
    await dialog.locator('.image-translated').waitFor(); await page.keyboard.press('Escape');
    assert.equal(await page.locator('body').evaluate(node => node.classList.contains('immersive')), true);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    console.log(JSON.stringify({ allModes: true, cachedReload: true, retryPreservesResult: true, staleStatusHandled: true, escapedText: true,
      zoomViewer: true, mobileDark: true, immersiveEscape: true, modelCalls: 0 }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
