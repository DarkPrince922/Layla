const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
 const browser = await chromium.launch({executablePath:process.env.LAYLA_BROWSER_EXECUTABLE || undefined,headless:true,args:JSON.parse(process.env.LAYLA_BROWSER_ARGS || '["--no-sandbox"]')});
 const context = await browser.newContext({viewport:{width:1440,height:1000}}), page = await context.newPage();
 await page.addInitScript(()=>localStorage.setItem('layla.motion','false'));
 page.setDefaultTimeout(20000);
 const base='http://127.0.0.1:3310', errors=[];page.on('pageerror',e=>errors.push(e.message));
 try {
  assert.equal((await context.request.post(base+'/api/auth/register',{data:{email:`workbench-${Date.now()}@example.com`,password:'qa-only-local-password'}})).status(),201);
  await context.request.post(base+'/api/providers',{data:{name:'QA',kind:'openai_compatible',base_url:'http://127.0.0.1:8219/test-provider',default_model:'qa-model',active:true}});
  await context.request.put(base+'/api/engagements/authorization/defaults',{data:{company:'QA',responsible_name:'QA Operator',employee_identifier:'QA-local'}});
  const engagement=await(await context.request.post(base+'/api/engagements',{data:{target:'fixture.example.com'}})).json();
  const doc=await(await context.request.get(base+`/api/engagements/${engagement.id}/authorization`)).json();
  assert.equal((await context.request.post(base+`/api/engagements/${engagement.id}/confirm`,{data:{content_sha256:doc.content_sha256}})).status(),200);
  const response=await context.request.post(base+`/api/engagements/${engagement.id}/agent/chat`,{data:{content:'QA workbench',model:'qa-model',mode:'auto'}});
  assert.equal(response.status(),202,await response.text());const run=await response.json();
  await page.goto(base+`/pentest?engagement=${engagement.id}&run=${run.id}`);
  await page.getByText('QA workbench finished',{exact:true}).first().waitFor();
  await page.getByLabel('Настройки пентеста',{exact:true}).click();
  const wb=page.getByLabel('Память и работа агента');assert.equal(await wb.evaluate(e=>e.open),false);
  await wb.locator(':scope > summary').click();
  const memory=wb.getByLabel('Постоянная память engagement');await memory.locator(':scope > summary').click();
  await memory.getByText('Fixture component',{exact:true}).click();await memory.getByText('Synthetic version 1.2',{exact:true}).waitFor();
  await memory.getByLabel('Поиск по памяти engagement').fill('list_files');
  const journal=memory.locator('details').filter({has:page.getByText('list_files: .',{exact:true})}).first();await journal.locator(':scope > summary').click();
  const proof=journal.locator('details').first();await proof.locator(':scope > summary').click();await proof.getByText(/"request"/).waitFor();
  const coverage=wb.getByLabel('Покрытие проверок');await coverage.locator(':scope > summary').click();
  await coverage.getByText('Authenticated checks',{exact:true}).click();await coverage.getByText('No test credentials',{exact:true}).waitFor();
  assert.equal(await coverage.getByRole('progressbar').getAttribute('value'),'0');
  const queue=wb.getByLabel('Очередь задач engagement');await queue.locator(':scope > summary').click();assert.equal(await queue.locator(':scope > div > details').count(),4);
  assert.equal((await(await context.request.get(base+`/api/engagements/${engagement.id}/workbench`)).json()).tasks.filter(t=>t.status==='done').length,4);
  await page.screenshot({path:process.env.LAYLA_QA_SCREENSHOTS ? process.env.LAYLA_QA_SCREENSHOTS+'/workbench-desktop.png':'workbench-desktop.png',animations:'disabled'});
  for(const width of [390,320]){
   await page.setViewportSize({width,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:process.env.LAYLA_QA_SCREENSHOTS ? process.env.LAYLA_QA_SCREENSHOTS+`/workbench-${width}.png`:`workbench-${width}.png`,animations:'disabled'});
  }
  const tools=wb.getByLabel('Инструменты attackbox');await tools.locator(':scope > summary').click();await tools.getByRole('button',{name:'Проверить инструменты в чате'}).click();
  await page.getByLabel('Сообщение пентест-агенту').waitFor();assert.match(await page.getByLabel('Сообщение пентест-агенту').inputValue(),/inspect_tools/);
  assert.equal(await page.getByRole('region',{name:'Чат пентест-агента'}).count(),1);assert.deepEqual(errors,[]);
  console.log('PASS: durable memory, evidence expansion, coverage reasons, four queued tasks, default collapsed panels, compose action and 320/390px layouts');
 } catch(e){console.error(e);console.error((await page.locator('body').innerText()).slice(-4000));throw e;} finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
