import { expect, test } from '@playwright/test'

test('sync waits for committed success; wealth refetches on entry and focus', async ({ page }) => {
  let value = 1000
  let run = 'old'
  let status = 'success'
  let reads = 0
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = {}
    if (path === '/api/demo/status') body = { public_demo_mode: false }
    else if (path === '/api/broker-sync/status') body = { brokers: [{ broker: 'tiger', platform: '老虎证券', last_sync_time: run, last_sync_status: status }] }
    else if (path === '/api/broker-sync/trigger') {
      run = 'new'; status = 'running'
      body = { brokers_triggered: ['tiger'] }
    } else if (path === '/api/portfolio/summary') body = {
      total_assets: value, total_liabilities: 0, net_worth: value, total_profit_loss: 0,
      allocation: {}, platform_distribution: {}, concentration: {},
    }
    else if (path === '/api/wealth/summary') {
      reads++
      body = {
        total_assets: value, total_liabilities: 0, net_worth: value, monthly_net_worth_change: null,
        investment: { total_assets: value, allocation: {} }, pension_benefit: 0,
        asset_breakdown: [{ category: 'investment', value }], liability_breakdown: [],
        attribution: { baseline_available: false }, trend: [],
      }
    } else if (path.includes('targets')) body = []
    else body = { items: [], total: 0 }
    await route.fulfill({ json: body })
  })
  await page.goto('/#/wealth')
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('¥1,000')
  await page.getByRole('link', { name: '投资账户总览', exact: true }).click()
  await page.getByRole('button', { name: /导入 \/ 导出数据（持仓）/ }).click()
  await page.getByRole('button', { name: '同步', exact: true }).click()
  await expect(page.getByText('⏳ 同步中...', { exact: true })).toBeVisible()
  await page.waitForTimeout(2300)
  await expect(page.getByText('✅ 同步完成', { exact: true })).toHaveCount(0)
  value = 1400; status = 'success'
  await expect(page.getByRole('button', { name: /导入 \/ 导出数据（持仓）/ })).toBeVisible()
  await expect(page.getByText('¥1,400', { exact: true }).first()).toBeVisible()
  await page.getByRole('link', { name: '财富总览', exact: true }).click()
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('¥1,400')
  const beforeFocus = reads
  value = 1600
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('¥1,600')
  expect(reads).toBeGreaterThan(beforeFocus)
  value = 1800
  await page.evaluate(() => window.dispatchEvent(new Event('portfolio-updated')))
  await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('¥1,800')
  await page.getByRole('link', { name: '投资账户总览', exact: true }).click()
  await expect(page.getByText('¥1,800', { exact: true }).first()).toBeVisible()
})
