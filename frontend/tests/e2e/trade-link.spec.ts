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
  return supplied ? base + new URL(supplied).hash.replace(/^#/, '/#') : `${base}/#/action?trade=ibkr&request_id=${randomUUID()}&broker=IBKR&symbol=IB01%3ALSE&side=BUY&order_type=LIMIT&target_amount=4000&currency=USD`
}

async function resetIBKR(request: import('@playwright/test').APIRequestContext) {
  await request.post(`${base}/__test/ibkr-state`, {data:{quality:'LIVE',cash:10000,fee:5}})
}

for (const mobile of [false,true]) {
  test(`IB01 unified dialogs: all-in quote, edit, final plan and version invalidation (${mobile?'mobile':'desktop'})`, async ({page,request}) => {
    await resetIBKR(request)
    if (mobile) await page.setViewportSize({width:390,height:844})
    const before = await (await request.get(`${base}/__test/ibkr-counts`)).json()
    const submissions:string[]=[]
    page.on('request',r => { if(r.url().endsWith('/submit') || r.url().endsWith('/place_order')) submissions.push(r.url()) })
    await page.goto(ibkrLink())
    const draft=page.getByRole('dialog')
    await expect(draft.getByRole('heading',{name:/行动清单/})).toBeVisible()
    await expect(draft).toContainText('LSEETF / SMART / USD')
    await expect(draft).toContainText('354802220')
    await expect(draft).toContainText('IE00BGSF1X88')
    await expect(draft.getByTestId('ibkr-quantity')).toHaveText('39')
    await expect(draft.getByTestId('ibkr-total-outlay')).toHaveText('3,930 USD')
    await expect(draft).toContainText('WhatIf：PASS')
    expect((await (await request.get(`${base}/__test/ibkr-counts`)).json()).batches).toBe(before.batches)
    await expect(page.getByText('多标的交易执行',{exact:true})).not.toBeVisible()
    await expect(page.getByText(/IBKR Case 1/)).not.toBeVisible()
    await draft.getByLabel('IBKR 限价').fill('130')
    await expect(draft.getByRole('button',{name:'确认意图并进入最终确认'})).toBeDisabled()
    await draft.getByRole('button',{name:'重新计算股数、费用及 WhatIf'}).click()
    await expect(draft.getByTestId('ibkr-quantity')).toHaveText('30')
    await expect(draft.getByTestId('ibkr-total-outlay')).toHaveText('3,930 USD')
    if (process.env.WP_UI_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.WP_UI_SCREENSHOT_DIR}/isolated-${mobile?'mobile':'desktop'}-prefill.png`})
    await draft.getByRole('button',{name:'确认意图并进入最终确认'}).click()
    const final=page.getByTestId('confirm-order-dialog')
    await expect(final).toContainText('最终确认下单')
    await expect(final).toContainText('WhatIf：PASS')
    await expect(final).toContainText('当前请求：')
    await expect(final.getByTestId('submit-btn')).toBeDisabled()
    await final.getByTestId('confirm-ibkr-plan').click()
    await expect(final.getByTestId('confirm-ibkr-plan')).not.toBeVisible()
    await expect(final).toContainText('CONFIRMED')
    await final.getByRole('checkbox').check()
    await expect(final.getByTestId('submit-btn')).toBeDisabled() // real safety flags stay OFF
    if (process.env.WP_UI_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.WP_UI_SCREENSHOT_DIR}/isolated-${mobile?'mobile':'desktop'}-final.png`})
    const bounds=await final.boundingBox()
    expect(bounds!.width).toBeLessThanOrEqual(mobile?390:1280)
    await final.getByLabel('IBKR 限价').fill('100')
    await expect(final.getByRole('checkbox')).not.toBeChecked()
    await final.getByRole('button',{name:'重新计算股数、费用及 WhatIf'}).click()
    await expect(final.getByTestId('ibkr-quantity')).toHaveText('39')
    await expect(final.getByTestId('confirm-ibkr-plan')).toBeVisible()
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
    await expect(dialog.getByRole('button',{name:'确认意图并进入最终确认'})).toBeDisabled()
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
