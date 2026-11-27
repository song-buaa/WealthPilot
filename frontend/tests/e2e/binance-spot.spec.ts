import { spawn, type ChildProcess } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
let backend: ChildProcess
const base = 'http://127.0.0.1:18096'
test.beforeAll(async () => {
  backend = spawn(process.env.WP_TEST_PYTHON ?? 'python3', [
    fileURLToPath(new URL('../fixtures/binance_spot_backend.py', import.meta.url)), '18096',
  ])
  backend.stderr?.pipe(process.stderr)
  const deadline = Date.now() + 20000
  while (Date.now() < deadline) {
    try { if ((await fetch(base + '/api/demo/status')).ok) return } catch { /* bounded isolated startup */ }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error('Synthetic Binance server did not start')
})
test.afterAll(() => backend?.kill('SIGTERM'))

test('original dashboard shows exact BTC units, UNKNOWN P&L and one USDT balance', async ({ page, request }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(base + '/#/dashboard')
  await expect(page.getByText('BTC 现货', { exact: true })).toBeVisible()
  await expect(page.getByText('0.00123457', { exact: true })).toBeVisible()
  await expect(page.getByText('UNKNOWN', { exact: true }).first()).toBeVisible()
  const { items } = await (await request.get(base + '/api/portfolio/positions')).json()
  expect(items).toHaveLength(2)
  const btc = items.find((p: { symbol: string }) => p.symbol === 'BTC:CRYPTO')
  const cash = items.find((p: { symbol: string }) => p.symbol === 'USDT:CRYPTO')
  expect(btc.cost_price).toBeNull()
  expect(btc.profit_loss_value).toBeNull()
  expect(cash.quantity).toBe(120)
  expect(cash.original_currency).toBe('USDT')
  await page.getByRole('button', { name: /导入 \/ 导出数据（持仓）/ }).click()
  await page.getByRole('button', { name: '同步', exact: true }).click()
  await expect(page.getByText('Binance Spot', { exact: true }).first()).toBeVisible()
  expect(errors).toEqual([])
})
