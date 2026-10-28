/* UI contract for independent book and word services, using synthetic API data. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require(path.join(process.env.PARALLEL_NODE_MODULES,'playwright'));
(async()=>{
 const root=path.resolve('public');
 const server=http.createServer((req,res)=>{
  const file=path.resolve(root,'.'+new URL(req.url,'http://localhost').pathname.replace(/^\/$/,'/index.html'));
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({channel:'msedge',headless:true,args:['--no-proxy-server']});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900}}),errors=[],unexpected=[],wordCalls=[];
  let settings={translationProvider:'llm',wordProvider:'microsoft',baseUrl:'https://book.example.invalid/v1',model:'book-model',
   reviewEnabled:false,reviewBaseUrl:'',reviewModel:'',apiKey:'',hasApiKey:true,wordApiKey:'',targetLanguage:'简体中文',profile:'llm-fixture'};
  const book={id:'service-ui-fixture',title:'Independent Services',author:'Test Author',language:'en',currentPage:0,startPage:0,
   chapters:[{id:0,title:'Morning',startPage:0,endPage:0,startParagraph:0}],
   paragraphs:[{id:0,chapter:0,kind:'paragraph',text:'The traveler sat on the bank.'}],
   pages:[{id:0,chapter:0,paragraphs:[0],content:[{type:'paragraph',id:0},{type:'image',id:0}]}],
   images:{demo:{width:400,height:200}},illustrations:[{id:0,asset:'demo',afterParagraph:0}]};
  const task=()=>({state:'completed',completed:1,total:1,drafted:1,percent:100,failed:0,provider:settings.translationProvider,
   model:settings.translationProvider==='microsoft'?'Azure Translate':settings.model,reviewEnabled:false,targetLanguage:'简体中文',profile:settings.profile,totalChapters:1,completedChapters:1,activeChapters:[],parallelChapters:4});
  await context.route('**/api/**',async route=>{
   const request=route.request(),ep=new URL(request.url()).pathname;let value;
   if(ep==='/api/settings'){
    if(request.method()==='POST'){
     const data=request.postDataJSON();settings={...settings,...data,apiKey:'',wordApiKey:'',hasWordApiKey:!!data.wordApiKey||settings.hasWordApiKey,profile:data.translationProvider+'-fixture'};value={ok:true};
    }else value=settings;
   }else if(ep==='/api/model/test')value={ok:true,provider:settings.translationProvider};
   else if(ep==='/api/books')value=[{...book,pageCount:1}];
   else if(ep==='/api/books/'+book.id)value=book;
   else if(ep==='/api/sample')value={available:false};
   else if(ep==='/api/library-progress')value={profile:settings.profile,books:{[book.id]:task()}};
   else if(ep.endsWith('/translation-sources'))value={book:book.id,configurationProfile:settings.profile,selectedProfile:settings.profile,selectionRequired:false,completed:1,total:1,sources:[]};
   else if(ep.endsWith('/reproofread'))value={jobs:[]};
   else if(ep.endsWith('/pretranslate'))value=task();
   else if(ep.endsWith('/window'))value={profile:settings.profile};
   else if(ep.endsWith('/progress'))value={ok:true};
   else if(ep.endsWith('/status'))value={profile:settings.profile,pages:[],bookTranslation:task(),items:{0:{state:'ready',text:'旅人坐在河岸上。',sourceSentences:[book.paragraphs[0].text],targetSentences:['旅人坐在河岸上。'],alignment:[{source:[0],target:[0]}]}}};
   else if(ep.endsWith('/word')){
    wordCalls.push({book:settings.translationProvider,word:settings.wordProvider,model:settings.wordModel});
    value={lemma:'bank',partOfSpeech:'n.',basicMeaning:'银行',contextMeaning:'河岸',note:'',provider:settings.wordProvider,contextKind:'term',contextWindow:{before:0,after:0}};
   }else if(ep.endsWith('/images/demo'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="white"/></svg>'});
   else if(ep.endsWith('/image-translations/demo'))value={state:settings.translationProvider==='microsoft'?'disabled':'idle',result:null};
   else{unexpected.push(ep);value={};}
   await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.waitForFunction(()=>document.querySelector('.translation')?.textContent.includes('旅人'));
  for(const bookProvider of ['llm','microsoft'])for(const wordProvider of ['llm','microsoft']){
   await page.locator('#model-badge').click();await page.locator('#settings-dialog').waitFor({state:'visible'});
   assert.deepEqual(await page.locator('#translation-provider option').allTextContents(),['LLM','Azure Translate']);
   assert.deepEqual(await page.locator('#word-provider option').allTextContents(),['Azure Translate','LLM']);
   assert.equal(await page.locator('[name^=azure]').count(),0);
   assert.doesNotMatch(await page.locator('#settings-dialog').innerText(),/免配置/);
   await page.locator('#translation-provider').selectOption(bookProvider);await page.locator('#word-provider').selectOption(wordProvider);
   assert.equal(await page.locator('[name=baseUrl]').isDisabled(),bookProvider!=='llm');
   assert.equal(await page.locator('[name=wordBaseUrl]').isDisabled(),wordProvider!=='llm');
   if(wordProvider==='llm'){
    await page.locator('[name=wordBaseUrl]').fill('');await page.locator('[name=wordModel]').fill('');
    assert.equal(await page.locator('#settings-form').evaluate(form=>form.checkValidity()),false);
    await page.locator('[name=wordBaseUrl]').fill('https://words.example.invalid/v1');await page.locator('[name=wordModel]').fill('word-model');
    await page.locator('[name=wordApiKey]').fill('synthetic-word-key');
   }
   assert.equal(await page.locator('#settings-form').evaluate(form=>form.checkValidity()),true);
   await page.locator('#settings-form button[type=submit]').click();await page.locator('#settings-dialog').waitFor({state:'hidden'});
   await page.waitForFunction(()=>document.querySelector('.translation')?.textContent.includes('旅人'));
   assert.equal(settings.baseUrl,'https://book.example.invalid/v1');assert.equal(settings.model,'book-model');
   assert.equal(settings.wordProvider,wordProvider);
   if(bookProvider==='microsoft')assert.match(await page.locator('#model-badge').innerText(),/Azure Translate/);
   await page.locator('.original .sentence').first().scrollIntoViewIfNeeded();
   const point=await page.evaluate(()=>{
    const walker=document.createTreeWalker(document.querySelector('.original .sentence'),NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){
     const node=walker.currentNode,start=node.textContent.indexOf('bank');if(start<0)continue;
     const range=document.createRange();range.setStart(node,start);range.setEnd(node,start+4);const r=range.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};
    }
   });
   await page.mouse.dblclick(point.x,point.y);await page.waitForFunction(()=>!document.querySelector('#word-definitions').hidden);
   assert.equal(await page.locator('#word-context').innerText(),'由 '+(wordProvider==='llm'?'LLM':'Azure Translate')+' 提供');
   assert.equal(await page.locator('#word-meaning').innerText(),'河岸');await page.locator('#close-word').click();
  }
  assert.equal(wordCalls.length,4);assert.deepEqual(wordCalls.map(v=>v.book+':'+v.word),['llm:llm','llm:microsoft','microsoft:llm','microsoft:microsoft']);
  await page.setViewportSize({width:390,height:844});await page.locator('#model-badge').click();await page.locator('#settings-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#settings-dialog').evaluate(el=>el.getBoundingClientRect().width<=innerWidth),true);
  assert.equal(await page.locator('[name=wordApiKey]').inputValue(),'');
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  console.log('Two service menus, no Azure cloud fields, four independent combinations, word-provider credit and mobile settings: PASS');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
