import { expect, test } from '@playwright/test'

test('hides Futu on load and polling; sync all only triggers visible brokers', async ({ page }) => {
  const triggered: string[] = []
  let completed = false
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = { items: [], total: 0 }
    if (path === '/api/demo/status') body = { public_demo_mode: false }
    else if (path === '/api/broker-sync/status') body = { brokers:
      [['tiger', '老虎证券'], ['futu', '富途证券'], ['snowball', '雪盈证券'], ['guojin', '国金证券']].map(([broker, platform]) => ({
        broker, platform, last_sync_time: triggered.includes(broker) ? 'new' : 'old',
        last_sync_status: triggered.includes(broker) && !completed ? 'running' : 'success',
      })),
    }
    else if (path === '/api/broker-sync/trigger') {
      const { broker } = route.request().postDataJSON()
      triggered.push(broker)
      body = { brokers_triggered: [broker] }
    } else if (path === '/api/portfolio/summary') body = {
      total_assets: 1000, total_liabilities: 0, net_worth: 1000, total_profit_loss: 0,
      allocation: {}, platform_distribution: {}, concentration: {},
    }
    else if (path.includes('targets')) body = []
    await route.fulfill({ json: body })
  })
  await page.goto('/#/dashboard')
  await page.getByRole('button', { name: /导入 \/ 导出数据（持仓）/ }).click()
  await expect(page.getByRole('button', { name: '同步', exact: true })).toHaveCount(3)
  await expect(page.getByText('富途证券', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '同步全部', exact: true }).click()
  await expect.poll(() => triggered.length).toBe(3)
  expect([...triggered].sort()).toEqual(['guojin', 'snowball', 'tiger'])
  await page.waitForTimeout(2300)
  await expect(page.getByText('✅ 同步完成', { exact: true })).toHaveCount(0)
  await expect(page.getByText('富途证券', { exact: true })).toHaveCount(0)
  completed = true
  // Dashboard refresh remounts this panel, so completion text is transient.
  await expect(page.getByRole('button', { name: '同步全部', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: '同步', exact: true })).toHaveCount(3)
  await expect(page.getByText('富途证券', { exact: true })).toHaveCount(0)
  expect([...triggered].sort()).toEqual(['guojin', 'snowball', 'tiger'])
})
