import { spawn, type ChildProcess } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
let backend: ChildProcess
let snapshot: string
const base = 'http://127.0.0.1:18094'
test.beforeAll(async () => {
  backend = spawn(process.env.WP_TEST_PYTHON ?? 'python3', [fileURLToPath(new URL('../fixtures/u1_asset_view_backend.py', import.meta.url)), '18094'])
  backend.stderr?.pipe(process.stderr)
  const until = Date.now()+30000
  while (Date.now()<until) {
    try {
      const response=await fetch(base+'/__test/context')
      if (response.ok) { snapshot=(await response.json()).snapshot_id; return }
    } catch { /* bounded synthetic startup */ }
    await new Promise(resolve => setTimeout(resolve,100))
  }
  throw new Error('Isolated asset view server did not start')
})
test.afterAll(() => backend?.kill('SIGTERM'))

test('fixed wealth, separate securities and issuer exposure survive live mutation', async ({ page,request }) => {
  await page.goto(`${base}/#/asset-view/${snapshot}`)
  await expect(page.getByText('总资产 ¥1,450.00')).toBeVisible()
  await expect(page.getByRole('status')).toContainText('不能直接用于精确调仓')
  await expect(page.getByText('Li Auto Inc.：¥500.00')).toBeVisible()
  await expect(page.locator('tbody tr')).toHaveCount(3)
  await request.post(base+'/__test/change-live')
  await page.reload()
  await expect(page.getByText('总资产 ¥1,450.00')).toBeVisible()
  await expect(page.getByTestId('asset-view-id')).toHaveText(snapshot)
})

test('review passes exact fixed input version, no prepare or trade request', async ({ page }) => {
  let writes=0
  page.on('request',r => { if (r.url().includes('/prepare') || r.url().includes('/place_order')) writes++ })
  await page.goto(`${base}/#/asset-view/${snapshot}`)
  await page.getByRole('button',{name:'使用此版本进行组合评估'}).click()
  await expect(page.getByTestId('asset-view-conclusion')).toContainText('fixed CNY 1450')
  expect(writes).toBe(0)
})

test('missing view is explicit and mobile view preserves original security rows', async ({ page }) => {
  await page.goto(`${base}/#/asset-view/asset-view:v1:99999`)
  await expect(page.getByRole('alert')).toContainText('固定资产视图不可用')
  await page.setViewportSize({width:390,height:844})
  await page.goto(`${base}/#/asset-view/${snapshot}`)
  await expect(page.locator('tbody tr')).toHaveCount(3)
  await expect(page.getByRole('heading',{name:'本次分析的固定资产视图'})).toBeVisible()
  // A hash-route change must not label the previous view/conclusion with a new ID.
  await page.evaluate(() => { window.location.hash='/asset-view/asset-view:v1:99999' })
  await expect(page.getByRole('alert')).toContainText('固定资产视图不可用')
  await expect(page.locator('tbody tr')).toHaveCount(0)
  await expect(page.getByText('总资产 ¥1,450.00')).toHaveCount(0)
})
