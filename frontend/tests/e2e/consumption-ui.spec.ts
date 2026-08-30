import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'
import { createServer, type ViteDevServer } from 'vite'

const root = fileURLToPath(new URL('../..', import.meta.url))
let viteServer: ViteDevServer

function point(
  month: string,
  total: string,
  coverage: 'COMPLETE' | 'PARTIAL' | 'SOURCE_LIMITED' | 'UNKNOWN' = 'COMPLETE',
  secondaryBreakdowns: Array<Record<string, string | number | null>> = [],
) {
  const value = Number(total)
  return {
    month,
    total_spending_cny: total,
    daily_cny: String(value * 0.5),
    travel_cny: String(value * 0.2),
    housing_cny: String(value * 0.1),
    unclassified_eligible_cny: String(value * 0.2),
    classified_eligible_cny: String(value * 0.8),
    classification_coverage_rate: '0.8',
    eligible_event_count: 4,
    eligibility_review_count: 2,
    classification_review_count: 1,
    amount_unresolved_count: month === '2026-08-01' ? 1 : 0,
    amount_unresolved_original_amount: month === '2026-08-01' ? '20' : '0',
    amount_unresolved_by_currency: month === '2026-08-01' ? [{ currency: 'USD', amount: '20', event_count: 1 }] : [],
    amount_complete: month !== '2026-08-01',
    data_coverage_status: coverage,
    is_partial_month: month === '2026-08-01',
    as_of_date: month === '2026-08-01' ? '2026-08-20' : null,
    comparison_available: month !== '2025-09-01' && month !== '2026-08-01',
    comparison_reason: month === '2026-08-01' ? 'MONTH_NOT_COMPARABLE' : null,
    secondary_breakdowns: secondaryBreakdowns,
  }
}

const analyticsResponse = {
  months: [
    '2025-09-01', '2025-10-01', '2025-11-01', '2025-12-01', '2026-01-01', '2026-02-01',
    '2026-03-01', '2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01',
  ].map((month, index) => point(
    month,
    String(1000 + index * 100),
    month === '2026-07-01' ? 'SOURCE_LIMITED' : month === '2026-08-01' ? 'PARTIAL' : 'COMPLETE',
    month === '2026-07-01'
      ? [{ primary_category: 'DAILY', secondary_category: 'FOOD_DINING', amount_cny: '1000', event_count: 4, share_of_total: '0.5', share_within_primary: '1' }]
      : month === '2026-08-01'
        ? [{ primary_category: 'TRAVEL', secondary_category: 'ACCOMMODATION', amount_cny: '420', event_count: 2, share_of_total: '0.21', share_within_primary: '1' }]
        : [],
  )),
  secondary_breakdowns: [
    { primary_category: 'DAILY', secondary_category: 'FOOD_DINING', amount_cny: '1200', event_count: 12, share_of_total: '0.2', share_within_primary: '0.5' },
    { primary_category: 'TRAVEL', secondary_category: 'ACCOMMODATION', amount_cny: '600', event_count: 2, share_of_total: '0.1', share_within_primary: '0.5' },
  ],
  complete_month_average_cny: '1400',
  complete_month_count: 10,
  three_month_average: { amount_cny: '1500', months_used: 2 },
  twelve_month_average: { amount_cny: '1400', months_used: 10 },
}

const eventsByMonth: Record<string, { month: string; items: Array<Record<string, string>>; total: number; limit: number; offset: number }> = {
  '2026-08': {
    month: '2026-08-01', total: 2, limit: 200, offset: 0,
    items: [
      { event_id: 'event-aug-rent', analytics_effective_date: '2026-08-20', raw_description: '原始账单描述：月度房租', account_display_name: 'CMB Debit ****', primary_category: 'HOUSING', secondary_category: 'RENT', classification_status: 'CLASSIFIED', amount_cny: '6500' },
      { event_id: 'event-aug-unclassified', analytics_effective_date: '2026-08-18', raw_description: '原始账单描述：待确认交易', account_display_name: 'CMB Debit ****', primary_category: '', secondary_category: '', classification_status: 'NEEDS_REVIEW', amount_cny: '20' },
    ],
  },
  '2026-07': {
    month: '2026-07-01', total: 1, limit: 200, offset: 0,
    items: [
      { event_id: 'event-jul-food', analytics_effective_date: '2026-07-15', raw_description: '原始账单描述：餐饮', account_display_name: 'CMB Debit ****', primary_category: 'DAILY', secondary_category: 'FOOD_DINING', classification_status: 'CLASSIFIED', amount_cny: '1000' },
    ],
  },
}

