const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.LAYLA_BROWSER_EXECUTABLE || undefined, headless:true, args:JSON.parse(process.env.LAYLA_BROWSER_ARGS || '["--no-sandbox"]')});
 const context = await browser.newContext({viewport:{width:1280,height:1000}});
 const page = await context.newPage();
 const errors=[]; page.on('pageerror', e=>errors.push(e.message));
 page.setDefaultTimeout(30000);
 const base='http://127.0.0.1:3310';
 await page.addInitScript(()=>{
  localStorage.setItem('layla.motion','false');
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__copied=value;}}});
 });
 try {
  assert.equal((await context.request.post(base+'/api/auth/register',{data:{email:`rich-${Date.now()}@example.com`,password:'qa-only-local-password'}})).status(),201);
  await context.request.post(base+'/api/providers',{data:{name:'QA rich',kind:'openai_compatible',base_url:'http://127.0.0.1:8219/test-provider',default_model:'qa-model',active:true}});
  const e=await(await context.request.post(base+'/api/engagements',{data:{target:'rich.example.com'}})).json();
  const run=await(await context.request.post(base+`/api/engagements/${e.id}/agent/chat`,{data:{content:'QA rich response',model:'qa-model',mode:'plan'}})).json();
  await page.goto(base+`/pentest?engagement=${e.id}&run=${run.id}`);
  const response=page.locator('.chat-model').filter({has:page.getByRole('heading',{name:'Результаты проверки'})});
  await response.getByRole('table').waitFor();
  assert.equal(await response.locator('th').count(),3);
  assert.equal(await response.locator('tbody tr').count(),2);
  assert.equal(await response.locator('blockquote').count(),1);
  assert.equal(await response.locator('input[type=checkbox]').count(),2);
  assert.equal(await response.locator('script, img').count(),0);
  assert.equal(await page.evaluate(()=>window.__unsafe),undefined);
  assert.equal(await response.locator('a[href^="javascript:"]').count(),0);
  await response.getByRole('button',{name:'Копировать таблицу',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.__copied),'Проверка\tСтатус\tПримечание\nHTTP\tГотово\tБез ошибок\nTLS\tПроверено\tАктуальный сертификат');
  await response.getByRole('button',{name:'Копировать блок',exact:true}).click();
  const code=await response.locator('pre code').textContent();
  assert.equal(await page.evaluate(()=>window.__copied),code);
  await response.getByRole('button',{name:'Копировать ответ',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.__copied),fs.readFileSync(path.join(__dirname,'rich-response.md'),'utf8'));
  // Both Clipboard API and legacy copy may be denied; manual copy must remain available.
  await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw new Error('denied');};document.execCommand=()=>false;});
  await response.getByRole('button',{name:'Копировать блок',exact:true}).click();
  assert.equal(await response.getByLabel('Текст для копирования').inputValue(),code);
  for(const width of [390,320]) {
   await page.setViewportSize({width,height:900});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),`overflow at ${width}`);
   assert.ok(await response.locator('pre').evaluate(el=>el.scrollWidth>el.clientWidth),'long code should scroll');
  }
  if(process.env.LAYLA_QA_SCREENSHOTS){fs.mkdirSync(process.env.LAYLA_QA_SCREENSHOTS,{recursive:true});await response.screenshot({path:path.join(process.env.LAYLA_QA_SCREENSHOTS,'rich-response-mobile.png')});}
  assert.deepEqual(errors,[]);
  console.log('Rich responses: tables, code, clipboard, fallback, XSS and mobile checks passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
