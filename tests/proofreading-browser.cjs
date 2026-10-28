/* Isolated UI fixtures: no real book, settings, or model mutations. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modules = process.env.PARALLEL_NODE_MODULES;
const { chromium } = require(modules ? path.join(modules, 'playwright') : 'playwright');

(async () => {
  const destination = path.resolve('test-results/proofreading-browser');
  fs.mkdirSync(destination, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
  const book = { id: 'proofreading-fixture', title: "The Devil's Hand", author: 'Carr, Jack', cover: null, language: 'en', currentPage: 0, startPage: 0,
    chapters: [{ id: 0, title: 'Chapter 1', startPage: 0, endPage: 0 }],
    pages: [{ id: 0, chapter: 0, paragraphs: [0] }],
    paragraphs: [{ id: 0, chapter: 0, kind: 'paragraph', text: 'Director Morgan waited.' }] };
  let settings = { baseUrl: 'https://translation.example.invalid/v1', model: 'Qwen3.6-27B', targetLanguage: '简体中文', apiKey: '', hasApiKey: true,
    reviewEnabled: true, reviewBaseUrl: 'https://review.example.invalid/v1', reviewModel: 'Qwen/Qwen3.8-27B-FP8', reviewApiKey: '', hasReviewApiKey: true, profile: 'review-fixture' };
  let saved, tests = 0;
  const errors = [], unexpected = [];
  await context.addInitScript(() => localStorage.setItem('parallel.prefetch', 'false'));
  await context.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    if (endpoint.endsWith('/translation-sources')) return route.fulfill({contentType:'application/json',body:JSON.stringify({configurationProfile:settings.profile,selectedProfile:settings.profile,selectionRequired:false,sources:[]})});
    let value;
    if (endpoint === '/api/settings') {
      if (request.method() === 'POST') { saved = request.postDataJSON(); settings = { ...settings, ...saved }; }
      value = settings;
    } else if (endpoint === '/api/model/test') { tests++; assert.equal(request.postDataJSON().reviewEnabled, true); value = { ok: true }; }
    else if (endpoint === '/api/books') value = [{ ...book, pageCount: 1 }];
    else if (endpoint === '/api/books/' + book.id) value = book;
    else if (endpoint === '/api/sample') value = { available: false };
    else if (endpoint.endsWith('/window')) value = { profile: settings.profile };
    else if (endpoint.endsWith('/progress')) value = { ok: true };
    else if (endpoint.endsWith('/status')) value = {
      profile: settings.profile, pages: [{ page: 0, ready: 0, total: 1 }], items: { 0: { state: 'translating', stage: 'proofreading' } },
      bookTranslation: { state: 'idle', completed: 0, total: 1, drafted: 1, failed: 0, percent: 0, model: settings.model,
        reviewEnabled: settings.reviewEnabled, reviewModel: settings.reviewModel, targetLanguage: settings.targetLanguage,
        completedChapters: 0, totalChapters: 1, parallelChapters: 4, activeChapters: [] }
    };
    else { unexpected.push(endpoint); value = { error: 'Unexpected fixture request' }; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(process.env.PARALLEL_BASE_URL || 'http://127.0.0.1:8765');
    await page.locator('.translation.translating').waitFor();
    assert.match(await page.locator('.translation').textContent(), /3.8 正在校对/);
    await page.locator('.book-translation-summary').click();
    assert.match(await page.locator('#book-translation-count').textContent(), /0 \/ 1 段已校对.*已有初译 1 段/);
    assert.match(await page.locator('#book-translation-action').textContent(), /开始校对/);
    assert.match(await page.locator('#book-translation-model').textContent(), /Qwen3.6-27B → Qwen\/Qwen3.8-27B-FP8 校对/);
    await page.locator('#model-badge').click();
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    const form = page.locator('#settings-form');
    assert.equal(await form.locator('[name=reviewEnabled]').isChecked(), true);
    await page.locator('#llm-provider-settings .review-settings summary').click();
    assert.equal(await form.locator('[name=reviewModel]').inputValue(), 'Qwen/Qwen3.8-27B-FP8');
    assert.equal(await form.locator('[name=reviewApiKey]').inputValue(), '');
    assert.match(await form.locator('[name=reviewApiKey]').getAttribute('placeholder'), /已保存/);
    await page.locator('#test-model').click();
    await page.getByText('连接成功，初译和校对模型均可用。').waitFor();
    assert.equal(tests, 1);
    await page.screenshot({ path: path.join(destination, 'settings-desktop.png') });
    await form.locator('[type=submit]').click();
    await page.locator('#settings-dialog').waitFor({ state: 'hidden' });
    assert.equal(saved.reviewEnabled, true);
    assert.equal(typeof saved.clearReviewApiKey, 'boolean');
    assert.equal(saved.reviewModel, 'Qwen/Qwen3.8-27B-FP8');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#reader-settings-button').click();
    await page.locator('#model-settings-button').click();
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const dialog = await page.locator('#settings-dialog').boundingBox();
    assert.ok(dialog.x >= 0 && dialog.x + dialog.width <= 390);
    await page.screenshot({ path: path.join(destination, 'settings-mobile.png') });
    assert.deepEqual(unexpected, []);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, checks: ['review-stage', 'draft-and-reviewed-progress', 'reuse-action', 'two-model-settings', 'masked-key', 'connection-feedback', 'boolean-save', 'mobile-settings'], screenshots: destination }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
