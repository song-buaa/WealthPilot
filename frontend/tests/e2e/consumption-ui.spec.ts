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

const completeSecondaryResponse = {
  ...analyticsResponse,
  months: analyticsResponse.months.map(item => {
    const total = Number(item.total_spending_cny)
    return {
      ...item,
      secondary_breakdowns: [
        { primary_category: 'DAILY', secondary_category: 'FOOD_DINING', amount_cny: item.daily_cny, event_count: 1, share_of_total: String(Number(item.daily_cny) / total), share_within_primary: '1' },
        { primary_category: 'HOUSING', secondary_category: 'RENT', amount_cny: item.housing_cny, event_count: 1, share_of_total: String(Number(item.housing_cny) / total), share_within_primary: '1' },
        { primary_category: 'TRAVEL', secondary_category: 'ACCOMMODATION', amount_cny: item.travel_cny, event_count: 1, share_of_total: String(Number(item.travel_cny) / total), share_within_primary: '1' },
      ],
    }
  }),
}

const eventsByMonth: Record<string, { month: string; items: Array<Record<string, string>>; total: number; limit: number; offset: number }> = {
  '2026-08': {
    month: '2026-08-01', total: 2, limit: 200, offset: 0,
    items: [
      { event_id: 'event-aug-rent', analytics_effective_date: '2026-08-20', raw_description: '原始账单描述：月度房租', account_display_name: '招行借记卡 ****', primary_category: 'HOUSING', secondary_category: 'RENT', classification_status: 'CLASSIFIED', amount_cny: '6500' },
      { event_id: 'event-aug-unclassified', analytics_effective_date: '2026-08-18', raw_description: '原始账单描述：待确认交易', account_display_name: '招行借记卡 ****', primary_category: '', secondary_category: '', classification_status: 'NEEDS_REVIEW', amount_cny: '20' },
    ],
  },
  '2026-07': {
    month: '2026-07-01', total: 1, limit: 200, offset: 0,
    items: [
      { event_id: 'event-jul-food', analytics_effective_date: '2026-07-15', raw_description: '原始账单描述：餐饮', account_display_name: '招行借记卡 ****', primary_category: 'DAILY', secondary_category: 'FOOD_DINING', classification_status: 'CLASSIFIED', amount_cny: '1000' },
    ],
  },
}

