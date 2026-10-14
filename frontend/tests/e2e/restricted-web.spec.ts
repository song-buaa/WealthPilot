import { expect, test } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'

test.describe.configure({ mode: 'serial' })

async function server(fails = false): Promise<{ base: string; process: ChildProcess }> {
  const port = await new Promise<number>((resolve) => {
    const sock = createServer()
    sock.listen(0, '127.0.0.1', () => {
      const address = sock.address() as { port: number }
      sock.close(() => resolve(address.port))
    })
  })
  const python = process.env.WP_TEST_PYTHON ?? '/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python'
  const fixture = fileURLToPath(new URL('../fixtures/restricted_web_server.py', import.meta.url))
  const child = spawn(python, [fixture, String(port), ...(fails ? ['--source-fails'] : [])], {
    env: { PATH: process.env.PATH, HOME: process.env.HOME, PYTHON_DOTENV_DISABLED: '1' }, stdio: 'ignore',
  })
  const base = `http://127.0.0.1:${port}`
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error('Synthetic Web fixture failed to start')
    try { if ((await fetch(`${base}/api/web/status`)).ok) return { base, process: child } } catch { /* wait for loopback */ }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  child.kill()
  throw new Error('Synthetic Web listener timed out')
}

test('restricted React pages, actual manual sync and explicit unavailable features', async ({ page, request }) => {
  const fixture = await server()
  const failures: string[] = []
  page.on('response', response => { if (response.url().includes('/api/') && response.status() >= 400) failures.push(response.url()) })
  try {
    await page.goto(fixture.base)
    await expect(page.getByText('手动券商同步', { exact: true })).toBeVisible()
    await expect(page.getByText('CSV、截图、基金导入及服务端导出尚未开放。')).toBeVisible()
    const buttons = page.getByRole('button', { name: '同步', exact: true })
    await expect(buttons).toHaveCount(3)
    await expect(buttons.nth(0)).toBeDisabled()
    await expect(buttons.nth(1)).toBeDisabled()
    await expect(buttons.nth(2)).toBeEnabled()
    await buttons.nth(2).click()
    await expect(page.getByText('同步成功', { exact: true })).toBeVisible()
    expect((await (await request.get(`${fixture.base}/api/wealth/summary`)).json()).total_assets).toBe(3500)
    await page.goto(`${fixture.base}/#/wealth`)
    await expect(page.getByRole('region', { name: '财富核心概览' })).toContainText('¥3,400')
    await expect(page.getByRole('button', { name: '新增资产', exact: true })).toBeDisabled()
    await expect(page.getByText('自动同步尚未启用；仅支持已验收券商的手动同步。')).toHaveCount(0)
    await page.goto(`${fixture.base}/#/decision`)
    await expect(page.getByRole('heading', { name: '此功能尚未开放' })).toBeVisible()
    expect(failures).toEqual([])
  } finally { fixture.process.kill() }
})

test('failed synthetic source is visible and existing wealth remains available', async ({ page, request }) => {
  const fixture = await server(true)
  try {
    await page.goto(fixture.base)
    await page.getByRole('button', { name: '同步', exact: true }).nth(2).click()
    await expect(page.getByText('同步失败', { exact: true })).toBeVisible()
    expect((await (await request.get(`${fixture.base}/api/wealth/summary`)).json()).total_assets).toBe(1000)
    await expect(page.getByText('自动同步尚未启用；仅支持已验收券商的手动同步。')).toBeVisible()
  } finally { fixture.process.kill() }
})
