import { spawn, type ChildProcess } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'

let backend: ChildProcess
const base = 'http://127.0.0.1:18092'
function link(extra = '') {
  return `${base}/#/action?trade=1&request_id=${randomUUID()}&symbol=NVDA%3AUS&side=BUY&quantity=10&order_type=LIMIT&limit_price=180&currency=USD${extra}`
}
test.beforeAll(async () => {
  backend = spawn(process.env.WP_TEST_PYTHON ?? 'python3', [fileURLToPath(new URL('../fixtures/trade_link_backend.py', import.meta.url)), '18092'])
  backend.stderr?.pipe(process.stderr)
  await new Promise<void>((resolve,reject) => {
    const timer=setTimeout(() => reject(new Error('Isolated backend did not start')),20000)
    backend.once('error',reject)
    backend.once('exit',code => { clearTimeout(timer); reject(new Error(`Backend exit ${code}`)) })
    backend.stderr?.on('data',chunk => { if (String(chunk).includes('Uvicorn running')) { clearTimeout(timer); resolve() } })
  })
})
test.afterAll(() => backend?.kill('SIGTERM'))

test('link → existing editable draft → existing final confirmation → Mock order/history; no replay', async ({ page,request }) => {
  const url=link()
  let submissions=0
  page.on('request',r => { if (r.url().endsWith('/place_order')) submissions++ })
  await page.goto(url)
  const dialog=page.getByRole('dialog')
  await expect(dialog.getByLabel('标的及市场')).toHaveValue('NVDA:US')
  await expect(dialog.getByLabel('买卖方向')).toHaveValue('BUY')
  await expect(dialog.getByLabel('数量（股）')).toHaveValue('10')
  await expect(dialog.getByLabel('限价',{ exact:true })).toHaveValue('180')
  await expect(dialog.getByLabel('交易币种')).toHaveValue('USD')
  expect((await (await request.get(`${base}/api/action/orders`)).json()).items).toHaveLength(0)
  expect((await (await request.get(`${base}/api/action/drafts`)).json()).items).toHaveLength(0)
  await dialog.getByLabel('数量（股）').fill('2')
  await dialog.getByRole('button',{name:'加入投资行动'}).click()
  const final=page.getByTestId('confirm-order-dialog')
  await expect(final).toContainText('2 股')
  await expect(final).toContainText('Mock 模拟交易')
  await expect(final.getByTestId('submit-btn')).toBeDisabled()
  expect(submissions).toBe(0)
  const riskInput = final.getByTestId('risk-confirm-input')
  await expect(riskInput).toBeVisible()
  await riskInput.fill('我已知晓风险并坚持下单')
  await final.getByRole('checkbox').check()
  await final.getByTestId('submit-btn').click()
  await expect(final).not.toBeVisible()
  expect(submissions).toBe(1)
  await expect(page.getByText('已成交',{exact:true}).first()).toBeVisible({timeout:15000})
  await page.getByRole('button',{name:'行动记录',exact:true}).click()
  await expect(page.getByText(/NVDA/).first()).toBeVisible()
  await page.goto(url)
  await page.getByRole('button',{name:'加入投资行动'}).click()
  await expect(page.getByRole('alert')).toContainText('已有订单')
  expect(submissions).toBe(1)
  expect((await (await request.get(`${base}/api/action/orders`)).json()).items).toHaveLength(1)
})

test('invalid and duplicate parameters are explicit, no implicit defaults', async ({page}) => {
  await page.goto(link())
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.goto(link('&quantity=2'))
  await expect(page.getByRole('alert')).toContainText('参数重复')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.goto(link().replace('currency=USD',''))
  await expect(page.getByRole('alert')).toContainText('currency')
  await expect(page.getByRole('dialog')).not.toBeVisible()
})

test('mobile viewport: prefill and final confirmation fit the screen without submitting',async ({page}) => {
  await page.setViewportSize({width:390,height:844})
  await page.goto(link().replace('side=BUY','side=SELL'))
  const draft=page.getByRole('dialog')
  await expect(draft.getByLabel('买卖方向')).toHaveValue('SELL')
  const bounds=await draft.boundingBox()
  expect(bounds!.width).toBeLessThanOrEqual(390)
  await draft.getByRole('button',{name:'加入投资行动'}).click()
  const final=page.getByTestId('confirm-order-dialog')
  await expect(final).toContainText('卖出')
  expect((await final.boundingBox())!.width).toBeLessThanOrEqual(390)
  await final.getByTestId('cancel-btn').click()
})


