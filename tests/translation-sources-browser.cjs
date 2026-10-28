/* Simulate saved versions; all API traffic and translation work remain local fixtures. */
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const { chromium } = require(path.join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
(async () => {
  const root = path.resolve('public');
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname.replace(/^\/$/, '/index.html'));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' })[path.extname(file)] || 'application/octet-stream'); res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const errors = [], unexpected = [];
    let settings = { translationProvider: 'llm', profile: 'llm-profile', wordProvider: 'microsoft',
      baseUrl: 'https://llm.example.invalid/v1', model: 'fixture-model', reviewEnabled: false, targetLanguage: '简体中文' };
    const book = { id: 'source-ui-fixture', title: 'Saved Versions', author: 'Test Author', language: 'en', currentPage: 0, startPage: 0,
      chapters: [{ id: 0, title: 'Morning', startPage: 0, endPage: 0, startParagraph: 0 }],
      paragraphs: [{ id: 0, chapter: 0, kind: 'paragraph', text: 'The traveler sat on the bank.' }],
      pages: [{ id: 0, chapter: 0, paragraphs: [0] }], images: {}, illustrations: [] };
    const saved = { 'llm-profile': { text: '大模型原有译文。', provider: 'llm', model: 'fixture-model' } }, choices = {};
    let generated = 0, words = 0;
    const inventory = () => {
      const current = settings.profile, selected = choices[current] || current;
      const sources = Object.entries(saved).map(([profile, value]) => ({ profile, provider: value.provider, model: value.model,
        completed: 1, total: 1, percent: 100, targetLanguage: '简体中文', current: profile === current }));
      return { book: book.id, configurationProfile: current, selectedProfile: selected, selectionRequired: !!sources.length && !saved[current] && !choices[current], sources,
        completed: saved[selected] || saved[current] ? 1 : 0, total: 1, percent: saved[selected] || saved[current] ? 100 : 0 };
    };
    const task = () => ({ state: saved[settings.profile] ? 'completed' : 'idle', completed: saved[settings.profile] ? 1 : 0,
      total: 1, drafted: 0, percent: saved[settings.profile] ? 100 : 0, failed: 0, model: settings.translationProvider,
      provider: settings.translationProvider, reviewEnabled: false, targetLanguage: '简体中文', profile: settings.profile, totalChapters: 1, completedChapters: 0, activeChapters: [], parallelChapters: 4 });
    await context.route('**/api/**', async route => {
      const request = route.request(), ep = new URL(request.url()).pathname;
      let value;
      if (ep === '/api/settings') {
        if (request.method() === 'POST') { const data = request.postDataJSON(); settings = { ...settings, ...data, profile: data.translationProvider + '-profile', azureApiKey: '', hasAzureApiKey: !!data.azureApiKey }; value = { ok: true }; }
        else value = settings;
      } else if (ep === '/api/books') value = [{ ...book, pageCount: 1 }];
      else if (ep === '/api/books/' + book.id) value = book;
      else if (ep === '/api/sample') value = { available: false };
      else if (ep === '/api/library-progress') value = { profile: settings.profile, books: { [book.id]: task() } };
      else if (ep.endsWith('/translation-sources')) {
        if (request.method() === 'POST') choices[settings.profile] = request.postDataJSON().sourceProfile;
        value = inventory();
      } else if (ep.endsWith('/window')) {
        const source = inventory();
        if (!source.selectionRequired && !saved[source.selectedProfile] && !saved[settings.profile]) {
          generated++; saved[settings.profile] = { text: '微软新译文。', provider: settings.translationProvider, model: '微软免配置' };
        }
        value = { profile: settings.profile, needsTranslationChoice: source.selectionRequired };
      } else if (ep.endsWith('/status')) {
        const source = inventory(), fingerprint = saved[source.selectedProfile] ? source.selectedProfile : settings.profile, item = saved[fingerprint];
        value = { profile: settings.profile, readingProfile: source.selectedProfile, needsTranslationChoice: source.selectionRequired,
          pages: [], readingTranslation: source, bookTranslation: task(), reproofreading: [], items: item ? { 0: {
            state: 'ready', text: item.text, translationProfile: fingerprint, sourceSentences: [book.paragraphs[0].text], targetSentences: [item.text], alignment: [{ source: [0], target: [0] }] } } : {} };
      } else if (ep.endsWith('/word')) {
        words++; assert.equal(settings.wordProvider, 'microsoft');
        value = { lemma: 'bank', partOfSpeech: 'n.', basicMeaning: '银行', contextMeaning: '河岸', note: '', provider: 'microsoft', contextKind: 'term', contextWindow: { before: 1, after: 0 } };
      } else if (ep.endsWith('/reproofread')) value = { jobs: [] };
      else if (ep.endsWith('/pretranslate')) value = task();
      else if (ep.endsWith('/progress')) value = { ok: true };
      else { unexpected.push(ep); value = {}; }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const ready = text => page.waitForFunction(text => document.querySelector('.translation')?.textContent.includes(text), text);
    await page.goto('http://127.0.0.1:' + server.address().port); await ready('大模型原有译文。');
    assert.equal(generated, 0); assert.match(await page.locator('#translation-source-button').innerText(), /LLM/);
    await page.locator('#model-badge').click();
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#word-provider').inputValue(), 'microsoft');
    await page.locator('#translation-provider').selectOption('microsoft');
    await page.locator('#settings-form button[type=submit]').click();
    await page.locator('#translation-source-dialog').waitFor({ state: 'visible' });
    assert.equal(generated, 0, 'Must not translate before the user chooses');
    await page.locator('#use-saved-translation').click(); await ready('大模型原有译文。');
    assert.equal(generated, 0); assert.match(await page.locator('#model-badge').innerText(), /免配置/);
    assert.match(await page.locator('#translation-source-button').innerText(), /LLM/);
    assert.equal(await page.locator('#book-exports').evaluate(el => !el.hidden), true, 'Complete saved versions can be exported');
    await page.reload(); await ready('大模型原有译文。');
    assert.equal(await page.locator('#translation-source-dialog').isVisible(), false, 'Remember the reading source on reload');
    await page.locator('#translation-source-button').click();
    await page.locator('#translation-source-dialog').waitFor({ state: 'visible' });
    await page.locator('#translate-with-current').click(); await ready('微软新译文。');
    assert.equal(generated, 1); assert.equal(saved['llm-profile'].text, '大模型原有译文。');
    await page.locator('#model-badge').click(); await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    await page.locator('#translation-provider').selectOption('azure');
    await page.locator('[name=azureEndpoint]').fill('https://azure.example.invalid');
    await page.locator('[name=azureApiKey]').fill('synthetic-test-key');
    await page.locator('#settings-form button[type=submit]').click();
    await page.locator('#translation-source-dialog').waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#translation-source-dialog').evaluate(el => el.getBoundingClientRect().width <= innerWidth), true);
    await page.locator('#translation-source-select').selectOption('microsoft-profile');
    await page.locator('#use-saved-translation').click(); await ready('微软新译文。'); assert.equal(generated, 1);
    await page.locator('.original .sentence').first().scrollIntoViewIfNeeded();
    const point = await page.evaluate(() => {
      const walker = document.createTreeWalker(document.querySelector('.original .sentence'), NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode, start = node.textContent.indexOf('bank');
        if (start < 0) continue;
        const range = document.createRange(); range.setStart(node, start); range.setEnd(node, start + 4);
        const rect = range.getBoundingClientRect(); return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
      }
    });
    await page.mouse.dblclick(point.x, point.y);
    await page.waitForFunction(() => document.querySelector('#word-basic').textContent === '银行');
    assert.equal(await page.locator('#word-meaning').innerText(), '河岸'); assert.equal(words, 1);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    console.log('Saved-source switching, no duplicate translation, explicit new translation, persistence, export and keyless word lookup: PASS');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
