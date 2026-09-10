import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

const appSource = readFileSync(fileURLToPath(new URL('../../src/App.tsx', import.meta.url)), 'utf8')
const sidebarSource = readFileSync(fileURLToPath(new URL('../../src/components/layout/Sidebar.tsx', import.meta.url)), 'utf8')

test('keeps current investment route labels and order inside the planning shell', () => {
  const expectedItems = [
    ['用户画像', '/profile'],
    ['投资账户总览', '/dashboard'],
    ['投资纪律', '/discipline'],
    ['投资观点', '/research'],
    ['投资决策', '/decision'],
    ['投资行动', '/action'],
  ]
  const positions = expectedItems.map(([label, route]) => {
    const itemStart = sidebarSource.indexOf(`label: '${label}'`)
    expect(sidebarSource.slice(itemStart, itemStart + 100)).toContain(`to: '${route}'`)
    return itemStart
  })

  expect(positions.every((position) => position >= 0)).toBe(true)
  expect(positions).toEqual([...positions].sort((left, right) => left - right))
  expect(sidebarSource).toContain('投资规划')
})

test('declares stable wealth, retirement, and consumption routes', () => {
  expect(appSource).toContain('path="/wealth"')
  expect(appSource).toContain('path="/retirement"')
  expect(appSource).toContain('path="/consumption"')
})

test('sidebar hierarchy, collapse, route expansion and independent scrolling', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = { items: [], total: 0 }
    if (path === '/api/demo/status') body = { public_demo_mode: false }
    else if (path === '/api/portfolio/summary') body = {
      total_assets: 1000, total_liabilities: 0, net_worth: 1000, total_profit_loss: 0,
      allocation: {}, platform_distribution: {}, concentration: {},
    }
    else if (path.includes('targets')) body = []
    await route.fulfill({ json: body })
  })
  await page.goto('/#/retirement')
  const nav = page.getByRole('navigation', { name: '主导航' })
  const parent = nav.getByRole('button', { name: '投资规划', exact: true })
  for (const name of ['总览', '财富管理', '系统']) {
    await expect(nav.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  await expect(parent).toHaveAttribute('aria-expanded', 'true')
  await parent.focus()
  await page.keyboard.press('Enter')
  await expect(nav.getByRole('link', { name: '投资账户总览' })).toBeHidden()
  await expect(page).toHaveURL(/#\/retirement$/)
  await parent.click()
  await nav.getByRole('link', { name: '投资账户总览' }).click()
  await expect(parent).toHaveAttribute('aria-expanded', 'true')
  await expect(parent).toHaveAttribute('aria-disabled', 'true')
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1)
  await expect(nav.getByRole('link', { name: '投资账户总览' })).toHaveAttribute('aria-current', 'page')
  await page.reload()
  await expect(parent).toHaveAttribute('aria-expanded', 'true')
  await expect(nav.getByRole('link', { name: '投资账户总览' })).toHaveAttribute('aria-current', 'page')
  await page.screenshot({ path: '/tmp/wealthpilot-sidebar-refined.png', fullPage: true })
  await nav.getByRole('link', { name: '养老规划' }).click()
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1)
  await expect(nav.getByRole('link', { name: '投资账户总览' })).not.toHaveAttribute('aria-current', 'page')
  await parent.click()
  await expect(parent).toHaveAttribute('aria-expanded', 'false')
  await page.goto('/#/dashboard')
  await expect(parent).toHaveAttribute('aria-expanded', 'true')
  await page.setViewportSize({ width: 1100, height: 400 })
  expect(await nav.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true)
  await nav.getByRole('link', { name: '设置', exact: true }).scrollIntoViewIfNeeded()
  expect(await nav.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  expect(await page.locator('main').evaluate(el => el.scrollTop)).toBe(0)
})
