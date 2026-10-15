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
