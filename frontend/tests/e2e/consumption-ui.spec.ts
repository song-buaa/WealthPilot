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

const julyKpiResponse = {
  ...analyticsResponse,
  months: [point('2025-08-01', '900'), ...analyticsResponse.months.slice(0, -1)],
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
    const query = new URL(route.request().url()).searchParams
    const month = query.get('month') ?? ''
    const base = eventsByMonth[month] ?? { month, items: [], total: 0, limit: 200, offset: 0 }
    const items = base.items.filter(item => (
      (!query.get('classification_status') || item.classification_status === query.get('classification_status'))
      && (!query.get('primary_category') || item.primary_category === query.get('primary_category'))
      && (!query.get('secondary_category') || item.secondary_category === query.get('secondary_category'))
    ))
    return route.fulfill({ json: { ...base, items, total: items.length } })
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

test('renders net-spending KPIs and refreshes their rolling window with the selected month', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => {
    const asOf = new URL(route.request().url()).searchParams.get('as_of')
    return route.fulfill({ json: asOf === '2026-07-31' ? julyKpiResponse : analyticsResponse })
  })
  await page.goto('/#/consumption')

  await expect(page.getByText('消费分析', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('本月消费 · 2026年8月')).toBeVisible()
  await expect(page.getByText('¥2,100').first()).toBeVisible()
  await expect(page.getByText('截至 2026-08-20')).toBeVisible()
  await expect(page.getByText('近12个月消费')).toBeVisible()
  await expect(page.getByText('¥18,600')).toBeVisible()
  await expect(page.getByText('月均 ¥1,550')).toBeVisible()
  await expect(page.getByText('本月消费环比')).toBeVisible()
  await expect(page.getByText('本月尚未结束')).toBeVisible()
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
  await expect(page.getByText('分类覆盖率', { exact: true })).toHaveCount(0)
  await expect(page.getByText('数据覆盖', { exact: true })).toHaveCount(0)
  await expect(page.getByText('2026年8月消费明细')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '消费明细' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '消费名称' })).toHaveCount(0)
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
  await expect(page.getByText('本月消费 · 2026年7月')).toBeVisible()
  await expect(page.getByText('¥17,400')).toBeVisible()
  await expect(page.getByText('月均 ¥1,450')).toBeVisible()
  await expect(page.getByText('+5.3%')).toBeVisible()
  await expect(page.getByText('较6月 · ¥1,900')).toBeVisible()
  await expect(page.getByText('2026年7月消费结构')).toBeVisible()
  await expect(page.getByText('2026年7月二级分类')).toBeVisible()
  await expect(page.getByText('餐饮').first()).toBeVisible()
  await expect(page.getByText('住宿')).toHaveCount(0)
  await expect(page.getByText('原始账单描述：餐饮')).toBeVisible()
  await expect(page.getByText('房租')).toHaveCount(0)
})

test('shows no month-over-month value when the prior month is zero or missing', async ({ page }) => {
  await mockDemo(page)
  const zeroPrevious = { ...analyticsResponse, months: [point('2026-06-01', '0'), point('2026-07-01', '1000')] }
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: zeroPrevious }))
  await page.goto('/#/consumption')
  await expect(page.getByText('本月消费 · 2026年7月')).toBeVisible()
  await expect(page.getByText('暂无可比上月数据')).toBeVisible()

  await page.unroute('**/api/consumption/analytics*')
  const missingPrevious = { ...analyticsResponse, months: [point('2026-07-01', '1000')] }
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: missingPrevious }))
  await page.reload()
  await expect(page.getByText('暂无可比上月数据')).toBeVisible()
})

test('filters monthly details server-side and removes an autosaved review row', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: analyticsResponse }))
  await page.goto('/#/consumption')

  await page.getByLabel('分类状态筛选').selectOption('NEEDS_REVIEW')
  await expect(page.getByLabel('一级分类筛选')).toBeDisabled()
  await expect(page.getByLabel('二级分类筛选')).toBeDisabled()
  await expect(page.getByText('原始账单描述：待确认交易')).toBeVisible()
  await expect(page.getByText('原始账单描述：月度房租')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/api/consumption/events/export.csv?month=2026-08&classification_status=NEEDS_REVIEW')

  await page.getByLabel('一级分类 event-aug-unclassified').selectOption('DAILY')
  await page.getByLabel('二级分类 event-aug-unclassified').selectOption('SHOPPING')
  await expect(page.getByText('原始账单描述：待确认交易')).toHaveCount(0)
  await expect(page.getByText('当前筛选条件下暂无消费明细')).toBeVisible()

  await page.getByLabel('分类状态筛选').selectOption('CLASSIFIED')
  await page.getByLabel('一级分类筛选').selectOption('HOUSING')
  await page.getByLabel('二级分类筛选').selectOption('RENT')
  await expect(page.getByText('原始账单描述：月度房租')).toBeVisible()
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