async function mockDemo(page: Page) {
  await page.route('**/api/demo/status', route => route.fulfill({ json: { public_demo_mode: false, password_required: false } }))
  await page.route('**/api/consumption/events*', route => {
    const query = new URL(route.request().url()).searchParams
    const month = query.get('month') ?? ''
    const startMonth = query.get('start_month')
    const endMonth = query.get('end_month')
    const rangeItems = startMonth && endMonth
      ? Object.entries(eventsByMonth).filter(([key]) => key >= startMonth && key <= endMonth).flatMap(([, value]) => value.items)
      : null
    const base = rangeItems == null
      ? (eventsByMonth[month] ?? { month, items: [], total: 0, limit: 200, offset: 0 })
      : { month: startMonth, items: rangeItems, total: rangeItems.length, limit: 200, offset: 0 }
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
  await page.route('**/api/consumption/events/*/note', async route => {
    const eventId = route.request().url().split('/').at(-2)!
    const body = route.request().postDataJSON() as { user_note?: string | null }
    const note = body.user_note?.trim() || null
    await route.fulfill({ json: { event_id: eventId, user_note: note } })
  })
}

test.beforeAll(async () => {
  viteServer = await createServer({ root, appType: 'spa', logLevel: 'silent' })
  await viteServer.listen()
})

test.afterAll(async () => { await viteServer.close() })

test('renders net-spending KPIs while keeping the rolling window anchored to the latest month', async ({ page }) => {
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
  await expect(page.getByText('近12个月消费', { exact: true })).toBeVisible()
  await expect(page.getByText('¥18,600')).toBeVisible()
  await expect(page.getByText('月均 ¥1,550')).toBeVisible()
  await expect(page.getByText('本月消费环比')).toBeVisible()
  await expect(page.getByText('本月尚未结束')).toBeVisible()
  await expect(page.getByText('近 12 个月消费趋势')).toBeVisible()
  await expect(page.getByRole('button', { name: '7月' })).toHaveCount(0)
  await expect(page.getByTestId('trend-month-8月')).toHaveAttribute('fill', '#1D4ED8')
  await expect(page.getByTestId('trend-month-8月')).toHaveAttribute('font-weight', '700')
  await expect(page.locator('.recharts-legend-item-text')).toHaveText(['日常消费', '住房消费', '旅行消费', '待分类'])
  await expect(page.getByTestId('trend-bar-daily_cny-2026-08-01')).toBeVisible()
  await expect(page.getByTestId('trend-bar-travel_cny-2026-08-01')).toBeVisible()
  await expect(page.getByTestId('trend-bar-housing_cny-2026-08-01')).toBeVisible()
  await expect(page.getByTestId('trend-bar-unclassified_eligible_cny-2026-08-01')).toBeVisible()
  await expect(page.getByTestId('trend-bar-daily_cny-2026-08-01')).toHaveAttribute('fill-opacity', '1')
  await expect(page.getByTestId('trend-bar-daily_cny-2026-07-01')).toHaveAttribute('fill-opacity', '1')
  await expect(page.getByText('日常消费', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('旅行消费', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('住房消费', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('待分类', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('部分外币消费尚未完成人民币金额换算，当前为已知金额。')).toHaveCount(0)
  await expect(page.getByText('数据覆盖与金额状态')).toHaveCount(0)
  await expect(page.getByText('待确认状态')).toHaveCount(0)
  await expect(page.getByText('2026年8月消费结构')).toBeVisible()
  await expect(page.getByTestId('secondary-category-tab-DAILY')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('当前分类暂无消费明细')).toBeVisible()
  for (const [key, label, amount, share] of [
    ['daily_cny', '日常消费', '¥1,050', '50.0%'],
    ['housing_cny', '住房消费', '¥210', '10.0%'],
    ['travel_cny', '旅行消费', '¥420', '20.0%'],
    ['unclassified_eligible_cny', '待分类', '¥420', '20.0%'],
  ]) {
    await page.getByTestId(`structure-segment-${key}`).hover()
    await expect(page.getByTestId('structure-segment-tooltip')).toContainText(label)
    await expect(page.getByTestId('structure-segment-tooltip')).toContainText(amount)
    await expect(page.getByTestId('structure-segment-tooltip')).toContainText(share)
  }
  await expect(page.getByText('近12个月消费结构', { exact: true })).toBeVisible()
  await expect(page.getByText('统计区间：2025年9月 – 2026年8月').last()).toBeVisible()
  await expect(page.getByTestId('rolling-secondary-category-tab-DAILY')).toHaveAttribute('aria-selected', 'true')
  for (const [key, label, amount, share] of [
    ['daily_cny', '日常消费', '¥9,300', '50.0%'],
    ['housing_cny', '住房消费', '¥1,860', '10.0%'],
    ['travel_cny', '旅行消费', '¥3,720', '20.0%'],
    ['unclassified_eligible_cny', '待分类', '¥3,720', '20.0%'],
  ]) {
    await page.getByTestId(`rolling-structure-segment-${key}`).hover()
    await expect(page.getByTestId('rolling-structure-segment-tooltip')).toContainText(label)
    await expect(page.getByTestId('rolling-structure-segment-tooltip')).toContainText(amount)
    await expect(page.getByTestId('rolling-structure-segment-tooltip')).toContainText(share)
  }
  await page.getByTestId('secondary-category-tab-TRAVEL').click()
  await expect(page.getByTestId('secondary-category-tab-TRAVEL')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('住宿')).toBeVisible()
  await expect(page.getByText('分类覆盖率', { exact: true })).toHaveCount(0)
  await expect(page.getByText('数据覆盖', { exact: true })).toHaveCount(0)
  await expect(page.getByText('2026年8月消费明细')).toBeVisible()
  const monthlyDetailCard = page.getByText('2026年8月消费明细').locator('xpath=ancestor::section')
  const sectionTexts = await page.locator('section').allTextContents()
  expect(sectionTexts.findIndex(text => text.includes('2026年8月消费明细'))).toBeLessThan(
    sectionTexts.findIndex(text => text.includes('2026年8月消费结构')),
  )
  await expect(monthlyDetailCard.getByRole('columnheader', { name: '消费明细' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '消费名称' })).toHaveCount(0)
  await expect(monthlyDetailCard.getByText('原始账单描述：月度房租')).toBeVisible()
  await expect(monthlyDetailCard.getByText('招行借记卡 ****').first()).toBeVisible()
  await expect(monthlyDetailCard.getByText('共 2 条，按金额从高到低排列')).toBeVisible()
  await expect(monthlyDetailCard.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/api/consumption/events/export.csv?month=2026-08')
  await monthlyDetailCard.getByLabel('一级分类 event-aug-rent').selectOption('DAILY')
  await expect(monthlyDetailCard.getByLabel('二级分类 event-aug-rent')).toHaveValue('')
  await monthlyDetailCard.getByLabel('二级分类 event-aug-rent').selectOption('SHOPPING')
  await expect(monthlyDetailCard.getByLabel('保存分类 event-aug-rent')).toHaveCount(0)
  await expect(monthlyDetailCard.getByText('保存中…')).toBeVisible()
  await expect(monthlyDetailCard.getByText('已保存')).toBeVisible()

  await page.getByTestId('trend-bar-daily_cny-2026-08-01').hover()
  await expect(page.getByText('总消费：', { exact: false })).toBeVisible()
  await page.getByTestId('trend-bar-daily_cny-2026-07-01').click()
  await expect(page.getByText('本月消费 · 2026年7月')).toBeVisible()
  const julyDetailCard = page.getByText('2026年7月消费明细').locator('xpath=ancestor::section')
  await expect(page.getByText('¥18,600')).toBeVisible()
  await expect(page.getByText('月均 ¥1,550')).toBeVisible()
  await expect(page.getByText('¥17,400')).toHaveCount(0)
  await expect(page.getByText('+5.3%')).toBeVisible()
  await expect(page.getByText('较6月 · ¥1,900')).toBeVisible()
  await expect(page.getByText('2026年7月消费结构')).toBeVisible()
  await expect(page.getByText('近12个月消费结构', { exact: true })).toBeVisible()
  await expect(page.getByTestId('secondary-category-tab-DAILY')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('餐饮').first()).toBeVisible()
  await expect(page.getByText('住宿')).toHaveCount(0)
  await expect(julyDetailCard.getByText('原始账单描述：餐饮')).toBeVisible()
  await expect(julyDetailCard.getByText('房租')).toHaveCount(0)
  await expect(page.getByTestId('trend-month-7月')).toHaveAttribute('fill', '#1D4ED8')
  await expect(page.getByTestId('trend-month-7月')).toHaveAttribute('font-weight', '700')
  await page.getByTestId('rolling-structure-segment-daily_cny').hover()
  await expect(page.getByTestId('rolling-structure-segment-tooltip')).toContainText('¥9,300')

  await page.getByTestId('trend-bar-daily_cny-2026-06-01').click()
  await expect(page.getByText('本月消费 · 2026年6月')).toBeVisible()
  await expect(page.getByText('¥18,600')).toBeVisible()
  await expect(page.getByText('月均 ¥1,550')).toBeVisible()
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

test('hides the candidate review module when the selected month has no candidates', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: analyticsResponse }))
  await page.route('**/api/consumption/candidates*', route => {
    const month = new URL(route.request().url()).searchParams.get('month')
    return route.fulfill({ json: month === '2026-07'
      ? {
          month: '2026-07-01', total: 1, limit: 100, offset: 0,
          items: [{ event_id: 'candidate-july', analytics_effective_date: '2026-07-16', raw_description: '用途待确认', account_display_name: 'CMB Debit ****', source_label: 'CMB Debit', amount_cny: '88', currency: 'CNY' }],
        }
      : { month: `${month}-01`, total: 0, limit: 100, offset: 0, items: [] },
    })
  })
  await page.goto('/#/consumption')

  await expect(page.getByText('消费候选待确认', { exact: true })).toHaveCount(0)
  await page.getByTestId('trend-bar-daily_cny-2026-07-01').click()
  await expect(page.getByText('消费候选待确认', { exact: true })).toBeVisible()
  await expect(page.getByTestId('consumption-candidate-candidate-july')).toBeVisible()
  await page.getByTestId('trend-bar-daily_cny-2026-08-01').click()
  await expect(page.getByText('消费候选待确认', { exact: true })).toHaveCount(0)
})

test('aggregates rolling secondary breakdowns from the same twelve-month window', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: completeSecondaryResponse }))
  await page.goto('/#/consumption')

  const rollingCard = page.getByText('近12个月消费结构', { exact: true }).locator('xpath=ancestor::section')
  await expect(rollingCard).toContainText('¥9,300')
  await expect(rollingCard).toContainText('¥1,860')
  await expect(rollingCard).toContainText('¥3,720')
  await expect(rollingCard).toContainText('餐饮')
  await expect(rollingCard).toContainText('¥9,300100.0%')

  await rollingCard.getByTestId('rolling-secondary-category-tab-HOUSING').click()
  await expect(rollingCard).toContainText('房租')
  await expect(rollingCard).toContainText('¥1,860100.0%')

  await rollingCard.getByTestId('rolling-secondary-category-tab-TRAVEL').click()
  await expect(rollingCard).toContainText('住宿')
  await expect(rollingCard).toContainText('¥3,720100.0%')
})

test('keeps rolling transaction details anchored to the latest twelve-month window', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: analyticsResponse }))
  await page.goto('/#/consumption')

  const rollingCard = page.getByText('近12个月消费明细', { exact: true }).locator('xpath=ancestor::section')
  await expect(rollingCard).toContainText('统计区间：2025年9月 – 2026年8月')
  await expect(rollingCard.getByText('原始账单描述：月度房租')).toBeVisible()
  await expect(rollingCard.getByText('原始账单描述：餐饮')).toBeVisible()
  await expect(rollingCard.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/api/consumption/events/export.csv?start_month=2025-09&end_month=2026-08')

  await page.getByTestId('trend-bar-daily_cny-2026-07-01').click()
  await expect(page.getByText('2026年7月消费明细')).toBeVisible()
  await expect(rollingCard.getByText('原始账单描述：月度房租')).toBeVisible()
  await rollingCard.getByLabel('近12个月明细分类状态').selectOption('NEEDS_REVIEW')
  await expect(rollingCard.getByText('原始账单描述：待确认交易')).toBeVisible()
  await expect(rollingCard.getByText('原始账单描述：月度房租')).toHaveCount(0)
  await rollingCard.getByLabel('一级分类 event-aug-unclassified').selectOption('DAILY')
  await rollingCard.getByLabel('二级分类 event-aug-unclassified').selectOption('SHOPPING')
  await expect(rollingCard.getByText('原始账单描述：待确认交易')).toHaveCount(0)
})