function ibkrLink() {
  const supplied = process.env.WP_ACTUAL_CHATGPT_LINK
  return supplied ? base + new URL(supplied).hash.replace(/^#/, '/#') : `${base}/#/action?trade=ibkr&request_id=${randomUUID()}&broker=IBKR&symbol=IB01%3ALSE&side=BUY&order_type=LIMIT&target_amount=4000&currency=USD&time_in_force=DAY`
}

async function resetIBKR(request: import('@playwright/test').APIRequestContext) {
  await request.post(`${base}/__test/ibkr-state`, {data:{quality:'LIVE',cash:10000,fee:5}})
}

for (const mobile of [false,true]) {
  test(`IB01 unified dialogs: all-in quote, edit, final plan and version invalidation (${mobile?'mobile':'desktop'})`, async ({page,request}) => {
    await resetIBKR(request)
    if (mobile) await page.setViewportSize({width:390,height:844})
    const before = await (await request.get(`${base}/__test/ibkr-counts`)).json()
    let quoteCalls=0
    page.on('request',r => { if(r.url().endsWith('/trade-link/quote')) quoteCalls++ })
    const submissions:string[]=[]
    page.on('request',r => { if(r.url().endsWith('/submit') || r.url().endsWith('/place_order')) submissions.push(r.url()) })
    const url=ibkrLink()
    await page.goto(url)
    const draft=page.getByRole('dialog')
    await expect(draft.getByRole('heading',{name:/行动清单/})).toBeVisible()
    await expect(draft.getByLabel('标的及市场')).toHaveValue('IB01:LSE')
    await expect(draft.getByLabel('总预算（USD，含费用）')).toHaveValue('4000')
    await expect(draft.getByLabel('有效期')).toHaveValue('DAY')
    await expect(draft.getByTestId('ibkr-review-fields')).not.toBeVisible()
    await expect(page.getByTestId('confirm-order-dialog')).not.toBeVisible()
    expect(quoteCalls).toBe(0)
    expect(await (await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
    if (process.env.WP_UI_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.WP_UI_SCREENSHOT_DIR}/isolated-${mobile?'mobile':'desktop'}-entry.png`})
    await draft.getByRole('button',{name:'加入投资行动'}).click()
    await expect(draft.getByRole('heading',{name:/投资行动核对/})).toBeVisible()
    await expect(draft).toContainText('LSEETF / SMART / USD')
    await draft.getByText('证券身份及诊断',{exact:true}).click()
    await expect(draft).toContainText('354802220')
    await expect(draft).toContainText('IE00BGSF1X88')
    await expect(draft.getByTestId('ibkr-quantity')).toHaveText('39')
    await expect(draft.getByTestId('ibkr-total-outlay')).toHaveText('3,930 USD')
    await expect(draft).toContainText('WhatIf：PASS')
    expect((await (await request.get(`${base}/__test/ibkr-counts`)).json()).batches).toBe(before.batches)
    await expect(page.getByText('多标的交易执行',{exact:true})).not.toBeVisible()
    await expect(page.getByText(/IBKR Case 1/)).not.toBeVisible()
    await draft.getByLabel('IBKR 限价').fill('130')
    await expect(draft.getByRole('button',{name:'核对并进入最终确认'})).toBeDisabled()
    await draft.getByRole('button',{name:'重新计算股数、费用及 WhatIf'}).click()
    await expect(draft.getByTestId('ibkr-quantity')).toHaveText('30')
    await expect(draft.getByTestId('ibkr-total-outlay')).toHaveText('3,930 USD')
    if (process.env.WP_UI_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.WP_UI_SCREENSHOT_DIR}/isolated-${mobile?'mobile':'desktop'}-prefill.png`})
    await draft.getByRole('button',{name:'核对并进入最终确认'}).click()
    const final=page.getByTestId('confirm-order-dialog')
    await expect(final).toContainText('最终确认下单')
    await expect(final).toContainText('WhatIf：PASS')
    await final.getByText('请求诊断',{exact:true}).click()
    await expect(final).toContainText('当前请求：')
    await expect(final).toContainText(new URLSearchParams(new URL(url).hash.split('?')[1]).get('request_id')!)
    await expect(final.getByTestId('submit-btn')).toBeDisabled()
    await final.getByTestId('risk-confirm-input').fill('我已知晓风险并坚持下单')
    await expect(final.getByTestId('confirm-ibkr-plan')).not.toBeVisible()
    await expect(final).toContainText('READY')
    await final.getByRole('checkbox').check()
    await expect(final.getByTestId('submit-btn')).toBeDisabled() // real safety flags stay OFF
    if (process.env.WP_UI_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.WP_UI_SCREENSHOT_DIR}/isolated-${mobile?'mobile':'desktop'}-final.png`})
    const bounds=await final.boundingBox()
    expect(bounds!.width).toBeLessThanOrEqual(mobile?390:1280)
    await final.getByLabel('IBKR 限价').fill('100')
    await expect(final.getByRole('checkbox')).not.toBeChecked()
    await final.getByRole('button',{name:'重新计算股数、费用及 WhatIf'}).click()
    await expect(final.getByTestId('ibkr-quantity')).toHaveText('39')
    await expect(final.getByTestId('confirm-ibkr-plan')).not.toBeVisible()
    await expect(final.getByTestId('review-economic-change')).toBeVisible()
    await final.getByTestId('cancel-btn').click()
    expect(submissions).toHaveLength(0)
    const after=await (await request.get(`${base}/__test/ibkr-counts`)).json()
    expect(after.orders).toBe(before.orders)
    expect(after.real_order_calls).toBe(0)
  })
}

test('IB01 delayed quote / insufficient cash blocks confirmation in original dialog',async ({page,request}) => {
  for (const state of [{quality:'DELAYED',cash:10000,fee:5},{quality:'LIVE',cash:3999,fee:5}]) {
    await request.post(`${base}/__test/ibkr-state`,{data:state})
    await page.goto(ibkrLink())
    const dialog=page.getByRole('dialog')
    await expect(dialog.getByRole('button',{name:'加入投资行动'})).toBeEnabled()
    await dialog.getByRole('button',{name:'加入投资行动'}).click()
    await expect(dialog.getByRole('button',{name:'核对并进入最终确认'})).toBeDisabled()
    if(state.cash===3999) await expect(dialog.getByRole('alert')).toContainText('INSUFFICIENT')
    else await expect(dialog.getByRole('alert')).toContainText('marketDataType')
  }
  await resetIBKR(request)
})

test('HK ordinary prefill retains original dialogs and currency',async({page})=>{
  await page.goto(link().replace('NVDA%3AUS','00700%3AHK').replace('currency=USD','currency=HKD'))
  const dialog=page.getByRole('dialog')
  await expect(dialog.getByLabel('标的及市场')).toHaveValue('0700:HK')
  await expect(dialog.getByLabel('交易币种')).toHaveValue('HKD')
  await dialog.getByRole('button',{name:'加入投资行动'}).click()
  const final=page.getByTestId('confirm-order-dialog')
  await expect(final).toContainText('HKD')
  await final.getByTestId('cancel-btn').click()
})

test('IB01 entry edits preserve budget intent; returning to edit discards old read-only preview',async({page,request})=>{
  await resetIBKR(request)
  const before=await (await request.get(`${base}/__test/ibkr-counts`)).json()
  let quoteCalls=0
  page.on('request',r=>{if(r.url().endsWith('/trade-link/quote')) quoteCalls++})
  await page.goto(ibkrLink())
  const dialog=page.getByRole('dialog')
  await dialog.getByLabel('总预算（USD，含费用）').fill('3500')
  await dialog.getByLabel('限价（可选）').fill('130')
  expect(quoteCalls).toBe(0)
  await dialog.getByRole('button',{name:'加入投资行动'}).click()
  await expect(dialog.getByTestId('ibkr-link-target')).toHaveText('3,500 USD')
  await expect(dialog.getByTestId('ibkr-quantity')).toHaveText('26')
  await expect(dialog.getByTestId('ibkr-total-outlay')).toHaveText('3,410 USD')
  await dialog.getByRole('button',{name:'返回编辑参数'}).click()
  await expect(dialog.getByRole('heading',{name:/行动清单/})).toBeVisible()
  await expect(dialog.getByTestId('ibkr-review-fields')).not.toBeVisible()
  await expect(dialog.getByLabel('总预算（USD，含费用）')).toHaveValue('3500')
  await dialog.getByLabel('总预算（USD，含费用）').fill('4000')
  await dialog.getByLabel('限价（可选）').fill('100')
  await dialog.getByRole('button',{name:'加入投资行动'}).click()
  await expect(dialog.getByTestId('ibkr-quantity')).toHaveText('39')
  expect(quoteCalls).toBe(2)
  await dialog.getByRole('button',{name:'取消',exact:true}).click()
  expect(await (await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
})

test('IB01 quote outage leaves first-step editing available and blocks final confirmation',async({page,request})=>{
  const before=await (await request.get(`${base}/__test/ibkr-counts`)).json()
  await page.route('**/trade-link/quote',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'IBKR 调用超时（隔离故障注入）'})}))
  await page.goto(ibkrLink())
  const dialog=page.getByRole('dialog')
  await expect(dialog.getByRole('button',{name:'加入投资行动'})).toBeEnabled()
  await expect(dialog.getByRole('alert')).not.toBeVisible()
  await dialog.getByRole('button',{name:'加入投资行动'}).click()
  await expect(dialog.getByRole('alert')).toContainText('IBKR 调用超时')
  await expect(dialog.getByRole('button',{name:'核对并进入最终确认'})).toBeDisabled()
  await expect(page.getByTestId('confirm-order-dialog')).not.toBeVisible()
  await dialog.getByRole('button',{name:'返回编辑参数'}).click()
  await expect(dialog.getByLabel('总预算（USD，含费用）')).toHaveValue('4000')
  await dialog.getByRole('button',{name:'关闭行动清单'}).click()
  await expect(dialog).not.toBeVisible()
  expect(await (await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
})

test('IB01 invalid edits are rejected by existing pure validation without quote or persistence',async({page,request})=>{
  const before=await (await request.get(`${base}/__test/ibkr-counts`)).json()
  let quoteCalls=0
  page.on('request',r=>{if(r.url().endsWith('/trade-link/quote')) quoteCalls++})
  await page.goto(ibkrLink())
  const dialog=page.getByRole('dialog')
  await dialog.getByLabel('总预算（USD，含费用）').fill('0')
  await expect(dialog.getByRole('button',{name:'加入投资行动'})).toBeDisabled()
  await dialog.getByLabel('总预算（USD，含费用）').fill('4000')
  await dialog.getByLabel('标的及市场').fill('IB01:US')
  await dialog.getByRole('button',{name:'加入投资行动'}).click()
  await expect(dialog.getByRole('alert')).toContainText('IBKR_LINK_REQUIRES_VERIFIED_IB01_LSE_USD')
  await expect(dialog.getByRole('heading',{name:/行动清单/})).toBeVisible()
  expect(quoteCalls).toBe(0)
  expect(await (await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
  await dialog.getByRole('button',{name:'取消',exact:true}).click()
})

test('new IB01 link clears earlier final step and returns to first-screen editing',async({page,request})=>{
  await resetIBKR(request)
  await page.goto(ibkrLink())
  await page.getByRole('button',{name:'加入投资行动'}).click()
  await page.getByRole('button',{name:'核对并进入最终确认'}).click()
  await expect(page.getByTestId('confirm-order-dialog')).toBeVisible()
  const before=await (await request.get(`${base}/__test/ibkr-counts`)).json()
  await page.evaluate(hash=>{window.location.hash=hash},new URL(ibkrLink()).hash)
  await expect(page.getByTestId('confirm-order-dialog')).not.toBeVisible()
  await expect(page.getByRole('dialog').getByRole('heading',{name:/行动清单/})).toBeVisible()
  await expect(page.getByRole('button',{name:'加入投资行动'})).toBeEnabled()
  expect(await (await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
})

test('legacy saved draft retains edit, cancel and close without saving local changes',async({page,request})=>{
  const created=await (await request.post(`${base}/api/action/drafts`,{data:{conversation_id:randomUUID(),decision_summary:'Isolated legacy draft edit regression',payload:{symbol_strategies:[{symbol:'NVDA:US',side:'BUY',quantity:3,limit_price:180,order_type:'LIMIT',trigger_price:null}],allocation_intents:[],risk_notes:[],missing_fields:[]}}})).json()
  expect(created.status).toBe('draft')
  await page.goto(`${base}/#/action`)
  const card=page.getByText('Isolated legacy draft edit regression').locator('..').locator('..')
  await card.getByRole('button',{name:'查看并确认'}).click()
  const dialog=page.getByRole('dialog')
  await expect(dialog.getByRole('heading',{name:/行动清单/})).toBeVisible()
  await dialog.getByLabel('数量（股）').fill('7')
  await dialog.getByRole('button',{name:'取消',exact:true}).click()
  expect((await (await request.get(`${base}/api/action/drafts/${created.id}`)).json()).payload.symbol_strategies[0].quantity).toBe(3)
  await card.getByRole('button',{name:'查看并确认'}).click()
  await expect(dialog.getByLabel('数量（股）')).toHaveValue('3')
  await dialog.getByRole('button',{name:'关闭行动清单'}).click()
  await expect(dialog).not.toBeVisible()
})

for (const mobile of [false, true]) {
  test(`IB01 reopened stale request refreshes and enters final dialog once (${mobile ? 'mobile' : 'desktop'})`, async ({page, request}) => {
    await resetIBKR(request)
    if (mobile) await page.setViewportSize({width:390,height:844})
    const before = await (await request.get(`${base}/__test/ibkr-counts`)).json()
    const url = ibkrLink()
    let confirmations = 0
    let orderSubmissions = 0
    page.on('request', r => {
      if (r.url().endsWith('/confirm-intent')) confirmations++
      if (r.url().endsWith('/submit') || r.url().endsWith('/place_order')) orderSubmissions++
    })
    async function review() {
      await page.getByRole('button',{name:'加入投资行动',exact:true}).click()
      await expect(page.getByTestId('ibkr-review-fields')).toContainText('WhatIf：PASS')
      await expect(page.getByRole('button',{name:'核对并进入最终确认',exact:true})).toBeEnabled()
    }
    await page.goto(url)
    await review()
    await page.getByRole('button',{name:'核对并进入最终确认',exact:true}).click()
    const final = page.getByTestId('confirm-order-dialog')
    await expect(final).toContainText('最终确认下单')
    await request.post(`${base}/__test/age-ibkr-reviews`)
    await page.reload() // Same request_id and an old DB quote; a new preview is fresh.
    await expect(page.getByTestId('confirm-order-dialog')).toBeVisible()
    await page.getByLabel('IBKR 限价').fill('130')
    await expect(page.getByTestId('submit-btn')).toBeDisabled()
    await page.getByRole('button',{name:'重新计算股数、费用及 WhatIf',exact:true}).click()
    await expect(page.getByTestId('ibkr-quantity')).toHaveText('30')
    await expect(final).toContainText('最终确认下单')
    await expect(final).toContainText('WhatIf：PASS')
    await final.getByTestId('risk-confirm-input').fill('我已知晓风险并坚持下单')
    await expect(final.getByTestId('confirm-ibkr-plan')).not.toBeVisible()
    await expect(final.getByTestId('submit-btn')).toBeDisabled()
    expect(confirmations).toBe(1)
    expect(orderSubmissions).toBe(0)
    const after = await (await request.get(`${base}/__test/ibkr-counts`)).json()
    expect(after.batches).toBe(before.batches + 2)
    expect(after.orders).toBe(before.orders)
    expect(after.real_order_calls).toBe(0)
  })
}


test('completed IB01 request reload shows its result even after cash spent; no fresh quote or duplicate',async({page,request})=>{
  await resetIBKR(request)
  const url=ibkrLink()
  const requestId=new URLSearchParams(new URL(url).hash.split('?')[1]).get('request_id')!
  await page.goto(url)
  await page.getByRole('button',{name:'加入投资行动'}).click()
  await expect(page.getByTestId('ibkr-quantity')).toHaveText('39')
  await page.getByRole('button',{name:'核对并进入最终确认'}).click()
  await expect(page.getByTestId('confirm-order-dialog')).toBeVisible()
  await request.post(`${base}/__test/complete-ibkr-request`,{data:{request_id:requestId}})
  await request.post(`${base}/__test/ibkr-state`,{data:{cash:100}})
  await request.post(`${base}/__test/age-ibkr-reviews`)
  const before=await(await request.get(`${base}/__test/ibkr-counts`)).json()
  let quotes=0
  page.on('request',r=>{if(r.url().endsWith('/trade-link/quote'))quotes++})
  await page.reload()
  const final=page.getByTestId('confirm-order-dialog')
  await expect(final.getByTestId('ibkr-existing-order')).toContainText('COMPLETED / FILLED')
  await expect(final.getByLabel('IBKR 限价')).toBeDisabled()
  await expect(final.getByRole('button',{name:'重新计算股数、费用及 WhatIf'})).toBeDisabled()
  await expect(final.getByTestId('confirm-checkbox')).toBeDisabled()
  await expect(final.getByTestId('submit-btn')).toBeDisabled()
  await expect(final).not.toContainText('报价已过期')
  expect(quotes).toBe(0)
  expect(await(await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
})

// B3: real original preview/review endpoints, disposable DB, no broker mutations.
test('B3 prepared request restores parameters without another quote or intent confirmation',async({page,request})=>{
  await resetIBKR(request)
  const url=ibkrLink()
  await page.goto(url)
  await page.getByRole('button',{name:'加入投资行动',exact:true}).click()
  await page.getByRole('button',{name:'核对并进入最终确认',exact:true}).click()
  await expect(page.getByTestId('confirm-order-dialog')).toBeVisible()
  const before=await(await request.get(`${base}/__test/ibkr-counts`)).json()
  let repeated=0
  page.on('request',r=>{if(r.url().endsWith('/trade-link/quote')||r.url().endsWith('/confirm-intent'))repeated++})
  await page.reload()
  const final=page.getByTestId('confirm-order-dialog')
  await expect(final.getByTestId('ibkr-quantity')).toHaveText('39')
  await expect(final.getByLabel('IBKR 限价')).toHaveValue('100')
  await expect(final).toContainText('可用原币现金：10,000 USD')
  await expect(final.getByTestId('ibkr-budget-remaining')).toHaveText('70 USD')
  await expect(final.getByTestId('ibkr-account-cash-remaining')).toHaveText('6,070 USD')
  expect(repeated).toBe(0)
  expect(await(await request.get(`${base}/__test/ibkr-counts`)).json()).toEqual(before)
})

test('B3 30-second expiry and changed economics require a new acknowledgement',async({page,request})=>{
  await resetIBKR(request)
  await page.goto(ibkrLink())
  await page.getByRole('button',{name:'加入投资行动',exact:true}).click()
  await page.getByRole('button',{name:'核对并进入最终确认',exact:true}).click()
  const final=page.getByTestId('confirm-order-dialog')
  await expect(final).toBeVisible()
  await final.getByTestId('risk-confirm-input').fill('我已知晓风险并坚持下单')
  await final.getByRole('checkbox').check()
  await page.clock.install()
  await page.clock.setFixedTime(new Date(Date.now()+31000))
  await page.clock.runFor(1000)
  await expect(final).toContainText('报价已过期')
  await expect(final.getByTestId('submit-btn')).toBeDisabled()
  await page.clock.setFixedTime(new Date())
  await request.post(`${base}/__test/ibkr-state`,{data:{fee:10}})
  await final.getByLabel('IBKR 限价').fill('130')
  await final.getByRole('button',{name:'重新计算股数、费用及 WhatIf'}).click()
  await expect(final.getByTestId('ibkr-quantity')).toHaveText('30')
  await expect(final.getByTestId('review-economic-change')).toContainText('预计费用 5 → 10 USD')
  await expect(final.getByRole('checkbox')).not.toBeChecked()
  await expect(final.getByTestId('risk-confirm-input')).toHaveValue('')
  await expect(final.getByTestId('submit-btn')).toBeDisabled()
  await resetIBKR(request)
})

for(const outcome of ['filled','partially_filled','unknown','network_timeout']) {
  test(`B3 one final click and duplicate protection: ${outcome}`,async({page,request})=>{
    await resetIBKR(request)
    const before=await(await request.get(`${base}/__test/ibkr-counts`)).json()
    let submitted=0
    let confirmed: Record<string,unknown> | null=null
    // HTTP responses are synthetic only. Original submit endpoint is never hit.
    await page.route('**/execution-batches/safety',r=>r.fulfill({json:{ibkr_read_only_mode:false,live_trading_enabled:true}}))
    await page.route('**/execution-batches/*/confirm',async route=>{
      const response=await route.fetch()
      confirmed=await response.json()
      await route.fulfill({response})
    })
    await page.route('**/execution-batches/*/legs/*/submit',async route=>{
      submitted++
      if(outcome==='network_timeout') {await route.abort('timedout');return}
      await route.fulfill({json:{order_id:'synthetic-result',status:outcome}})
    })
    await page.route(/\/api\/execution-batches\/[^/]+$/,async route=>{
      if(route.request().method()!=='GET'||!confirmed){await route.fallback();return}
      const legs=(confirmed.legs as Record<string,unknown>[]).map(leg=>({...leg,linked_order_id:'synthetic-result',status:outcome==='filled'?'FILLED':outcome==='partially_filled'?'PARTIALLY_FILLED':'UNKNOWN'}))
      await route.fulfill({json:{...confirmed,status:outcome==='filled'?'COMPLETED':'NEEDS_ATTENTION',legs}})
    })
    await page.route('**/action/trade-and-sync-status?*',r=>r.fulfill({json:{items:[{order_id:'synthetic-result',trade_status:outcome==='network_timeout'?'unknown':outcome,filled_quantity:outcome==='filled'?39:outcome==='partially_filled'?10:0,ordered_quantity:39,asset_sync:{status:outcome==='filled'?'SYNC_UNVERIFIED':outcome==='partially_filled'?'SYNC_RUNNING':'SYNC_PENDING'}}]}}))
    await page.goto(ibkrLink())
    await page.getByRole('button',{name:'加入投资行动',exact:true}).click()
    await page.getByRole('button',{name:'核对并进入最终确认',exact:true}).click()
    const final=page.getByTestId('confirm-order-dialog')
    await final.getByTestId('risk-confirm-input').fill('我已知晓风险并坚持下单')
    await final.getByRole('checkbox').check()
    await expect(final.getByTestId('submit-btn')).toBeEnabled()
    await final.getByTestId('submit-btn').evaluate(button=>{(button as HTMLButtonElement).click();(button as HTMLButtonElement).click()})
    await expect(final.getByTestId('submit-btn')).toBeDisabled()
    await expect(final.getByTestId('trade-sync-result')).toBeVisible()
    await expect(final).toContainText(outcome==='filled'?'覆盖尚未证实':outcome==='partially_filled'?'部分成交':'待券商对账')
    expect(submitted).toBe(1)
    const counts=await(await request.get(`${base}/__test/ibkr-counts`)).json()
    expect(counts.real_order_calls).toBe(0)
    expect(counts.orders).toBe(before.orders) // No IBKR SDK or new local orders.
  })
}

test('B3 failure before dispatch leaves recheck available without implying an order was sent',async({page,request})=>{
  await resetIBKR(request)
  let submits=0
  page.on('request',r=>{if(r.url().endsWith('/submit'))submits++})
  await page.route('**/execution-batches/safety',r=>r.fulfill({json:{ibkr_read_only_mode:false,live_trading_enabled:true}}))
  await page.route('**/execution-batches/*/confirm',r=>r.abort('timedout'))
  await page.goto(ibkrLink())
  await page.getByRole('button',{name:'加入投资行动',exact:true}).click()
  await page.getByRole('button',{name:'核对并进入最终确认',exact:true}).click()
  const final=page.getByTestId('confirm-order-dialog')
  await final.getByTestId('risk-confirm-input').fill('我已知晓风险并坚持下单')
  await final.getByRole('checkbox').check()
  await final.getByTestId('submit-btn').click()
  await expect(final.getByTestId('submit-error')).toContainText('尚未调用下单接口')
  await expect(final.getByRole('button',{name:'重新计算股数、费用及 WhatIf'})).toBeEnabled()
  await expect(final.getByRole('checkbox')).not.toBeChecked()
  expect(submits).toBe(0)
})
