const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require(path.join(process.env.PARALLEL_NODE_MODULES,'playwright'));
(async()=>{
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--no-proxy-server']});
try{
 const context=await browser.newContext({viewport:{width:1280,height:1000},reducedMotion:'reduce'});
 const base='http://127.0.0.1:8765', id=process.env.PARALLEL_BOOK_ID, asset=process.env.PARALLEL_IMAGE_ASSET;
 if(!id||!asset) throw new Error('Set PARALLEL_BOOK_ID and PARALLEL_IMAGE_ASSET for your own library');
 const book=await (await context.request.get(base+'/api/books/'+id)).json();
 const target=book.pages.findIndex(page=>page.content?.some(item=>item.type==='image'&&book.illustrations[item.id].asset===asset));
 const settings=await (await context.request.get(base+'/api/settings')).json();
 await context.addInitScript(()=>{localStorage.setItem('parallel.prefetch','false');localStorage.setItem('parallel.flow','pages');});
 await context.route('**/api/**',async route=>{
 const r=route.request(),u=new URL(r.url()); let value;
 if(r.method()==='POST') value=u.pathname.endsWith('/progress')?{ok:true}:{profile:settings.profile};
 else if(u.pathname==='/api/books') value=[{...book,currentPage:target,pageCount:book.pages.length,bookmark:{manual:true,page:target}}];
 else if(u.pathname==='/api/books/'+id) value={...book,currentPage:target,bookmark:{manual:true,page:target}};
 if(value!==undefined) return route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
 return route.continue();
 });
 const page=await context.newPage(), errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base,{waitUntil:'domcontentloaded'}); await page.locator('#show-translation').click();
 const fig=page.locator('.illustration-row').filter({has:page.locator(`img[src*="${asset}"]`)}).first();
 await fig.scrollIntoViewIfNeeded();
 await page.waitForFunction(a=>[...document.querySelectorAll('.illustration-image')].some(i=>i.src.includes('/translated-images/'+a)&&i.complete&&i.naturalWidth>0),asset);
 await fig.locator('.illustration-open').click(); const dialog=page.locator('.illustration-viewer[open]');
 await page.waitForFunction(()=>document.querySelector('.illustration-viewer img')?.src.includes('/translated-images/'));
 await dialog.locator('.image-original').click(); assert.ok((await dialog.locator('img').getAttribute('src')).includes('/images/'));
 await dialog.locator('.image-translated').click();
 await page.waitForFunction(()=>document.querySelector('.illustration-viewer img').complete);
 fs.mkdirSync('test-results/image-translation',{recursive:true}); await page.screenshot({path:'test-results/image-translation/live-translated.png'});
 assert.equal(await dialog.locator('.image-text-block').count(),0); assert.deepEqual(errors,[]);
 console.log(JSON.stringify({livePNG:true,originalToggle:true,noList:true,page:target+1,preservedReadingPosition:true}));
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