test('autosaves one event-scoped note across monthly and rolling transaction details', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: analyticsResponse }))
  await page.goto('/#/consumption')

  const monthlyCard = page.getByText('2026年8月消费明细').locator('xpath=ancestor::section')
  const rollingCard = page.getByText('近12个月消费明细', { exact: true }).locator('xpath=ancestor::section')
  await expect(monthlyCard.getByRole('columnheader', { name: '备注' })).toBeVisible()
  await monthlyCard.getByLabel('备注 event-aug-rent').click()
  const monthlyInput = monthlyCard.locator('input[aria-label="备注 event-aug-rent"]')
  await expect(monthlyInput).toBeVisible()
  await monthlyInput.fill('9月房租')
  await monthlyInput.press('Enter')
  await expect(monthlyCard.getByText('已保存')).toBeVisible()
  await expect(rollingCard.getByText('9月房租')).toBeVisible()

  await rollingCard.getByLabel('备注 event-aug-rent').click()
  const rollingInput = rollingCard.locator('input[aria-label="备注 event-aug-rent"]')
  await expect(rollingInput).toBeVisible()
  await rollingInput.fill('搬家相关支出')
  await rollingInput.press('Enter')
  await expect(monthlyCard.getByText('搬家相关支出')).toBeVisible()

  await monthlyCard.getByLabel('备注 event-aug-rent').click()
  await expect(monthlyInput).toBeVisible()
  await monthlyInput.fill('')
  await monthlyInput.press('Enter')
  await expect(rollingCard.getByLabel('备注 event-aug-rent')).toHaveText('添加备注')
})

