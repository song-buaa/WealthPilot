import { spawn, type ChildProcess } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

let backend: ChildProcess
const base = 'http://127.0.0.1:18091'
test.beforeAll(async () => {
  backend = spawn(process.env.WP_TEST_PYTHON ?? 'python3', [
    fileURLToPath(new URL('../fixtures/wealth_live_backend.py', import.meta.url)), '18091',
  ])
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Isolated backend did not start')), 20000)
    backend.once('error', reject)
    backend.once('exit', code => { clearTimeout(timer); reject(new Error(`Backend exit ${code}`)) })
    backend.stderr?.on('data', chunk => {
      if (String(chunk).includes('Uvicorn running')) { clearTimeout(timer); resolve() }
    })
  })
})
test.afterAll(() => backend?.kill('SIGTERM'))

test('real service + SQLite: A minus B minus C, before and after committed sync', async ({ page, request }) => {
  await page.goto(`${base}/#/wealth`)
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('-¥450')
  await page.getByRole('link', { name: '投资账户总览', exact: true }).click()
  await expect(page.getByText('¥1,000', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: /导入 \/ 导出数据（持仓）/ }).click()
  await page.getByRole('button', { name: '同步', exact: true }).click()
  await expect(page.getByText('¥1,400', { exact: true }).first()).toBeVisible({ timeout: 15000 })
  await page.getByRole('link', { name: '财富总览', exact: true }).click()
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('-¥50')
  await expect(page.getByRole('region', { name: '资产与负债结构' }).getByText('¥1,250', { exact: true })).toBeVisible()
  const details = page.getByRole('region', { name: '资产与负债明细' })
  await expect(details.locator('tbody tr').first()).toContainText('投资资产汇总')
  await expect(details.locator('tbody tr').first()).toContainText('¥1,250')
  await expect(details.getByRole('link', { name: '投资资产汇总' })).toHaveAttribute('href', '#/dashboard')
  await expect(details.getByRole('button', { name: '新增资产' })).toBeVisible()
  await expect(details.getByRole('button', { name: '导出资产' })).toBeVisible()
  await details.getByRole('button', { name: '负债', exact: true }).click()
  await expect(details.getByRole('button', { name: '新增负债' })).toBeVisible()
  await expect(details.getByRole('button', { name: '导出负债' })).toBeVisible()
  await details.getByRole('button', { name: '新增负债' }).click()
  await expect(page.getByLabel('类型')).toHaveValue('liability')
  await page.getByRole('button', { name: '关闭' }).click()
  await details.getByRole('button', { name: '资产', exact: true }).click()
  const portfolio = await (await request.get(`${base}/api/portfolio/summary`)).json()
  const wealth = await (await request.get(`${base}/api/wealth/summary`)).json()
  expect(portfolio.total_assets).toBe(1400)
  expect(wealth.investment.total_assets).toBe(1400 - 100 - 50)
  expect(wealth.total_assets).toBe(1450)
  expect(wealth.net_worth).toBe(-50)
  expect(wealth.pension_benefit).toBe(150)
  // Exercise each refresh trigger against the real service, without reloading.
  for (const event of ['focus', 'visibilitychange', 'portfolio-updated']) {
    const refreshed = page.waitForResponse(response => response.url().includes('/api/wealth/summary') && response.ok())
    await page.evaluate(name => {
      if (name === 'visibilitychange') document.dispatchEvent(new Event(name))
      else window.dispatchEvent(new Event(name))
    }, event)
    expect((await (await refreshed).json()).investment.total_assets).toBe(1250)
    await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('-¥50')
  }
  await expect(page.getByRole('button', { name: /同步投资账户|刷新券商数据/ })).toHaveCount(0)
  await details.getByRole('link', { name: '投资资产汇总' }).click()
  await expect(page.getByText('¥1,400', { exact: true }).first()).toBeVisible()
})
