import { spawn, type ChildProcess } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

let server: ChildProcess
const base = 'http://127.0.0.1:18111'
const draft = (id = 'synthetic', price: number | null = 82, symbol = 'PDD:US') => ({
  id, status: 'draft', decision_summary: 'Synthetic isolated regression', missing_fields: [],
  payload: { symbol_strategies: [{ symbol, side: 'SELL', quantity: 30, quantity_pct: null,
    order_type: 'LIMIT', trigger_price: null, limit_price: price, parent_intent_index: 0,
    currency: 'USD', time_in_force: 'GTC' }], allocation_intents: [], risk_notes: [], missing_fields: [] },
})
test.beforeAll(async () => {
  server = spawn(process.execPath, [fileURLToPath(new URL('../../node_modules/vite/bin/vite.js', import.meta.url)), '--host', '127.0.0.1', '--port', '18111', '--strictPort'], { cwd: fileURLToPath(new URL('../..', import.meta.url)) })
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('isolated Vite startup timeout')), 20000)
    server.once('error', reject)
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Vite exited ${code}`)) })
    server.stdout?.on('data', chunk => { if (String(chunk).includes('18111')) { clearTimeout(timer); resolve() } })
  })
})
test.afterAll(() => server?.kill('SIGTERM'))

test('decimal intermediate text, clear, paste and unchanged numeric API transport', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  let received: unknown
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'PATCH') received = route.request().postDataJSON()
    await route.fulfill({ json: { ...draft(), status: 'confirmed' } })
  })
  await page.goto(`${base}/tests/fixtures/action-draft-isolation.html`)
  await page.waitForFunction(() => 'setDraft' in window)
  await page.evaluate(d => (window as unknown as { setDraft: (d: unknown) => void }).setDraft(d), draft())
  const limit = page.getByLabel('限价', { exact: true })
  const confirm = page.getByRole('button', { name: '加入投资行动' })
  await expect(limit).toHaveAttribute('inputmode', 'decimal')
  await limit.fill('82')
  await limit.evaluate(el => { const input = el as HTMLInputElement; input.setSelectionRange(input.value.length, input.value.length) })
  await limit.pressSequentially('.')
  await expect(limit).toHaveValue('82.')
  await expect(confirm).toBeDisabled()
  await limit.pressSequentially('40')
  await expect(limit).toHaveValue('82.40')
  await expect(confirm).toBeEnabled()
  for (const value of ['', '0', '82.40001']) {
    await limit.fill(value)
    await expect(confirm).toBeDisabled()
  }
  await limit.fill('82.40') // replacement/paste input event
  await limit.press('Backspace')
  await expect(limit).toHaveValue('82.4')
  await limit.fill('82.40')
  await confirm.click()
  expect(received).toMatchObject({ payload: { symbol_strategies: [{ limit_price: 82.4, quantity: 30 }] } })
  expect(errors).toEqual([])
})

test('unedited legacy price keeps existing eligibility; original missing fields still block', async ({ page }) => {
  await page.goto(`${base}/tests/fixtures/action-draft-isolation.html`)
  await page.waitForFunction(() => 'setDraft' in window)
  await page.evaluate(d => (window as unknown as { setDraft: (d: unknown) => void }).setDraft(d), draft('legacy', null))
  await expect(page.getByRole('button', { name: '加入投资行动' })).toBeEnabled()
  await page.evaluate(d => (window as unknown as { setDraft: (d: unknown) => void }).setDraft(d), { ...draft('missing', null), missing_fields: ['limit_price'] })
  await expect(page.getByRole('button', { name: '加入投资行动' })).toBeDisabled()
})

test('prefill request, source payload reorder and reopen cannot reuse indexed edit text', async ({ page }) => {
  await page.goto(`${base}/tests/fixtures/action-draft-isolation.html`)
  await page.waitForFunction(() => 'setDraft' in window)
  const set = async (d: unknown) => page.evaluate(d => (window as unknown as { setDraft: (d: unknown) => void }).setDraft(d), d)
  await set({ ...draft('', 82), prefill_request_id: 'request-A' })
  const limit = page.getByLabel('限价', { exact: true })
  await limit.fill('83.20')
  await set({ ...draft('', 90, 'LI:US'), prefill_request_id: 'request-B' })
  await expect(limit).toHaveValue('90')
  await limit.fill('91.20')
  await set({ ...draft('', 100, 'QQQ:US'), prefill_request_id: 'request-B' })
  await expect(limit).toHaveValue('100')
  await limit.fill('101.20')
  await page.evaluate(() => (window as unknown as { setOpen: (open: boolean) => void }).setOpen(false))
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.evaluate(() => (window as unknown as { setOpen: (open: boolean) => void }).setOpen(true))
  await expect(limit).toHaveValue('100')
  const first = draft('rows', 82).payload.symbol_strategies[0]
  const second = draft('rows', 90, 'LI:US').payload.symbol_strategies[0]
  await set({ ...draft('rows'), payload: { ...draft().payload, symbol_strategies: [first, second] } })
  const limits = page.getByLabel('限价', { exact: true })
  await limits.nth(0).fill('83.20')
  await set({ ...draft('rows'), payload: { ...draft().payload, symbol_strategies: [second, first] } })
  await expect(limits.nth(0)).toHaveValue('90')
  await expect(limits.nth(1)).toHaveValue('82')
})

test('semantic equality preserves decimal edits despite property order and unrelated metadata', async ({ page }) => {
  await page.goto(`${base}/tests/fixtures/action-draft-isolation.html`)
  await page.waitForFunction(() => 'setDraft' in window)
  const set = async (d: unknown) => page.evaluate(d => (window as unknown as { setDraft: (d: unknown) => void }).setDraft(d), d)
  const source = { ...draft('stable-context'), payload: { ...draft().payload,
    allocation_intents: [{ title: 'Synthetic allocation', target_allocation: { equity: 60, cash: 40 } }] } }
  await set(source)
  const limit = page.getByLabel('限价', { exact: true })
  await limit.fill('82.40')
  const reordered = { ...source, payload: Object.fromEntries(Object.entries(source.payload).reverse()) }
  await set(reordered)
  await expect(limit).toHaveValue('82.40')
  await expect(limit).toBeFocused()
  await set({ ...source, updated_at: '2026-11-03T15:00:00Z', decision_summary: 'Updated label',
    payload: { ...source.payload, display_metadata: { fetched_at: 'later' },
      symbol_strategies: source.payload.symbol_strategies.map(s => ({ ...Object.fromEntries(Object.entries(s).reverse()), value_sources: { limit_price: 'Updated explanatory metadata' } })),
      allocation_intents: [{ title: 'Updated explanatory title', target_allocation: { cash: 40, equity: 60 } }] } })
  await expect(limit).toHaveValue('82.40')
  await expect(limit).toBeFocused()
  await limit.fill('82.')
  await set({ ...source, updated_at: '2026-11-03T16:00:00Z' })
  await expect(limit).toHaveValue('82.')
  await expect(page.getByRole('button', { name: '加入投资行动' })).toBeDisabled()
})

test('meaningful source parameters and authorization fields invalidate edits', async ({ page }) => {
  await page.goto(`${base}/tests/fixtures/action-draft-isolation.html`)
  await page.waitForFunction(() => 'setDraft' in window)
  const set = async (d: unknown) => page.evaluate(d => (window as unknown as { setDraft: (d: unknown) => void }).setDraft(d), d)
  const source = draft('same-identity')
  const limit = page.getByLabel('限价', { exact: true })
  for (const change of [{ symbol: 'LI:US' }, { side: 'BUY' }, { quantity: 31 }, { quantity_pct: 10 },
    { order_type: 'CONDITIONAL_LIMIT' }, { trigger_price: 80 }, { limit_price: 90 },
    { currency: 'HKD' }, { time_in_force: 'DAY' }, { parent_intent_index: 1 }]) {
    await set(source)
    await limit.fill('83.20')
    await set({ ...source, payload: { ...source.payload, symbol_strategies: [{ ...source.payload.symbol_strategies[0], ...change }] } })
    await expect(limit).toHaveValue('limit_price' in change ? '90' : '82')
  }
  await set(source)
  await limit.fill('83.20')
  await set({ ...source, missing_fields: [{ target_type: 'symbol_strategy', target_index: 0, field: 'quantity', description: 'Quantity must be checked' }] })
  await expect(limit).toHaveValue('82')
  await expect(page.getByRole('button', { name: '加入投资行动' })).toBeDisabled()
  await set(source)
  await limit.fill('83.20')
  await set({ ...source, risk_notes: ['New source risk must be reviewed'] })
  await expect(limit).toHaveValue('82')
  const allocation = { ...source, payload: { ...source.payload,
    allocation_intents: [{ title: 'Synthetic allocation', target_allocation: { equity: 60, cash: 40 } }] } }
  await set(allocation)
  await limit.fill('83.20')
  await set({ ...allocation, payload: { ...allocation.payload,
    allocation_intents: [{ title: 'Synthetic allocation', target_allocation: { equity: 40, cash: 60 } }] } })
  await expect(limit).toHaveValue('82')
})