async function mockDemo(page: Page) {
  await page.route('**/api/demo/status', route => route.fulfill({ json: { public_demo_mode: false, password_required: false } }))
  await page.route('**/api/consumption/events*', route => {
    const month = new URL(route.request().url()).searchParams.get('month') ?? ''
    return route.fulfill({ json: eventsByMonth[month] ?? { month, items: [], total: 0, limit: 200, offset: 0 } })
  })
  await page.route('**/api/consumption/events/*/classification', route => route.fulfill({
    json: { event_id: 'event-aug-rent', primary_category: 'DAILY', secondary_category: 'SHOPPING', classification_status: 'CLASSIFIED', revision_number: 2 },
  }))
}

test.beforeAll(async () => {
  viteServer = await createServer({ root, appType: 'spa', logLevel: 'silent' })
  await viteServer.listen()
})

test.afterAll(async () => { await viteServer.close() })

test('renders analytics and selected-month detail without auxiliary cards', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: analyticsResponse }))
  await page.goto('/#/consumption')

  await expect(page.getByText('消费分析', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('近 12 个月消费趋势')).toBeVisible()
  await expect(page.getByText('日常消费', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('旅行消费', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('住房消费', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('待分类', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('部分外币消费尚未完成人民币金额换算，当前为已知金额。')).toHaveCount(0)
  await expect(page.getByText('数据覆盖与金额状态')).toHaveCount(0)
  await expect(page.getByText('待确认状态')).toHaveCount(0)
  await expect(page.getByText('2026年8月二级分类')).toBeVisible()
  await expect(page.getByText('住宿')).toBeVisible()
  await expect(page.getByText('分析日期：2026-08-20（不代表数据完整覆盖）')).toBeVisible()
  await expect(page.getByText('本月数据截至', { exact: false })).toHaveCount(0)
  await expect(page.getByText('2026年8月消费明细')).toBeVisible()
  await expect(page.getByText('原始账单描述：月度房租')).toBeVisible()
  await expect(page.getByText('CMB Debit ****').first()).toBeVisible()
  await expect(page.getByText('共 2 条，按金额从高到低排列')).toBeVisible()
  await expect(page.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/api/consumption/events/export.csv?month=2026-08')
  await page.getByLabel('一级分类 event-aug-rent').selectOption('DAILY')
  await expect(page.getByLabel('二级分类 event-aug-rent')).toHaveValue('')
  await page.getByLabel('二级分类 event-aug-rent').selectOption('SHOPPING')
  await expect(page.getByLabel('保存分类 event-aug-rent')).toHaveCount(0)
  await expect(page.getByText('保存中…')).toBeVisible()
  await expect(page.getByText('已保存')).toBeVisible()

  await page.getByRole('button', { name: '7月' }).click()
  await expect(page.getByText('2026年7月消费结构')).toBeVisible()
  await expect(page.getByText('2026年7月二级分类')).toBeVisible()
  await expect(page.getByText('餐饮').first()).toBeVisible()
  await expect(page.getByText('住宿')).toHaveCount(0)
  await expect(page.getByText('原始账单描述：餐饮')).toBeVisible()
  await expect(page.getByText('房租')).toHaveCount(0)
  await expect(page.getByText('来源无法确认完整性').first()).toBeVisible()
})

test('renders loading, empty, and safe error states', async ({ page }) => {
  await mockDemo(page)
  let release = () => undefined
  const responseReady = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/consumption/analytics*', async route => {
    await responseReady
    await route.fulfill({ json: { ...analyticsResponse, months: [] } })
  })
  await page.goto('/#/consumption')
  await expect(page.getByLabel('正在加载消费数据')).toBeVisible()
  release()
  await expect(page.getByText('暂无消费分析数据')).toBeVisible()

  await page.unroute('**/api/consumption/analytics*')
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ status: 500, json: { detail: 'internal only' } }))
  await page.reload()
  await expect(page.getByText('消费数据加载失败')).toBeVisible()
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
})

for (const [status, label, detail] of [
  ['COMPLETE', '数据完整', '已接入账户的本月数据完整。'],
  ['PARTIAL', '数据未完整', '部分数据尚未完整覆盖，金额会随导入更新。'],
  ['SOURCE_LIMITED', '来源无法确认完整性', '招行信用卡账单未提供明确账单周期，当前基于已解析交易范围分析。'],
  ['UNKNOWN', '数据覆盖未知', '部分预期账户尚无可验证的导入覆盖范围。'],
] as const) {
  test(`renders ${status} coverage without overstating completeness`, async ({ page }) => {
    await mockDemo(page)
    const months = analyticsResponse.months.map(item => ({ ...item }))
    months[months.length - 1] = { ...months[months.length - 1], data_coverage_status: status }
    await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: { ...analyticsResponse, months } }))
    await page.goto('/#/consumption')
    await expect(page.getByText(label).first()).toBeVisible()
    await expect(page.getByText(detail).first()).toBeVisible()
  })
}
