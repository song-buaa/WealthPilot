import { spawn, type ChildProcess } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

let backend: ChildProcess
const base = 'http://127.0.0.1:18096'
test.beforeAll(async () => {
  backend = spawn(process.env.WP_TEST_PYTHON ?? 'python3', [
    fileURLToPath(new URL('../fixtures/guojin_native_backend.py', import.meta.url)), '18096',
  ])
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Synthetic backend did not start')), 20000)
    backend.once('error', reject)
    backend.once('exit', code => { clearTimeout(timer); reject(new Error(`Backend exit ${code}`)) })
    backend.stderr?.on('data', chunk => {
      if (String(chunk).includes('Uvicorn running')) { clearTimeout(timer); resolve() }
    })
  })
})
test.afterAll(() => backend?.kill('SIGTERM'))

test('native four holdings, repeated sync, Web/MCP and last-good agree', async ({ page, request }) => {
  await page.goto(`${base}/#/dashboard`)
  await expect(page.getByText('¥1,490', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: /境内券商/ }).click()
  await expect(page.getByText('合成汽车', { exact: true })).toHaveCount(2)
  const initial = await (await request.get(`${base}/api/portfolio/positions`)).json()
  expect(initial.total).toBe(5) // four securities plus one native cash fact
  for (let attempt = 0; attempt < 2; attempt++) {
    expect((await request.post(`${base}/api/broker-sync/trigger`, { data: { broker: 'guojin' } })).ok()).toBeTruthy()
    expect((await (await request.get(`${base}/api/portfolio/positions`)).json()).items).toEqual(initial.items)
  }
  const mcp = await (await request.get(`${base}/test/mcp`)).json()
  const portfolio = await (await request.get(`${base}/api/portfolio/summary`)).json()
  const wealth = await (await request.get(`${base}/api/wealth/summary`)).json()
  expect(mcp.portfolio_summary.total_assets).toBe(portfolio.total_assets)
  expect(wealth.investment.total_assets).toBe(portfolio.total_assets)
  expect(mcp.items.filter((p: { security_symbol: string }) => p.security_symbol === '2015:HK')).toHaveLength(2)
  expect(mcp.issues).toContain('PRICE_TIME_UNKNOWN')
  await page.getByRole('link', { name: '财富总览', exact: true }).click()
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('¥1,490')
  await request.post(`${base}/test/incomplete`)
  await request.post(`${base}/api/broker-sync/trigger`, { data: { broker: 'guojin' } })
  const failed = await (await request.get(`${base}/api/broker-sync/status`)).json()
  const status = failed.brokers.find((b: { broker: string }) => b.broker === 'guojin')
  expect(status.last_attempt_status).toBe('failed')
  expect(status.last_attempt_error_message).toBe('NATIVE_BATCH_INCOMPLETE')
  expect((await (await request.get(`${base}/api/portfolio/positions`)).json()).items).toEqual(initial.items)
})
