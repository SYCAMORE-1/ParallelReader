/* Capture the real UI using original demo prose; all API traffic is isolated. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(path.join(process.env.PARALLEL_NODE_MODULES,'playwright'));
(async()=>{
 const http=require('node:http'),publicRoot=path.resolve('public');
 const staticServer=http.createServer((req,res)=>{
  const filename=path.resolve(publicRoot,'.'+decodeURIComponent(new URL(req.url,'http://localhost').pathname.replace(/^\/$/,'/index.html')));
  if(!filename.startsWith(publicRoot+path.sep)||!fs.existsSync(filename)||!fs.statSync(filename).isFile()){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(filename)]||'application/octet-stream');res.end(fs.readFileSync(filename));
 });
 await new Promise(resolve=>staticServer.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+staticServer.address().port,profile='public-demo';
 const prose=[
 ['Morning light spread across the lake, turning the water a soft shade of gold. The town was only beginning to wake. From the open window of the little café, Anna could hear the first boat moving away from the shore.', '晨光洒过湖面，把水色染成了柔和的金黄。小镇刚刚醒来。咖啡馆的窗子敞着，Anna坐在窗边，听见第一条小船缓缓离岸的声音。'],
 ['She opened her notebook and found the page she had left unfinished the night before. There was no need to hurry now. For the first time in weeks, the whole morning belonged to her, with nothing to do but read, think, and watch the light change.', '她翻开笔记本，找到昨晚还没写完的那一页。现在不必着急了。几周以来，她头一回拥有一个完全属于自己的早晨，可以读书、思考，看光影慢慢变换。'],
 ['A cup of tea arrived beside her book. Steam curled into the cool air, carrying the scent of lemon. Outside, a cyclist stopped at the bridge and leaned over the railing, as if the quiet water had something worth listening to.', '一杯茶被轻轻放在书旁。热气袅袅升入微凉的空气，带着柠檬的清香。窗外，一位骑车人在桥边停下，俯身望着栏杆外的湖水，仿佛那片宁静里藏着什么值得倾听的声音。'],
 ['Anna smiled and turned the page. Some journeys began with a train ticket or a carefully drawn map. This one began with an open book, a warm cup, and the simple decision to stay a little longer.', 'Anna微笑着，翻过一页。有些旅程从一张车票或一幅精心绘制的地图开始。而这一段旅程，始于一本展开的书、一杯热茶，以及再多停留片刻的简单念头。']
 ];
 const titles=['Morning Light','The Lakeside Path','A Letter from Home','Across the Bridge','The Open Window','A New Beginning'];
 const book={id:'public-demo',title:'A Quiet Morning',author:'Parallel Reader · Reading Sample',language:'en',cover:null,currentPage:0,startPage:0,images:{},illustrations:[],
  chapters:titles.map((title,id)=>({id,title,startPage:id*2,endPage:id*2+1,startParagraph:id*6})),
  pages:Array.from({length:12},(_,id)=>({id,chapter:Math.floor(id/2),paragraphs:[0,1,2].map(n=>id*3+n)})),
  paragraphs:Array.from({length:36},(_,id)=>({id,chapter:Math.floor(id/6),kind:'paragraph',text:prose[id%4][0]}))};
 const items=Object.fromEntries(book.paragraphs.map(p=>[p.id,{state:'ready',text:prose[p.id%4][1],sourceSentences:[p.text],targetSentences:[prose[p.id%4][1]],alignment:[{source:[0],target:[0]}],alignmentMethod:'translation-slots'}]));
 const task={state:'completed',completed:36,total:36,drafted:36,failed:0,percent:100,model:'Translation',reviewEnabled:true,reviewModel:'Proofreading',targetLanguage:'简体中文',profile,completedChapters:6,totalChapters:6,parallelChapters:4,activeChapters:[]};
 const browser=await chromium.launch({channel:'msedge',headless:true,args:['--no-proxy-server']});
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1000},deviceScaleFactor:1,reducedMotion:'reduce'});
  await context.addInitScript(()=>{localStorage.setItem('parallel.prefetch','false');localStorage.setItem('parallel.theme','green');localStorage.setItem('parallel.flow','pages');localStorage.setItem('parallel.columns','both');localStorage.setItem('parallel.appearance',JSON.stringify({en:{font:'georgia',size:22,weight:400,line:1.8},zh:{font:'song',size:22,weight:400,line:1.8}}));});
  const unexpected=[],errors=[];
  await context.route('**/api/**',async route=>{
   const request=route.request(),u=new URL(request.url()),ep=u.pathname;let value;
   if(ep==='/api/settings')value={model:'Translation',reviewEnabled:true,reviewModel:'Proofreading',targetLanguage:'简体中文',profile};
   else if(ep==='/api/books')value=[{...book,pageCount:12}];
   else if(ep==='/api/sample')value={available:false};
   else if(ep==='/api/library-progress')value={profile,books:{[book.id]:task}};
   else if(ep==='/api/books/'+book.id)value=book;
   else if(ep.endsWith('/reproofread'))value={jobs:[]};
   else if(ep.endsWith('/pretranslate'))value=task;
   else if(ep.endsWith('/window'))value={profile};
   else if(ep.endsWith('/progress'))value={ok:true};
   else if(ep.endsWith('/status'))value={profile,items,pages:book.pages.map(p=>({page:p.id,ready:3,total:3})),bookTranslation:task};
   else{unexpected.push(ep);value={};}
   await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.locator('#show-both').click();await page.locator('#tab-toc').click();
  await page.waitForFunction(()=>document.querySelectorAll('.translation:not(.placeholder)').length>=3);
  await page.evaluate(async()=>{await document.fonts.ready;await document.fonts.load('22px "Parallel Song"');window.scrollTo(0,0);});
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  const visible=await page.locator('body').innerText();assert.ok(!/https?:\/\/|api.?key|access.?token/i.test(visible));
  fs.mkdirSync('docs/screenshots',{recursive:true});
  await page.screenshot({path:'docs/screenshots/reader.png'});
  console.log(JSON.stringify({file:path.resolve('docs/screenshots/reader.png'),realUI:true,demoContent:true,productionRequests:0,errors}));
 }finally{await browser.close();await new Promise(resolve=>staticServer.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
