const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
 const browser = await chromium.launch({executablePath:process.env.LAYLA_BROWSER_EXECUTABLE || undefined, headless:true, args:JSON.parse(process.env.LAYLA_BROWSER_ARGS || '["--no-sandbox"]')});
 const context = await browser.newContext({viewport:{width:1440,height:1000}});
 const page = await context.newPage();
 await page.addInitScript(()=>localStorage.setItem('layla.motion','false'));
 page.setDefaultTimeout(15000);
 const base = 'http://127.0.0.1:3310';
 const errors = []; page.on('pageerror', e=>errors.push(e.message));
 try {
  assert.equal((await context.request.post(base+'/api/auth/register',{data:{email:`isolation-${Date.now()}@example.com`,password:'qa-only-local-password'}})).status(),201);
  await context.request.post(base+'/api/providers',{data:{name:'QA isolation',kind:'openai_compatible',base_url:'http://127.0.0.1:8219/test-provider',default_model:'qa-model',active:true}});
  await context.request.put(base+'/api/engagements/authorization/defaults',{data:{company:'QA company',responsible_name:'QA Operator',employee_identifier:'QA-local'}});
  const old = await (await context.request.post(base+'/api/engagements',{data:{target:'old.example.com'}})).json();
  const confirm = async eid => {
   const doc = await (await context.request.get(base+`/api/engagements/${eid}/authorization`)).json();
   const accepted = await context.request.post(base+`/api/engagements/${eid}/confirm`,{data:{content_sha256:doc.content_sha256}});
   assert.equal(accepted.status(),200,await accepted.text());
  };
  await confirm(old.id);
  const initial = await (await context.request.post(base+`/api/engagements/${old.id}/agent/chat`,{data:{content:'QA isolation old chat',model:'qa-model',mode:'auto'}})).json();
  await page.goto(base+`/pentest?engagement=${old.id}&run=${initial.id}`);
  await page.getByText('QA isolation old chat',{exact:true}).last().waitFor();
  // Hold the list refresh: selection must remain on the object returned by POST.
  let delayList = true;
  await page.route('**/api/engagements',async route=>{
   if(route.request().method()==='GET' && delayList) { const response=await route.fetch();await pause(1200);await route.fulfill({response}); }
   else await route.continue();
  });
  await page.getByPlaceholder('цель (домен/IP)',{exact:true}).fill('new.example.com');
  const createdPromise = page.waitForResponse(r=>r.url().endsWith('/api/engagements')&&r.request().method()==='POST');
  await page.getByLabel('Создать engagement',{exact:true}).click();
  const created = await (await createdPromise).json();
  await page.getByRole('heading',{name:'new.example.com',exact:true}).waitFor();
  await pause(1600); delayList=false;
  assert.equal(await page.getByRole('heading',{name:'new.example.com',exact:true}).count(),1,'new engagement reverted to old');
  assert.equal(await page.getByRole('region',{name:'Чат пентест-агента'}).count(),1,'multiple chat panels remained mounted');
  assert.equal(await page.getByText('QA isolation old chat',{exact:true}).count(),0,'old dialogue leaked');
  await page.waitForFunction(()=>document.querySelector('select[aria-label="История пентест-чатов"]')?.disabled===false);
  assert.equal(await page.getByLabel('История пентест-чатов').locator('option').count(),1);
  assert.equal(new URL(page.url()).searchParams.get('engagement'),created.id);
  assert.equal(new URL(page.url()).searchParams.get('run'),null);
  await confirm(created.id);
  await page.waitForFunction(()=>document.querySelector('select[aria-label="Модель пентест-агента"]')?.value);
  // Delay all history refreshes until after follow-up: POST must seed the chat itself.
  let releasePoll;
  const pollGate=new Promise(resolve=>{releasePoll=resolve;});
  await page.route(`**/api/engagements/${created.id}/agent/runs`,async route=>{await pollGate;await route.continue().catch(()=>{});});
  let posts=[];
  await page.route('**/agent/chat',async route=>{
   posts.push({url:route.request().url(),body:route.request().postDataJSON()});
   const response=await route.fetch(); const data=await response.json();
   // Return a completed fixture to expose the continuation-before-poll race.
   data.workers=data.workers.map(w=>({...w,status:'done'}));
   await pause(400);await route.fulfill({response,json:data});
  });
  await page.getByLabel('Сообщение пентест-агенту').fill('QA isolation new chat');
  await page.getByLabel('Сообщение пентест-агенту').evaluate(el=>{
   for(let i=0;i<4;i++) el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  });
  await page.waitForFunction(()=>document.querySelector('select[aria-label="История пентест-чатов"]')?.value);
  assert.equal(posts.length,1,'repeated Enter created duplicate chats');
  const newRun=await page.getByLabel('История пентест-чатов').inputValue();
  assert.equal(await page.getByLabel('История пентест-чатов').locator('option').count(),2,'POST chat absent from cache');
  // Wait for the real worker to finish without allowing the UI history poll to respond.
  for(let i=0;i<40;i++) {
   const state=await(await context.request.get(base+'/api/agent/runs/'+newRun)).json();
   if(state.workers.every(w=>!['queued','running','awaiting_approval'].includes(w.status))) break;
   await pause(100);
  }
  await page.getByLabel('Сообщение пентест-агенту').fill('QA isolation follow-up');
  await page.getByLabel('Отправить пентест-агенту').click();
  await page.getByText('QA isolation follow-up',{exact:true}).waitFor();
  assert.equal(posts.length,2);
  assert.equal(posts[1].body.run_id,newRun,'follow-up silently created another chat');
  assert.equal(await page.getByLabel('История пентест-чатов').inputValue(),newRun);
  releasePoll(); await page.unroute(`**/api/engagements/${created.id}/agent/runs`);
  const freshRuns=await(await context.request.get(base+`/api/engagements/${created.id}/agent/runs`)).json();
  assert.equal(freshRuns.length,1);
  assert.equal((await(await context.request.get(base+`/api/engagements/${old.id}/agent/runs`)).json()).length,1);
  // Navigate while POST is pending: the late response must not rewrite the selected engagement URL.
  await page.getByLabel('Новый пентест-чат').click();
  await page.getByLabel('Сообщение пентест-агенту').fill('QA isolation background chat');
  const late=page.waitForResponse(r=>r.url().includes(`/engagements/${created.id}/agent/chat`));
  await page.getByLabel('Отправить пентест-агенту').click();
  await page.getByRole('button',{name:'old.example.com',exact:true}).click();
  await late;await pause(800);
  await page.getByText('QA isolation old chat',{exact:true}).last().waitFor();
  assert.equal(new URL(page.url()).searchParams.get('engagement'),old.id,'late callback changed engagement URL');
  assert.equal(await page.getByLabel('История пентест-чатов').inputValue(),initial.id);
  await page.reload();await page.getByText('QA isolation old chat',{exact:true}).last().waitFor();
  await page.getByRole('button',{name:'new.example.com',exact:true}).click();
  await page.getByText('QA isolation background chat',{exact:true}).last().waitFor();
  assert.equal(await page.getByText('QA isolation old chat',{exact:true}).count(),0);
  assert.equal(await page.getByLabel('История пентест-чатов').locator('option').count(),3);
  await page.setViewportSize({width:320,height:844});
  await page.reload();await page.getByText('QA isolation background chat',{exact:true}).last().waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);
  console.log('PASS: delayed engagement refresh, isolated history, rapid Enter deduplication, follow-up before poll, late response after switching, reload, 320px.');
 } catch (error) { console.error(error); throw error; } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