test('rolls the structure window forward when analytics receives a new latest month', async ({ page }) => {
  await mockDemo(page)
  const shiftedMonths = [
    '2025-10-01', '2025-11-01', '2025-12-01', '2026-01-01', '2026-02-01', '2026-03-01',
    '2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01', '2026-09-01',
  ]
  await page.route('**/api/consumption/analytics*', route => route.fulfill({
    json: { ...analyticsResponse, months: analyticsResponse.months.map((item, index) => ({ ...item, month: shiftedMonths[index] })) },
  }))
  await page.goto('/#/consumption')

  await expect(page.getByText('近12个月消费结构', { exact: true })).toBeVisible()
  await expect(page.getByText('统计区间：2025年10月 – 2026年9月').last()).toBeVisible()
})

test('keeps an ultra-narrow primary segment hoverable', async ({ page }) => {
  await mockDemo(page)
  const narrowAugust = {
    ...analyticsResponse.months.at(-1),
    daily_cny: '1048',
    housing_cny: '210',
    travel_cny: '840',
    unclassified_eligible_cny: '2',
  }
  const narrowResponse = {
    ...analyticsResponse,
    months: [...analyticsResponse.months.slice(0, -1), narrowAugust],
  }
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: narrowResponse }))
  await page.goto('/#/consumption')

  await page.getByTestId('structure-segment-unclassified_eligible_cny').hover()
  await expect(page.getByTestId('structure-segment-tooltip')).toContainText('待分类')
  await expect(page.getByTestId('structure-segment-tooltip')).toContainText('¥2')
  await expect(page.getByTestId('structure-segment-tooltip')).toContainText('0.1%')
})

