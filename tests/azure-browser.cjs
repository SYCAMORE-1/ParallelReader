/* Synthetic browser coverage: provider settings, saved keys, and original images. */
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const { chromium } = require(path.join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
(async () => {
  const root = path.resolve('public');
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname.replace(/^\/$/, '/index.html'));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' })[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const errors = [], unexpected = [], submitted = [];
    let settings = { translationProvider: 'llm', baseUrl: 'https://llm.example.invalid/v1', model: 'test-text',
      reviewEnabled: false, reviewBaseUrl: '', reviewModel: '', apiKey: '', reviewApiKey: '', wordApiKey: '',
      azureApiKey: '', hasAzureApiKey: false, targetLanguage: '简体中文', profile: 'llm-fixture' };
    const book = { id: 'azure-ui-fixture', title: 'A Quiet Return', author: 'Test Author', language: 'en', currentPage: 0, startPage: 0,
      chapters: [{ id: 0, title: 'Morning', startPage: 0, endPage: 0, startParagraph: 0 }],
      paragraphs: [{ id: 0, chapter: 0, kind: 'paragraph', text: 'The rain stopped.' }],
      pages: [{ id: 0, chapter: 0, paragraphs: [0], content: [{ type: 'paragraph', id: 0 }, { type: 'image', id: 0 }] }],
      images: { demo: { width: 400, height: 200 } }, illustrations: [{ id: 0, asset: 'demo', afterParagraph: 0 }] };
    const task = () => ({ state: 'completed', completed: 1, total: 1, drafted: 1, percent: 100, failed: 0,
      provider: settings.translationProvider, model: settings.translationProvider === 'azure' ? 'Azure Translator' : settings.model,
      reviewEnabled: false, targetLanguage: '简体中文', profile: settings.profile, totalChapters: 1, completedChapters: 1, activeChapters: [], parallelChapters: 4 });
    await context.route('**/api/**', async route => {
      const request = route.request(), ep = new URL(request.url()).pathname;
      let value;
      if (ep === '/api/settings') {
        if (request.method() === 'POST') {
          const data = request.postDataJSON(); submitted.push(data);
          settings = { ...settings, ...data, azureApiKey: '', hasAzureApiKey: !!data.azureApiKey || settings.hasAzureApiKey,
            profile: data.translationProvider + '-fixture' }; value = { ok: true };
        } else value = settings;
      } else if (ep === '/api/model/test') { submitted.push(request.postDataJSON()); value = { ok: true, provider: 'azure' }; }
      else if (ep === '/api/books') value = [{ ...book, pageCount: 1 }];
      else if (ep === '/api/books/' + book.id) value = book;
      else if (ep === '/api/sample') value = { available: false };
      else if (ep === '/api/library-progress') value = { profile: settings.profile, books: { [book.id]: task() } };
      else if (ep.endsWith('/reproofread')) value = { jobs: [] };
      else if (ep.endsWith('/translation-sources')) value = { book: book.id, configurationProfile: settings.profile, selectedProfile: settings.profile, selectionRequired: false, completed: 1, total: 1, sources: [] };
      else if (ep.endsWith('/pretranslate')) value = task();
      else if (ep.endsWith('/window')) value = { profile: settings.profile };
      else if (ep.endsWith('/progress')) value = { ok: true };
      else if (ep.endsWith('/status')) value = { profile: settings.profile, pages: [], bookTranslation: task(), items: {
        0: { state: 'ready', text: '雨停了。', sourceSentences: ['The rain stopped.'], targetSentences: ['雨停了。'], alignment: [{ source: [0], target: [0] }] } } };
      else if (ep.endsWith('/images/demo')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="white"/><text x="10" y="50">Original illustration</text></svg>' });
      else if (ep.endsWith('/image-translations/demo')) value = { state: ['azure', 'microsoft'].includes(settings.translationProvider) ? 'disabled' : 'idle', result: null };
      else { unexpected.push(ep); value = {}; }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:' + server.address().port);
    await page.locator('#model-badge').click();
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    await page.locator('#translation-provider').selectOption('azure');
    assert.equal(await page.locator('[name=baseUrl]').isDisabled(), true);
    assert.equal(await page.locator('#llm-provider-settings').isVisible(), false);
    await page.locator('[name=azureEndpoint]').fill('https://azure.example.invalid');
    await page.locator('[name=azureApiKey]').fill('synthetic-ui-test-key');
    await page.locator('[name=azureRegion]').fill('test-region');
    assert.equal(await page.locator('#settings-form').evaluate(form => form.checkValidity()), true);
    await page.locator('#test-model').click();
    await page.waitForFunction(() => document.querySelector('#connection-result').textContent.includes('Azure 文字翻译可用'));
    await page.locator('#settings-form button[type=submit]').click();
    await page.locator('#settings-dialog').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#model-badge').innerText(), /Azure Translator/);
    assert.equal(submitted.at(-1).baseUrl, undefined, 'Inactive LLM fields must not overwrite stored configuration');
    await page.waitForFunction(() => document.querySelector('.image-translation-status')?.textContent.includes('图片保留原图'));
    assert.equal(await page.locator('.image-translation-action').isVisible(), false);
    assert.equal(await page.locator('#book-reproof-action').isVisible(), false);
    assert.match(await page.locator('.illustration-row img').first().getAttribute('src'), /\/images\/demo/);
    await page.locator('#model-badge').click();
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[name=azureApiKey]').inputValue(), '');
    assert.equal(await page.locator('#settings-form').evaluate(form => form.checkValidity()), true, 'Saved key may remain blank');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#settings-dialog').evaluate(el => el.getBoundingClientRect().width <= innerWidth), true);
    await page.locator('#translation-provider').selectOption('llm');
    assert.equal(await page.locator('[name=baseUrl]').inputValue(), 'https://llm.example.invalid/v1');
    assert.equal(await page.locator('[name=azureApiKey]').isDisabled(), true);
    await page.locator('#settings-form button[type=submit]').click();
    await page.locator('#settings-dialog').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#model-badge').innerText(), /test-text/);
    await page.locator('#model-badge').click();
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    await page.locator('#translation-provider').selectOption('microsoft');
    assert.equal(await page.locator('#settings-form').evaluate(form => form.checkValidity()), true);
    for (const name of ['baseUrl', 'model', 'azureEndpoint', 'azureApiKey', 'azureRegion']) {
      assert.equal(await page.locator('[name=' + name + ']').isDisabled(), true, name + ' must not be required for keyless translation');
    }
    await page.locator('#test-model').click();
    await page.waitForFunction(() => document.querySelector('#connection-result').textContent.includes('微软免配置翻译可用'));
    assert.equal(submitted.at(-1).azureApiKey, undefined);
    assert.equal(submitted.at(-1).baseUrl, undefined);
    await page.locator('#settings-form button[type=submit]').click();
    await page.locator('#settings-dialog').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#model-badge').innerText(), /免配置/);
    assert.equal(await page.locator('#book-reproof-action').isVisible(), false);
    await page.waitForFunction(() => document.querySelector('.image-translation-status')?.textContent.includes('图片保留原图'));
    assert.equal(await page.locator('.image-translation-action').isVisible(), false);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    console.log('Microsoft keyless + Azure browser checks passed: settings, key masking, cache switching, original images, mobile layout.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
