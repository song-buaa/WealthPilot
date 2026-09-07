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
  await expect(page.getByText('¥1,250', { exact: true })).toBeVisible()
  const portfolio = await (await request.get(`${base}/api/portfolio/summary`)).json()
  const wealth = await (await request.get(`${base}/api/wealth/summary`)).json()
  expect(portfolio.total_assets).toBe(1400)
  expect(wealth.investment.total_assets).toBe(1400 - 100 - 50)
  expect(wealth.total_assets).toBe(1450)
  expect(wealth.net_worth).toBe(-50)
  expect(wealth.pension_benefit).toBe(150)
  await page.getByRole('link', { name: '投资账户总览', exact: true }).click()
  await expect(page.getByText('¥1,400', { exact: true }).first()).toBeVisible()
})