test('filters monthly details server-side and removes an autosaved review row', async ({ page }) => {
  await mockDemo(page)
  await page.route('**/api/consumption/analytics*', route => route.fulfill({ json: analyticsResponse }))
  await page.goto('/#/consumption')
  const monthlyDetailCard = page.getByText('2026年8月消费明细').locator('xpath=ancestor::section')

  await monthlyDetailCard.getByLabel('分类状态筛选').selectOption('NEEDS_REVIEW')
  await expect(monthlyDetailCard.getByLabel('一级分类筛选')).toBeDisabled()
  await expect(monthlyDetailCard.getByLabel('二级分类筛选')).toBeDisabled()
  await expect(monthlyDetailCard.getByText('原始账单描述：待确认交易')).toBeVisible()
  await expect(monthlyDetailCard.getByText('原始账单描述：月度房租')).toHaveCount(0)
  await expect(monthlyDetailCard.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/api/consumption/events/export.csv?month=2026-08&classification_status=NEEDS_REVIEW')

  await monthlyDetailCard.getByLabel('一级分类 event-aug-unclassified').selectOption('DAILY')
  await monthlyDetailCard.getByLabel('二级分类 event-aug-unclassified').selectOption('SHOPPING')
  await expect(monthlyDetailCard.getByText('原始账单描述：待确认交易')).toHaveCount(0)
  await expect(monthlyDetailCard.getByText('当前筛选条件下暂无消费明细')).toBeVisible()

  await monthlyDetailCard.getByLabel('分类状态筛选').selectOption('CLASSIFIED')
  await monthlyDetailCard.getByLabel('一级分类筛选').selectOption('HOUSING')
  await monthlyDetailCard.getByLabel('二级分类筛选').selectOption('RENT')
  await expect(monthlyDetailCard.getByText('原始账单描述：月度房租')).toBeVisible()
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
