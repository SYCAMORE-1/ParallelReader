/* Exercise the real reader UI with isolated API fixtures; no real book/model mutations. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const modules = process.env.PARALLEL_NODE_MODULES;
const { chromium } = require(modules ? path.join(modules, 'playwright') : 'playwright');

(async () => {
  const url = process.env.PARALLEL_BASE_URL || 'http://127.0.0.1:8765';
  const destination = path.resolve('test-results/reading-modes');
  fs.mkdirSync(destination, { recursive: true });
  const baseParagraphs = [
    ['Morgan paused beside the old house. ', 'He listened to the quiet street.', 'Morgan在旧房子旁停下。', '他听着安静街道上的声音。'],
    ['The curtains moved behind the window. ', 'Someone was waiting inside.', '窗帘在窗后轻轻晃动。', '有人正在屋里等候。'],
    ['He took a slow breath. ', 'The door opened before he knocked.', '他缓缓吸了一口气。', '还没等他敲门，门就开了。'],
    ['“Come in,” his friend said. ', 'The house was warm and bright.', '“进来吧，”他的朋友说。', '屋里温暖而明亮。'],
  ];
  const paragraphs = Array.from({ length: 10 }, () => baseParagraphs).flat();
  let currentPage = 0;
  const book = { id: 'reading-modes-fixture', title: 'A Quiet Return', author: 'Parallel · 阅读模式', language: 'en', cover: null, startPage: 0,
    chapters: ['The Old House', 'An Unexpected Guest', 'The Road Ahead', 'Home Again'].map((title, id) => ({ id, title, startPage: id * 5, endPage: id * 5 + 4, startParagraph: id * 10 })),
    pages: Array.from({ length: 20 }, (_, id) => ({ id, chapter: Math.floor(id / 5), paragraphs: [id * 2, id * 2 + 1] })),
    paragraphs: paragraphs.map((parts, id) => ({ id, chapter: Math.floor(id / 10), kind: 'paragraph', text: parts[0] + parts[1] })) };
  const profile = 'reading-mode-qa';
  const task = { state: 'completed', completed: 40, total: 40, failed: 0, percent: 100, model: 'QA-model', targetLanguage: '简体中文', profile,
    completedChapters: 4, totalChapters: 4, parallelChapters: 4, activeChapters: [] };
  const items = Object.fromEntries(paragraphs.map((parts, id) => [id, { state: 'ready', text: parts[2] + parts[3], sourceSentences: parts.slice(0, 2), targetSentences: parts.slice(2),
    alignment: [{ source: [0], target: [0] }, { source: [1], target: [1] }], alignmentMethod: 'translation-slots' }]));
  // Some EPUBs encode their chapter title as an ordinary paragraph, in uppercase.
  book.paragraphs[30].text = 'HOME AGAIN';
  items[30] = { state: 'ready', text: '再次归来', sourceSentences: ['HOME AGAIN'], targetSentences: ['再次归来'],
    alignment: [{ source: [0], target: [0] }], alignmentMethod: 'translation-slots' };
  const redactedSource = ['😀 Morgan read [redacted], [Redacted X X X X X X X X X X X], xxx, [xxxx] and [xxx xxxxx]. ',
    'Behind xxx xxxxx xxxxxxxxx xxxx, he paused beside Exxon and an X-ray machine.'];
  const redactedTarget = ['Morgan看到了[已删节]、[已编辑 X X X X X X X X X X X]、xxx、[xxxx]和[xxx xxxxx]。',
    '在xxx xxxxx xxxxxxxxx xxxx后面，他在Exxon和X光机旁停了下来。'];
  book.paragraphs[32].text = redactedSource.join('');
  items[32] = { state: 'ready', text: redactedTarget.join(''), sourceSentences: redactedSource, targetSentences: redactedTarget,
    alignment: [{ source: [0], target: [0] }, { source: [1], target: [1] }], alignmentMethod: 'translation-slots' };
  book.paragraphs[33].text = 'A long redaction: [' + 'X'.repeat(80) + '] [Redacted].';
  items[33] = { state: 'ready', text: '长遮盖：[' + 'X'.repeat(80) + '] [机密]。', sourceSentences: [book.paragraphs[33].text], targetSentences: ['长遮盖：[' + 'X'.repeat(80) + '] [机密]。'],
    alignment: [{ source: [0], target: [0] }], alignmentMethod: 'translation-slots' };
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  const errors = [], unexpected = [], wordQueries = [];
  let alignments = 0;
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, reducedMotion: 'reduce' });
  await context.addInitScript(() => localStorage.setItem('parallel.prefetch', 'false'));
  await context.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    let value;
    if (endpoint === '/api/settings') value = { baseUrl: 'http://127.0.0.1:1/v1', model: 'QA-model', apiKey: '', targetLanguage: '简体中文', profile };
    else if (endpoint === '/api/books') value = [{ id: book.id, title: book.title, author: book.author, cover: null, currentPage, pageCount: book.pages.length }];
    else if (endpoint === '/api/sample') value = { available: false };
    else if (endpoint.endsWith('/word')) {
      wordQueries.push(request.postDataJSON());
      value = { lemma: 'pause', partOfSpeech: 'v.', basicMeaning: '暂停，停下。', contextMeaning: '这里指Morgan在房子旁停下脚步。', note: 'paused是过去式。', model: 'Qwen/Qwen3.5-9B', contextWindow: { before: 0, after: 0 } };
    } else if (endpoint.endsWith('/align')) { alignments++; value = items[request.postDataJSON().paragraph]; }
    else if (endpoint.endsWith('/progress')) { currentPage = request.postDataJSON().page; value = { ok: true }; }
    else if (endpoint.endsWith('/window')) {
      const { page: start, endPage = start, selected } = request.postDataJSON();
      assert.ok(endPage >= start && endPage - start < 8);
      if (selected !== null) assert.ok(book.pages.slice(start, endPage + 1).some(page => page.paragraphs.includes(selected)), 'Selected paragraphs belong to the current expanded window');
      value = { profile };
    }
    else if (endpoint.endsWith('/status')) {
      const query = new URL(request.url()).searchParams, start = Number(query.get('page')), end = Number(query.get('endPage') || start);
      const pages = book.pages.filter(page => page.id >= start - 2 && page.id <= end + 2);
      value = { profile, items: Object.fromEntries(pages.flatMap(page => page.paragraphs.map(pid => [pid, items[pid]]))), pages: pages.map(page => ({ page: page.id, ready: 2, total: 2 })), bookTranslation: task };
    }
    else if (endpoint.endsWith('/pretranslate')) value = task;
    else if (endpoint === '/api/books/' + book.id) value = { ...book, currentPage };
    else { unexpected.push(endpoint); value = { error: 'Unexpected fixture request' }; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  async function clickText(locator, double = false) {
    await locator.scrollIntoViewIfNeeded();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const point = await locator.evaluate((span, double) => {
      const start = Math.max(0, double ? span.firstChild.textContent.indexOf('paused') : span.firstChild.textContent.search(/\S/));
      const range = document.createRange(); range.setStart(span.firstChild, start); range.setEnd(span.firstChild, start + 1);
      const rect = range.getClientRects()[0]; return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    }, double);
    if (double) await page.mouse.dblclick(point.x, point.y); else await page.mouse.click(point.x, point.y);
  }
  async function assertFollow() {
    const rows = await page.locator('.paragraph-row').evaluateAll(nodes => nodes.map(row => {
      const source = row.querySelector('.original').getBoundingClientRect(), target = row.querySelector('.translation').getBoundingClientRect();
      return { source: { left: source.left, right: source.right, top: source.top, bottom: source.bottom }, target: { left: target.left, right: target.right, top: target.top, bottom: target.bottom } };
    }));
    for (const row of rows) {
      assert.ok(row.target.top >= row.source.bottom - 1, 'Each translation must follow its own original');
      assert.ok(Math.abs(row.target.left - row.source.left) < 1 && Math.abs(row.target.right - row.source.right) < 1, 'Both languages use the full single-column width');
    }
    for (let i = 1; i < rows.length; i++) assert.ok(rows[i].source.top > rows[i - 1].target.bottom, 'Next original must follow the previous translation');
  }
  async function selectMode(mode) {
    for (const choice of ['both', 'original', 'translation', 'follow']) assert.equal(await page.locator('#show-' + choice).isVisible(), true, 'Every mode stays visible');
    await page.locator('#show-' + mode).click();
    assert.equal(await page.locator('#reader').getAttribute('data-columns'), mode);
    assert.equal(await page.locator('.column-switch button[aria-pressed=true]').count(), 1);
    assert.equal(await page.locator('#show-' + mode).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('.original-label, .translation-label').count(), 0);
  }
  async function assertReadingNavigation() {
    const bounds = await page.evaluate(() => {
      const bar = document.querySelector('.topbar').getBoundingClientRect();
      const modes = document.querySelector('.column-labels').getBoundingClientRect();
      const button = document.querySelector('#reader-settings-button'), settings = button.getBoundingClientRect();
      const hit = document.elementFromPoint(settings.x + settings.width / 2, settings.y + settings.height / 2);
      return { top: bar.top, bottom: bar.bottom, modesTop: modes.top, buttonClickable: button.contains(hit), height: innerHeight };
    });
    assert.ok(bounds.top >= 0 && bounds.bottom <= bounds.height, 'Scrolling keeps the top navigation in the viewport');
    assert.ok(bounds.modesTop >= bounds.bottom - 1, 'Language and flow choices stay below the top navigation');
    assert.ok(bounds.buttonClickable, 'Settings is reachable without scrolling to the book start');
  }
  async function assertTheme(theme) {
    const colors = await page.evaluate(() => ({
      theme: document.documentElement.dataset.theme,
      background: getComputedStyle(document.documentElement).backgroundColor,
      source: getComputedStyle(document.querySelector('.original')).color,
      target: getComputedStyle(document.querySelector('.translation')).color,
      scheme: getComputedStyle(document.documentElement).colorScheme,
      navigation: ['.toc-item', '#header-title'].map(selector => ({ color: getComputedStyle(document.querySelector(selector)).color, size: parseFloat(getComputedStyle(document.querySelector(selector)).fontSize) })),
      modes: [...document.querySelectorAll('.column-switch button')].map(button => ({ color: getComputedStyle(button).color, background: getComputedStyle(button).backgroundColor })),
    }));
    assert.equal(colors.theme, theme);
    const luminance = value => value.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    for (const text of [colors.source, colors.target]) {
      const ink = luminance(text), paper = luminance(colors.background);
      assert.ok((Math.max(ink, paper) + .05) / (Math.min(ink, paper) + .05) >= 4.5, `${theme} text must remain legible`);
      if (['yellow', 'deep-green'].includes(theme)) assert.ok(ink < .03, 'Yellow and deep green require black text');
      if (theme === 'dark') assert.ok(ink > .8 && paper < .03, 'Dark theme requires white on black');
    }
    assert.equal(colors.scheme, theme === 'dark' ? 'dark' : 'light');
    for (const item of colors.navigation) { assert.equal(item.color, colors.source, 'Navigation and chapter names follow the body text color'); assert.ok(item.size >= 15); }
    for (const mode of colors.modes) {
      const ink = luminance(mode.color), paper = luminance(mode.background === 'rgba(0, 0, 0, 0)' ? colors.background : mode.background);
      assert.ok((Math.max(ink, paper) + .05) / (Math.min(ink, paper) + .05) >= 4.5, `${theme} must keep all four mode buttons readable`);
    }
  }
  try {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.locator('.translation .sentence').first().waitFor();
    assert.equal(await page.locator('#reader').getAttribute('data-columns'), 'both');
    assert.deepEqual(await page.locator('.topbar .toolbar > button').evaluateAll(buttons => buttons.map(button => button.id)), ['model-badge', 'reader-settings-button', 'immersive-button']);
    assert.equal(await page.locator('.reading-toolbar, #page-ready, #chapter-title').count(), 0);
    assert.equal(await page.locator('#reading-settings #prefetch-toggle').count(), 1);
    assert.equal(await page.locator('#prefetch-toggle').isVisible(), false);
    assert.equal(await page.locator('.column-switch button').count(), 4);
    assert.deepEqual(await page.locator('.column-switch button').evaluateAll(buttons => buttons.map(button => button.id)), ['show-original', 'show-translation', 'show-both', 'show-follow']);
    for (const button of await page.locator('.column-switch button').all()) {
      assert.equal((await button.textContent()).trim(), '', 'Mode choices contain icons without visible text');
      assert.equal(await button.locator('svg').count(), 1);
      assert.ok(await button.getAttribute('aria-label'));
    }
    for (const button of await page.locator('.flow-switch button').all()) {
      assert.equal((await button.textContent()).trim(), '', 'Reading flow choices also use icons without text');
      assert.equal(await button.locator('svg').count(), 1);
      assert.ok(await button.getAttribute('aria-label'));
    }
    assert.equal(await page.locator('#book-translation').getAttribute('open'), null);
    assert.equal(await page.locator('#book-translation-progress').isVisible(), true);
    assert.equal(await page.locator('#book-translation-action').isVisible(), false);
    const sideBySide = await page.locator('.paragraph-row').first().evaluate(row => {
      const a = row.children[0].getBoundingClientRect(), b = row.children[1].getBoundingClientRect();
      return Math.abs(a.top - b.top) < 1 && b.left >= a.right - 1;
    });
    assert.ok(sideBySide);
    await selectMode('original');
    assert.equal(await page.locator('.original').first().isVisible(), true);
    assert.equal(await page.locator('.translation').first().isVisible(), false);
    await selectMode('both');
    await selectMode('translation');
    assert.equal(await page.locator('.original').first().isVisible(), false);
    assert.equal(await page.locator('.translation').first().isVisible(), true);
    await selectMode('both');
    await selectMode('follow');
    assert.equal(await page.locator('#reader').getAttribute('data-columns'), 'follow');
    await assertFollow();
    await clickText(page.locator('.original .sentence').first());
    await page.waitForFunction(() => document.querySelector('.original .sentence-selected') && document.querySelector('.translation .sentence-linked'));
    assert.equal(await page.locator('.translation .sentence-linked').count(), 1);
    await clickText(page.locator('.translation .sentence').nth(1));
    await page.waitForFunction(() => document.querySelector('.translation .sentence-selected')?.dataset.index === '1' && document.querySelector('.original .sentence-linked')?.dataset.index === '1');
    await page.screenshot({ path: path.join(destination, 'follow-desktop.png'), fullPage: true });
    await clickText(page.locator('.original .sentence').first(), true);
    await page.locator('#word-definitions').waitFor({ state: 'visible' });
    assert.equal(wordQueries.length, 1);
    assert.equal(wordQueries[0].word, 'paused');
    assert.equal(wordQueries[0].offset, 6);
    assert.equal(await page.locator('#word-pos').textContent(), 'v.');
    assert.match(await page.locator('#word-context').textContent(), /当前句.*前 0 句.*不读取后文/);
    const dictionaryFonts = await page.evaluate(() => ['#word-basic', '#word-meaning', '#word-pos', '#word-note'].map(selector => parseFloat(getComputedStyle(document.querySelector(selector)).fontSize)));
    assert.ok(dictionaryFonts[0] >= 14 && dictionaryFonts[1] >= 14 && dictionaryFonts[2] >= 12 && dictionaryFonts[3] >= 12);
    await page.screenshot({ path: path.join(destination, 'word-larger-desktop.png') });
    await page.locator('#close-word').click();
    await selectMode('original');
    assert.equal(await page.locator('.translation').first().isVisible(), false);
    await selectMode('both');
    await selectMode('translation');
    assert.equal(await page.locator('.original').first().isVisible(), false);
    await selectMode('original');
    await selectMode('follow');
    await page.locator('#reader-settings-button').click();
    await page.locator('#en-size').fill('24');
    await page.locator('#zh-weight').selectOption('700');
    await page.locator('.theme-choices [data-theme=green]').click();
    await page.locator('#close-reading-settings').click();
    assert.equal(await page.locator('.original').first().evaluate(node => getComputedStyle(node).fontSize), '24px');
    assert.equal(await page.locator('.translation').first().evaluate(node => getComputedStyle(node).fontSize), '18px');
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.translation .sentence').first().waitFor();
    assert.equal(await page.locator('#reader').getAttribute('data-columns'), 'follow');
    assert.equal(await page.locator('#show-follow').getAttribute('aria-pressed'), 'true');
    for (const theme of ['yellow', 'deep-green', 'dark', 'light', 'green']) {
      await page.locator('#reader-settings-button').click();
      assert.equal(await page.locator('.theme-choices button').count(), 5);
      await page.locator(`.theme-choices [data-theme="${theme}"]`).click();
      await assertTheme(theme);
      assert.equal(await page.locator('.theme-choices button[aria-pressed=true]').count(), 1);
      await page.screenshot({ path: path.join(destination, `theme-${theme}-desktop.png`), fullPage: true });
      await page.locator('#close-reading-settings').click();
      await page.reload({ waitUntil: 'networkidle' });
      await page.locator('.translation .sentence').first().waitFor();
      await assertTheme(theme);
      assert.equal(await page.locator('#reader').getAttribute('data-columns'), 'follow');
    }
    await selectMode('both');
    await page.locator('#next-page').click();
    await page.waitForFunction(() => document.querySelector('#page-input').value === '2' && document.querySelector('.translation .sentence'));
    await selectMode('follow'); await assertFollow();
    await page.locator('#prev-page').click();
    await page.waitForFunction(() => document.querySelector('#page-input').value === '1' && document.querySelector('.translation .sentence'));
    await page.locator('#immersive-button').click();
    await page.waitForFunction(() => document.body.classList.contains('immersive'));
    await assertFollow();
    assert.equal(await page.locator('#show-follow').isVisible(), true);
    await selectMode('both');
    await selectMode('follow');
    assert.equal(await page.locator('#reader').getAttribute('data-columns'), 'follow');
    await page.screenshot({ path: path.join(destination, 'follow-immersive.png'), fullPage: true });
    await page.locator('#exit-immersive').click();
    await page.waitForFunction(() => !document.body.classList.contains('immersive'));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    for (const width of [768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      for (const mode of ['follow', 'original', 'translation', 'both']) {
        await selectMode(mode);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `No overflow at ${width}px in ${mode}`);
        if (mode === 'follow') {
          await assertFollow();
          await page.screenshot({ path: path.join(destination, `follow-${width}.png`), fullPage: true });
        }
        await page.mouse.wheel(0, 750);
        await page.waitForTimeout(100);
        await assertReadingNavigation();
        if (mode === 'follow') {
          const position = await page.evaluate(() => scrollY);
          await page.locator('#reader-settings-button').click();
          await page.locator('#reading-settings').waitFor({ state: 'visible' });
          assert.ok(Math.abs(await page.evaluate(() => scrollY) - position) <= 1, 'Paged reading settings do not jump back when opened after scrolling');
          await page.locator('#close-reading-settings').click();
          await page.screenshot({ path: path.join(destination, `paged-scroll-navigation-${width}.png`) });
        }
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await selectMode('follow');
    for (const theme of ['yellow', 'deep-green', 'dark']) {
      await page.locator('#reader-settings-button').click();
      await page.locator(`.theme-choices [data-theme="${theme}"]`).click();
      await assertTheme(theme);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Theme menu fits on mobile');
      await page.screenshot({ path: path.join(destination, `theme-${theme}-mobile.png`), fullPage: true });
      await page.locator('#close-reading-settings').click();
    }
    // Switching flow on a later source page must not scroll the book information away.
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1200 : 844 });
      for (const mode of ['original', 'translation', 'both', 'follow']) {
        await selectMode(mode);
        await page.locator('#page-input').evaluate(input => { input.value = '7'; input.dispatchEvent(new Event('change')); });
        await page.waitForFunction(() => document.querySelector('#page-input').value === '7');
        await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const header = () => page.evaluate(() => ['#book-title', '#book-author', '#book-translation-progress', '.reading-mode-controls'].map(selector => {
          const node = document.querySelector(selector), bounds = node.getBoundingClientRect();
          return { selector, top: bounds.top, bottom: bounds.bottom, visible: bounds.height > 0 && bounds.top >= 0 && bounds.bottom <= innerHeight };
        }));
        const before = await header();
        assert.ok(before.every(item => item.visible), 'The complete paged reading header is visible before switching');
        await page.locator('#flow-scroll').click();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const after = await header();
        assert.ok(after.every(item => item.visible), `Scroll mode must retain book title, author, full-book progress and six reading choices at ${width}px in ${mode}`);
        for (let i = 0; i < before.length; i++) assert.ok(Math.abs(before[i].top - after[i].top) <= 1, 'Switching flow preserves the complete header layout');
        assert.equal(await page.locator('#page-input').inputValue(), '7');
        for (let round = 0; round < 2; round++) {
          await page.mouse.wheel(0, 1000); await page.waitForTimeout(100);
          if (round === 0) await page.mouse.wheel(0, -10000);
          else await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
          await page.waitForTimeout(350);
          assert.ok(await page.evaluate(() => scrollY <= 1), `Returning to the top must not be pushed back into the text at ${width}px in ${mode}`);
          assert.ok((await header()).every(item => item.visible), 'Book title, author, progress and reading choices return after scrolling down and back up');
        }
        if (mode === 'follow') await page.screenshot({ path: path.join(destination, `complete-scroll-header-${width}.png`) });
        await page.locator('#flow-pages').click();
      }
    }
    // Single columns fill the screen and page turns never skip source paragraphs.
    await page.setViewportSize({ width: 1440, height: 1200 });
    await selectMode('follow');
    await page.mouse.wheel(0, 900); await page.waitForTimeout(100);
    await assertReadingNavigation();
    await page.screenshot({ path: path.join(destination, 'paged-scroll-navigation-desktop.png') });
    await page.locator('#immersive-button').click();
    await page.waitForFunction(() => document.body.classList.contains('immersive'));
    for (const mode of ['original', 'translation', 'follow']) {
      await selectMode(mode);
      await page.locator('#page-input').evaluate(input => { input.value = '1'; input.dispatchEvent(new Event('change')); });
      await page.waitForFunction(() => document.querySelector('#page-input').value === '1');
      const range = await page.locator('.paragraph-row').evaluateAll(rows => ({ first: Number(rows[0].dataset.pid), last: Number(rows.at(-1).dataset.pid), pages: new Set(rows.map(row => row.dataset.page)).size, height: document.querySelector('#paragraphs').getBoundingClientRect().height }));
      assert.ok(range.pages > 1 && range.height >= 900, `${mode} must fill an immersive desktop viewport`);
      await page.locator('#immersive-next').click();
      await page.waitForFunction(last => Number(document.querySelector('.paragraph-row').dataset.pid) === last + 1, range.last);
      const nextStart = Number(await page.locator('.paragraph-row').first().getAttribute('data-pid'));
      await page.locator('#immersive-prev').click();
      assert.equal(Number(await page.locator('.paragraph-row').last().getAttribute('data-pid')), nextStart - 1, 'Backwards groups end exactly where the next group begins');
    }
    await selectMode('follow');
    await page.locator('#flow-scroll').click();
    assert.equal(await page.locator('body').getAttribute('data-flow'), 'scroll');
    assert.equal(await page.locator('#immersive-navigation').isVisible(), false);
    const initialCount = await page.locator('.paragraph-row').count();
    for (let i = 0; i < 7; i++) {
      await page.mouse.wheel(0, 900);
      await page.waitForTimeout(70);
    }
    await page.waitForFunction(() => Number(document.querySelector('#page-input').value) > 2);
    assert.ok(await page.locator('.paragraph-row').count() > initialCount, 'Scrolling appends more content');
    await page.waitForTimeout(450);
    const beforeReload = currentPage;
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.querySelector('body').dataset.flow === 'scroll' && document.querySelector('.translation .sentence'));
    assert.equal(Number(await page.locator('#page-input').inputValue()), beforeReload + 1, 'Scroll reading restores the saved source position');
    await assertReadingNavigation();
    const beforeSettings = await page.evaluate(() => scrollY);
    await page.screenshot({ path: path.join(destination, 'scroll-navigation-desktop.png') });
    await page.locator('#reader-settings-button').click();
    await page.locator('#reading-settings').waitFor({ state: 'visible' });
    const panel = await page.locator('#reading-settings').boundingBox(), bar = await page.locator('.topbar').boundingBox();
    assert.ok(panel.y >= bar.y + bar.height - 1 && panel.y + panel.height <= 1200, 'Scroll settings open under the navigation, within the viewport');
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - beforeSettings) <= 1, 'Opening settings does not jump to the book start');
    await page.screenshot({ path: path.join(destination, 'scroll-settings-desktop.png') });
    await page.locator('#close-reading-settings').click();
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - beforeSettings) <= 1);
    const focus = () => page.locator('.paragraph-row').evaluateAll(rows => Number(rows.find(row => row.getBoundingClientRect().bottom > document.querySelector('.column-labels').getBoundingClientRect().bottom + 12)?.dataset.pid));
    const beforeImmersive = await focus();
    await page.locator('#immersive-button').click();
    await page.waitForFunction(() => document.body.classList.contains('immersive'));
    await page.waitForTimeout(100);
    assert.ok(Math.abs(await focus() - beforeImmersive) <= 1, 'Entering immersive keeps the current scrolling paragraph');
    assert.equal(await page.locator('#flow-scroll').getAttribute('aria-pressed'), 'true');
    const firstBeforeBackscroll = Number(await page.locator('.paragraph-row').first().getAttribute('data-pid'));
    await page.mouse.wheel(0, -3500); await page.waitForTimeout(250);
    assert.ok(Number(await page.locator('.paragraph-row').first().getAttribute('data-pid')) < firstBeforeBackscroll, 'Previous content can be loaded by scrolling upwards');
    for (let i = 0; i < 30; i++) {
      await page.mouse.wheel(0, 1200); await page.waitForTimeout(35);
    }
    await page.waitForFunction(() => document.querySelector('.paragraph-row[data-pid="39"]'));
    const ids = await page.locator('.paragraph-row').evaluateAll(rows => rows.map(row => Number(row.dataset.pid)));
    assert.equal(new Set(ids).size, ids.length, 'No duplicate paragraphs while extending the document');
    for (let i = 1; i < ids.length; i++) assert.equal(ids[i], ids[i - 1] + 1, 'Continuous content has no gaps');
    await clickText(page.locator('.paragraph-row[data-pid="36"] .original .sentence').first());
    await page.waitForFunction(() => document.querySelector('.paragraph-row[data-pid="36"] .translation .sentence-linked'));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.paragraph-row[data-pid="36"]').scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(destination, 'scroll-immersive-mobile.png') });
    await page.locator('#exit-immersive').click();
    assert.equal(await page.locator('body').getAttribute('data-flow'), 'scroll');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await assertReadingNavigation();
    await page.screenshot({ path: path.join(destination, 'scroll-navigation-mobile.png') });
    const mobilePosition = await page.evaluate(() => scrollY);
    await page.locator('#sidebar-toggle').click();
    assert.equal(await page.locator('#toc-panel').isVisible(), true);
    await page.locator('#settings-button').click();
    assert.equal(await page.locator('body').evaluate(node => node.classList.contains('mobile-sidebar')), false);
    const mobilePanel = await page.locator('#reading-settings').boundingBox();
    const mobileTopbar = await page.locator('.topbar').boundingBox();
    assert.ok(mobilePanel.y >= mobileTopbar.y + mobileTopbar.height - 1 && mobilePanel.y + mobilePanel.height <= 844, 'Mobile scroll settings stay below navigation and inside the viewport');
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - mobilePosition) <= 1, 'Sidebar settings keep the current reading position');
    await page.screenshot({ path: path.join(destination, 'scroll-settings-mobile.png') });
    await page.locator('#close-reading-settings').click();
    await clickText(page.locator('.paragraph-row[data-pid="36"] .original .sentence').first(), true);
    await page.locator('#word-definitions').waitFor({ state: 'visible' });
    const wordBounds = await page.locator('#word-popover').boundingBox();
    assert.ok(wordBounds.x >= 0 && wordBounds.y >= 0 && wordBounds.x + wordBounds.width <= 391 && wordBounds.y + wordBounds.height <= 845, 'Larger dictionary fits a phone viewport');
    await page.screenshot({ path: path.join(destination, 'word-larger-mobile.png') });
    await page.locator('#close-word').click();
    assert.equal(await page.locator('#book-exports').isVisible(), false);
    await page.locator('.book-translation-summary').click();
    assert.equal(await page.locator('#book-exports').isVisible(), true);
    assert.ok((await page.locator('#download-bilingual').getAttribute('href')).endsWith('/export?format=bilingual'));
    // Chapter controls work in every content/flow mode, including immersive reading.
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const flow of ['pages', 'scroll']) {
        await page.locator('#flow-' + flow).click();
        for (const mode of ['original', 'translation', 'both', 'follow']) {
          await selectMode(mode);
          await page.locator('#page-input').evaluate(input => { input.value = '8'; input.dispatchEvent(new Event('change')); });
          await page.locator('#next-chapter').click();
          await page.waitForFunction(() => document.querySelector('#page-input').value === '11');
          assert.equal(await page.locator('.paragraph-row').first().getAttribute('data-pid'), '20');
          assert.equal(await page.locator('.toc-item.active > span').first().textContent(), 'The Road Ahead');
          assert.equal(await page.locator('body').getAttribute('data-flow'), flow);
          assert.equal(await page.locator('#reader').getAttribute('data-columns'), mode);
          await page.locator('#prev-chapter').click();
          await page.waitForFunction(() => document.querySelector('#page-input').value === '6');
          assert.equal(await page.locator('.paragraph-row').first().getAttribute('data-pid'), '10');
          await page.locator('#prev-chapter').click();
          await page.waitForFunction(() => document.querySelector('#page-input').value === '1');
          assert.equal(await page.locator('#prev-chapter').isDisabled(), true);
          await page.locator('#page-input').evaluate(input => { input.value = '20'; input.dispatchEvent(new Event('change')); });
          assert.equal(await page.locator('#next-chapter').isDisabled(), true);
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Chapter buttons fit within the compact footer');
        }
      }
    }
    for (const flow of ['pages', 'scroll']) {
      await page.locator('#flow-' + flow).click();
      await selectMode('both');
      await page.locator('#page-input').evaluate(input => { input.value = '16'; input.dispatchEvent(new Event('change')); });
      await page.waitForFunction(() => document.querySelector('.paragraph-row')?.dataset.pid === '30');
      assert.equal(await page.locator('.chapter-break[data-page="15"]').count(), 0, 'An existing EPUB heading is not repeated above the original');
      await page.waitForFunction(() => document.querySelector('.paragraph-row[data-pid="30"] .original').textContent === 'HOME AGAIN');
      assert.equal(await page.locator('.original').filter({ hasText: /^HOME AGAIN$/ }).count(), 1);
    }
    await page.locator('#page-input').evaluate(input => { input.value = '20'; input.dispatchEvent(new Event('change')); });
    await page.locator('#immersive-button').click();
    await page.waitForFunction(() => document.body.classList.contains('immersive'));
    assert.equal(await page.locator('#reading-footer').isVisible(), true);
    await page.locator('#prev-chapter').click();
    await page.waitForFunction(() => document.querySelector('#page-input').value === '11');
    assert.equal(await page.locator('.paragraph-row').first().getAttribute('data-pid'), '20');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const chapterStart = await page.locator('.chapter-break[data-page="10"]').boundingBox();
    assert.ok(chapterStart.y >= 0 && chapterStart.y < 240, 'Chapter navigation locates the chapter opening below the controls');
    await page.screenshot({ path: path.join(destination, 'chapter-navigation-immersive-mobile.png') });
    await page.locator('#exit-immersive').click();

    // Redactions stay visual: exact source text, sentence slots and word offsets survive.
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const flow of ['pages', 'scroll']) {
        await page.locator('#flow-' + flow).click();
        for (const mode of ['original', 'translation', 'both', 'follow']) {
          await selectMode(mode);
          await page.locator('#page-input').evaluate(input => { input.value = '17'; input.dispatchEvent(new Event('change')); });
          const row = page.locator('.paragraph-row[data-pid="32"]');
          await row.locator('.translation .redaction-mark').first().waitFor({ state: 'attached' });
          assert.equal(await row.locator('.original').textContent(), redactedSource.join(''));
          assert.equal(await row.locator('.translation').textContent(), redactedTarget.join(''));
          assert.equal(await row.locator('.original .redaction-mark').count(), 10);
          assert.equal(await row.locator('.translation .redaction-mark').count(), 10);
          assert.ok(await row.locator('.redaction-mark').evaluateAll(marks => marks.every(mark => getComputedStyle(mark).color === 'rgba(0, 0, 0, 0)' && ['rgb(16, 17, 16)', 'rgb(0, 0, 0)'].includes(getComputedStyle(mark).backgroundColor))));
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Long blacked-out placeholders wrap on mobile');
        }
      }
      await selectMode('both');
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({ path: path.join(destination, `redactions-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    const redactedRow = page.locator('.paragraph-row[data-pid="32"]');
    await clickText(redactedRow.locator('.original .sentence').first());
    await page.waitForFunction(() => document.querySelector('.paragraph-row[data-pid="32"] .translation .sentence-linked'));
    const grouped = await redactedRow.locator('.original .sentence').nth(1).locator('.redaction-mark').evaluateAll(marks => marks.map(mark => {
      const r = mark.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, gap: mark.nextSibling?.textContent };
    }));
    for (let i = 1; i < grouped.length; i++) {
      if (Math.abs(grouped[i].top - grouped[i - 1].top) < 1) assert.ok(grouped[i].left > grouped[i - 1].right, 'Censored words have separate strokes and visible gaps');
    }
    const callsBeforeMask = wordQueries.length;
    await redactedRow.locator('.original .redaction-mark').first().dblclick();
    await page.waitForTimeout(250);
    assert.equal(wordQueries.length, callsBeforeMask, 'Double-clicking the ink does not look up placeholder words');
    await redactedRow.locator('.original .sentence').nth(1).scrollIntoViewIfNeeded();
    const wordPoint = await redactedRow.locator('.original .sentence').nth(1).evaluate(sentence => {
      const nodes = document.createTreeWalker(sentence, NodeFilter.SHOW_TEXT);
      while (nodes.nextNode()) {
        const node = nodes.currentNode, start = node.textContent.indexOf('paused');
        if (start < 0) continue;
        const range = document.createRange(); range.setStart(node, start); range.setEnd(node, start + 6);
        const r = range.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }
    });
    await page.mouse.dblclick(wordPoint.x, wordPoint.y);
    await page.locator('#word-definitions').waitFor({ state: 'visible' });
    assert.equal(wordQueries.at(-1).word, 'paused');
    assert.equal(wordQueries.at(-1).offset, Array.from(book.paragraphs[32].text.slice(0, book.paragraphs[32].text.indexOf('paused'))).length);
    await page.locator('#close-word').click();
    await redactedRow.locator('.original .sentence').nth(1).click({ button: 'right' });
    assert.equal(await page.locator('#reproof-sentence').isVisible(), true);
    await page.keyboard.press('Escape');
    for (const theme of ['light', 'green', 'yellow', 'deep-green', 'dark']) {
      await page.locator('#reader-settings-button').click();
      await page.locator(`.theme-choices [data-theme="${theme}"]`).click();
      await page.locator('#close-reading-settings').click();
      assert.equal(await redactedRow.locator('.original .redaction-mark').first().evaluate(mark => getComputedStyle(mark).color), 'rgba(0, 0, 0, 0)');
    }
    await page.locator('#immersive-button').click();
    await page.waitForFunction(() => document.body.classList.contains('immersive'));
    await page.screenshot({ path: path.join(destination, 'redactions-dark-immersive.png') });
    await page.locator('#exit-immersive').click();

    // A change-only select event must update the font, and the actual web face must render.
    await page.setViewportSize({ width: 390, height: 844 });
    await selectMode('translation');
    const cdp = await context.newCDPSession(page);
    await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
    for (const [choice, family] of [['yahei', 'Parallel Sans'], ['kai', 'Parallel Kai'], ['song', 'Parallel Song']]) {
      await page.locator('#reader-settings-button').click();
      await page.locator('#zh-font').evaluate((select, value) => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); }, choice);
      await page.evaluate(family => document.fonts.load(`18px "${family}"`, '汉字阅读'), family);
      await page.evaluate(() => document.fonts.ready);
      const applied = await page.locator('.translation .sentence').first().evaluate(node => getComputedStyle(node).fontFamily);
      assert.ok(applied.includes(family), 'Existing Chinese sentences use the selected font');
      const { root } = await cdp.send('DOM.getDocument');
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: '.translation .sentence' });
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId });
      assert.ok(fonts.some(font => font.isCustomFont && font.glyphCount > 0 && font.familyName.replace(/\s/g, '') === family.replace(/\s/g, '')), 'Chinese glyphs are rendered using the selected web font');
      await page.locator('#close-reading-settings').click();
      await page.screenshot({ path: path.join(destination, `chinese-scroll-font-${choice}.png`) });
    }
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.translation .sentence').first().waitFor();
    assert.equal(await page.locator('#zh-font').inputValue(), 'song');
    assert.ok(await page.locator('.translation .sentence').first().evaluate(node => getComputedStyle(node).fontFamily.includes('Parallel Song')));
    assert.equal(alignments, 0);
    assert.deepEqual(unexpected, []);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, followOrder: true, fourModes: true, oneClickModes: true, alwaysVisibleChoices: true, preferenceRestored: true, themes: ['light', 'green', 'yellow', 'deep-green', 'dark'], themeRestored: true, textContrast: true,
      bidirectionalHighlight: true, wordLookup: true, independentTypography: true, chapterNavigationAllModes: true, chapterStartPosition: true, chapterBoundaries: true, actualChineseWebFonts: true, changeOnlyFontSelection: true, fontPreferenceRestored: true, immersiveChapterFooter: true, immersive: true, adaptivePagination: true, noSkippedParagraphs: true, continuousScroll: true, repeatedReturnToBookHeader: true, wheelAndProgrammaticReturn: true, persistentScrollNavigation: true, persistentPagedNavigation: true, pagedSettingsWithoutPositionJump: true, settingsWithoutPositionJump: true, mobileSidebarAccess: true, iconOnlyFlowChoices: true, scrollProgressRestored: true, immersiveScrollAnchor: true, epubDownloadLinks: true, pageNavigation: true, mobileWidths: [768, 390, 320], errors, screenshots: destination }));
  } catch (error) {
    console.error(await page.evaluate(() => ({ scroll: scrollY, rootScroll: document.documentElement.scrollTop, bodyScroll: document.body.scrollTop, height: innerHeight, rootHeight: document.documentElement.clientHeight, contentHeight: document.documentElement.scrollHeight, fullscreen: !!document.fullscreenElement, flow: document.body.dataset.flow, position: document.querySelector('#page-input').value, rows: document.querySelectorAll('.paragraph-row').length, labels: document.querySelector('.column-labels').getBoundingClientRect().toJSON(), last: document.querySelector('.paragraph-row:last-child').getBoundingClientRect().toJSON() })));
    await page.screenshot({ path: path.join(destination, 'failure.png') });
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
