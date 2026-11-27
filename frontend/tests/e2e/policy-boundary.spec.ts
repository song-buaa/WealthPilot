/** Presentation-only fixtures. Backend policy qualification is tested separately. */
import { createServer, type Server } from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
let server: Server
let base: string
const dist = fileURLToPath(new URL('../../dist/', import.meta.url))
test.beforeAll(async () => {
  server=createServer(async (req,res) => {
    try {
      const path=(req.url ?? '/').split('?')[0]
      if(path.includes('..')) { res.writeHead(404).end();return }
      const file=path==='/' ? 'index.html' : path.slice(1)
      const data=await readFile(dist+file)
      res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html')
      res.end(data)
    } catch { res.writeHead(404).end() }
  })
  await new Promise<void>(resolve => server.listen(0,'127.0.0.1',resolve))
  const address=server.address()
  if(!address || typeof address==='string')throw new Error('TEST_SERVER_UNAVAILABLE')
  base=`http://127.0.0.1:${address.port}`
})
test.afterAll(() => server?.close())

test('ACTIVE allocation displays qualified gap then expires without writes', async ({page}) => {
  let writes = 0
  const stamp = new Date().toISOString()
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') writes++
    const path = new URL(route.request().url()).pathname
    if (path.includes('policy-context')) {
      await route.fulfill({json:{mode:'POLICY_ACTIVE',status:'ACTIVE',bundle_sha:'f'.repeat(64),
        targets:{neutral:{cash:'.1'},etf_internal:{},direct_equity:{},growth_total:'.65',stable_total:'.35'}}})
    } else if (path === '/api/allocation/snapshot') {
      await route.fulfill({json:{policy_allocation:{status:'QUALIFIED_MAJOR_CLASSES',snapshot_id:'asset-view:v1:1',
        snapshot_sha256:'a'.repeat(64),as_of:stamp,expires_at:new Date(Date.now()+5000).toISOString(),
        denominator_cny:'1000',policy_reference:{bundle_sha256:'f'.repeat(64)},
        classes:{cash:{amount_cny:'1000',current_ratio:'1',target_ratio:'.1',gap_pp:'90',amount_gap_cny:'900',direction:'OVER'}},
        focus:['cash'],execution_authorized:false}}})
    } else await route.fulfill({json:path.endsWith('/summary')?null:{}})
  })
  await page.goto(base+'/#/discipline')
  await expect(page.getByRole('region',{name:'当前与目标配置'})).toContainText('超配')
  await expect(page.getByRole('region',{name:'当前与目标配置'})).toHaveCount(0,{timeout:10000})
  await expect(page.getByRole('alert')).toContainText('ASSET_VIEW_EXPIRED')
  expect(writes).toBe(0)
})
for(const mode of ['LEGACY','POLICY_ACTIVE','UNAVAILABLE']) {
  test(`policy ${mode}: legacy editor exposure and zero writes`,async ({page}) => {
    let writes=0
    const legacyReads: string[]=[]
    await page.route('**/api/**',async route => {
      const path=new URL(route.request().url()).pathname
      if(route.request().method()!=='GET')writes++
      if(path.includes('policy-context')) {
        await route.fulfill({json:{mode,status:mode==='POLICY_ACTIVE'?'ACTIVE':'NOT_ACTIVE',synthetic:true,
          bundle_id:'SYNTHETIC_BROWSER',bundle_version:1,bundle_sha:'f'.repeat(64),
          source_hashes:{philosophy:'a'.repeat(64),policy:'b'.repeat(64)},
          risk_diagnostic:{status:'UNKNOWN',asset_issues:['SYNTHETIC_MISSING_DENOMINATOR']},
          targets:{neutral:{cash:'0',us_etf:'.25'},growth_total:'.65',stable_total:'.35',etf_internal:{AAA:'.2'},direct_equity:{SYN1:'.03'}},
          rules:[],philosophy:{text:'SYNTHETIC BROWSER PHILOSOPHY'}}});return
      }
      if(path.includes('/discipline/'))legacyReads.push(path)
      await route.fulfill({json:path.endsWith('/handbook')?{source:'synthetic',content:'SYNTHETIC LEGACY'}:path.endsWith('/summary')?null:path.endsWith('/alerts')?{items:[]}:{}})
    })
    await page.goto(base+'/#/discipline')
    if(mode==='LEGACY') {
      await expect(page.getByRole('button',{name:'编辑',exact:true})).toBeVisible()
      expect(legacyReads.length).toBeGreaterThan(0)
    } else {
      await expect(page.getByRole('button',{name:'编辑',exact:true})).toHaveCount(0)
      expect(legacyReads).toEqual([])
      if(mode==='POLICY_ACTIVE') {
        await expect(page.getByRole('heading',{name:/TEST ONLY/})).toBeVisible()
        await expect(page.getByRole('status')).toContainText('UNKNOWN')
      } else await expect(page.getByRole('alert')).toContainText('政策状态不可核验')
    }
    expect(writes).toBe(0)
  })
}

for (const fixture of ['QUALIFIED', 'PARTIAL', 'OLD_HIERARCHY', 'FORMAL_V2', 'MISMATCHED_V2']) {
  test(`ACTIVE hierarchy renders ${fixture} with the appropriate review qualification`, async ({ page }) => {
    const status = fixture === 'PARTIAL' ? 'PARTIAL' : 'QUALIFIED'
    const gap = { amount_cny: '760', current_ratio: '.76', target_ratio: '.70', target_kind: 'NEUTRAL_POLICY',
      gap_pp: '6', amount_gap_cny: '60' }
    const partial = { ...gap, amount_cny: null, current_ratio: null, gap_pp: null, amount_gap_cny: null }
    const v2 = fixture === 'FORMAL_V2' || fixture === 'MISMATCHED_V2'
    const ref = { bundle_id: 'fixture-bundle', bundle_version: 2, bundle_sha256: 'f'.repeat(64),
      structured_sha256: 'e'.repeat(64), scope: 'portfolio:7' }
    const writes: string[] = []
    await page.route('**/api/**', async route => {
      if (route.request().method() !== 'GET') writes.push(route.request().url())
      const path = new URL(route.request().url()).pathname
      if (path.includes('policy-context')) {
        await route.fulfill({ json: { mode: 'POLICY_ACTIVE', status: 'ACTIVE', bundle_sha: 'f'.repeat(64),
          targets: { neutral: { cash: '0' }, etf_internal: {}, direct_equity: {}, growth_total: '.7', stable_total: '.3' } } })
      } else if (path === '/api/allocation/snapshot') {
        await route.fulfill({ json: { policy_allocation: {
          status: 'QUALIFIED_MAJOR_CLASSES', snapshot_id: 'asset-view:v1:1', snapshot_sha256: 'a'.repeat(64),
          as_of: new Date().toISOString(), expires_at: new Date(Date.now() + 30_000).toISOString(),
          denominator_cny: '1000', policy_reference: v2 ? ref : { bundle_sha256: 'f'.repeat(64) },
          classes: { equity: { ...gap, direction: 'OVER' } }, focus: ['equity'], execution_authorized: false,
          hierarchy: {
            status, strategic: { growth_and_alternatives: status === 'QUALIFIED' ? gap : partial },
            secondary: { us_etf: status === 'QUALIFIED' ? gap : partial },
            direct_equity: { status, issuers: { Alphabet: status === 'QUALIFIED' ? gap : partial },
              unallocated: { status: 'UNASSIGNED', target_ratio: '.03' } },
            etf: { status: status === 'QUALIFIED' ? 'QUALIFIED' : 'UNKNOWN', products: {
              SPY: { ...(status === 'QUALIFIED' ? gap : partial),
                current_ratio_of_d: status === 'QUALIFIED' ? '.12' : null, target_ratio_of_d: '.09' },
            } },
            strategic_review: v2
              ? { status: 'HUMAN_REVIEW', threshold_pp: '5', policy_schema_version: 'wp.policy.v2',
                  policy_reference: ref, snapshot_sha256: (fixture === 'FORMAL_V2' ? 'a' : 'b').repeat(64),
                  review_id: 'c'.repeat(64), triggered_classes: ['growth_and_alternatives', 'stable_pool'] }
              : fixture === 'OLD_HIERARCHY'
              ? { status: 'HUMAN_REVIEW', threshold_pp: '5', triggered_classes: ['growth_and_alternatives'] }
              : { status: 'RULE_SCOPE_UNVERIFIED', threshold_pp: '5', triggered_classes: [],
                  candidate_hit_classes: status === 'QUALIFIED' ? ['growth_and_alternatives'] : null },
            unclassified_asset_refs: ['position:99'], unclassified_amount_cny: '15',
          },
        } } })
      } else await route.fulfill({ json: path.endsWith('/summary') ? null : {} })
    })
    await page.goto(base + '/#/discipline')
    const hierarchy = page.getByRole('region', { name: 'ACTIVE 分层配置' })
    await expect(hierarchy.getByRole('table', { name: 'ETF 产品占 D' })).toContainText('9.00%')
    if (fixture === 'FORMAL_V2') {
      await expect(hierarchy.getByText('一级战略配置偏差严格超过政策阈值，请复核一次组合配置。')).toHaveCount(1)
      await expect(hierarchy).not.toContainText('RULE_SCOPE_UNVERIFIED')
    } else {
      await expect(hierarchy).toContainText('RULE_SCOPE_UNVERIFIED')
      await expect(hierarchy).not.toContainText('请复核一次组合配置')
    }
    await expect(hierarchy.getByRole('table', { name: '美国核心直接股（占 D）' })).toContainText('Alphabet')
    await expect(hierarchy).toContainText('未指定自主选股预算 3.00% · 待研究')
    await expect(hierarchy).not.toContainText('人工专项配置复核')
    await expect(hierarchy.getByRole('table', { name: 'ETF 产品占 D' })).toContainText('中性政策目标')
    if (fixture === 'QUALIFIED') {
      await expect(hierarchy).toContainText('候选偏差超过 5 个百分点')
    } else {
      await expect(hierarchy).not.toContainText('候选偏差超过')
    }
    if (status === 'QUALIFIED') {
      await expect(hierarchy.getByRole('table', { name: 'ETF 产品占 D' })).toContainText('12.00%')
    } else {
      await expect(hierarchy).toContainText('position:99')
      await expect(hierarchy.getByRole('table', { name: '一级战略配置（占 D）' })).not.toContainText('76.00%')
    }
    expect(writes).toEqual([])
  })
}


test('ACTIVE concentration retains an 8% lower-bound review with missing upper bound', async ({ page }) => {
  let writes = 0
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') writes++
    const path = new URL(route.request().url()).pathname
    if (path.includes('policy-context')) {
      await route.fulfill({ json: { mode: 'POLICY_ACTIVE', status: 'ACTIVE', bundle_sha: 'f'.repeat(64),
        targets: { neutral: { cash: '0' }, etf_internal: {}, direct_equity: {}, growth_total: '.7', stable_total: '.3' } } })
    } else if (path === '/api/allocation/snapshot') {
      await route.fulfill({ json: { policy_allocation: {
        status: 'QUALIFIED_MAJOR_CLASSES', snapshot_id: 'asset-view:v1:1', snapshot_sha256: 'a'.repeat(64),
        as_of: new Date().toISOString(), expires_at: new Date(Date.now() + 30_000).toISOString(),
        denominator_cny: '1000', policy_reference: { bundle_sha256: 'f'.repeat(64) },
        classes: {}, focus: [], execution_authorized: false,
        concentration: { status: 'DIAGNOSTIC', expires_at: new Date(Date.now() + 3000).toISOString(), unknown_coverage: true, issues: ['ETF_LOOKTHROUGH_INCOMPLETE'],
          issuers: { 'fixture-alphabet': { issuer_name: 'Alphabet Inc.', direct_pct: '4', indirect_known_pct: '4',
            known_lower_bound_pct: '8', proven_upper_bound_pct: null, risk_status: 'HUMAN_REVIEW' } } },
      } } })
    } else await route.fulfill({ json: path.endsWith('/summary') ? null : {} })
  })
  await page.goto(base + '/#/discipline')
  const risk = page.getByRole('region', { name: '发行人集中度' })
  await expect(risk).toContainText('缺少上界时无法判定安全')
  const alpha = risk.getByRole('row', { name: /Alphabet Inc/ })
  await expect(alpha).toContainText('8.00%')
  await expect(alpha).toContainText('—')
  await expect(alpha).toContainText('HUMAN_REVIEW')
  await expect(risk).toContainText('发行人集中度证据已到期')
  await expect(alpha).toHaveCount(0)
  await expect(page.getByRole('region', { name: '当前与目标配置' })).toBeVisible()
  expect(writes).toBe(0)
})


test('ETF observation expires independently and preserves formal C and B', async ({ page }) => {
  const sha = 'f'.repeat(64)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.includes('policy-context')) {
      await route.fulfill({ json: { mode: 'POLICY_ACTIVE', status: 'ACTIVE', bundle_sha: sha,
        targets: { neutral: { cash: '0' }, etf_internal: {}, direct_equity: {} } } })
    } else if (path === '/api/allocation/snapshot') {
      const source = { status: 'OBSERVED', holdings_as_of: '2026-11-24', lag_sessions: 5,
        expires_at: new Date(Date.now() + 3000).toISOString(), mapped_weight_pct: '35.1',
        unmapped_equity_weight_pct: '64.9' }
      await route.fulfill({ json: { policy_allocation: {
        status: 'QUALIFIED_MAJOR_CLASSES', as_of: new Date().toISOString(), snapshot_id: 'asset-view:v1:1',
        expires_at: new Date(Date.now() + 30_000).toISOString(), denominator_cny: '1000',
        policy_reference: { bundle_sha256: sha }, classes: {}, focus: [],
        concentration: { status: 'DIAGNOSTIC', issues: [], issuers: {
          alphabet: { issuer_name: 'Alphabet', direct_pct: '4', indirect_known_pct: '4',
            known_lower_bound_pct: '8', proven_upper_bound_pct: null, risk_status: 'HUMAN_REVIEW' },
        }, etf_observation: {
          status: 'OBSERVATION_ONLY', latest_completed_session: '2026-12-02', max_lag_sessions: 5,
          sources: { 'SPY:US': { ...source, expires_at: null }, 'QQQ:US': source,
            'SPMO:US': { ...source, status: 'UNAVAILABLE', expires_at: null } },
          issuers: { alphabet: { issuer_name: 'Alphabet', direct_cny: '40',
            indirect_observed_cny: { 'SPY:US': '15', 'QQQ:US': '12', 'SPMO:US': null },
            available_observed_cny: '67' } },
        } },
      } } })
    } else await route.fulfill({ json: path.endsWith('/summary') ? null : {} })
  })
  await page.goto(base + '/#/discipline')
  const observations = page.getByRole('region', { name: 'ETF 观察性敞口' })
  await expect(observations.getByRole('row', { name: /Alphabet/ })).toContainText('未知')
  await expect(observations).toContainText('2026-11-24')
  await expect(observations.getByRole('row', { name: /Alphabet/ })).toContainText('已到期')
  await expect(page.getByRole('region', { name: '发行人集中度' })).toContainText('HUMAN_REVIEW')
  await expect(page.getByRole('region', { name: '当前与目标配置' })).toBeVisible()
})

test('409 shows only conditional ETF amounts from the pinned policy', async ({ page }) => {
  const sha = 'f'.repeat(64)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.includes('policy-context')) {
      await route.fulfill({ json: { mode: 'POLICY_ACTIVE', status: 'ACTIVE', bundle_sha: sha,
        targets: { neutral: { cash: '0' }, etf_internal: {}, direct_equity: {} } } })
    } else if (path === '/api/allocation/snapshot') {
      const source = { status: 'UNAVAILABLE', holdings_as_of: null, lag_sessions: null, expires_at: null,
        mapped_weight_pct: null, unmapped_equity_weight_pct: null }
      await route.fulfill({ status: 409, json: { detail: 'POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED',
        qualification: { concentration: { policy_reference: { bundle_sha256: sha }, etf_observation: {
          status: 'CONDITIONAL', snapshot_at: new Date().toISOString(),
          latest_completed_session: '2026-11-25', max_lag_sessions: 5,
          sources: { 'SPY:US': source, 'QQQ:US': source, 'SPMO:US': source }, issuers: {},
        } } },
      } })
    } else await route.fulfill({ json: path.endsWith('/summary') ? null : {} })
  })
  await page.goto(base + '/#/discipline')
  await expect(page.getByRole('alert')).toContainText('当前配置资格 UNKNOWN')
  await expect(page.getByRole('region', { name: 'ETF 观察性敞口' })).toContainText('条件性金额')
  await expect(page.getByRole('region', { name: '发行人集中度' })).toHaveCount(0)
})

test('D previews both paths from a frozen 409 baseline without other writes', async ({ page }) => {
  const writes: string[] = []
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') writes.push(path)
    if (path.includes('policy-context')) {
      await route.fulfill({ json: { mode: 'POLICY_ACTIVE', status: 'ACTIVE', bundle_sha: 'f'.repeat(64),
        targets: { neutral: {}, etf_internal: {}, direct_equity: {}, growth_total: '.7', stable_total: '.3' } } })
    } else if (path === '/api/allocation/snapshot') {
      await route.fulfill({ status: 409, json: { detail: 'POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED',
        qualification: { snapshot_id: 'asset-view:v1:1', snapshot_sha256: 'a'.repeat(64), issues: [] } } })
    } else if (path === '/api/allocation/simulate-transition') {
      const body = route.request().postDataJSON()
      expect(body.snapshot_sha256).toBe('a'.repeat(64))
      expect(body.policy_bundle_sha256).toBe('f'.repeat(64))
      expect(Object.keys(body.paths)).toEqual(['A', 'B'])
      const stage = { label: 'S1', investment_assets_cny: null, liabilities_cny: '150',
        newly_breached_issuers: [], allocation: {}, concentration: {}, cash: {}, actions: [] }
      await route.fulfill({ json: { basis: 'ILLUSTRATIVE_ASSUMPTION', paths: {
        A: [stage], B: [{ ...stage, cash: { 'action:sale': {
          account: 'account_fixture', currency: 'USD', amount: '100', state: 'UNSETTLED' } } }],
      } } })
    } else await route.fulfill({ json: path.endsWith('/summary') ? null : {} })
  })
  await page.goto(base + '/#/discipline')
  await page.getByText('多阶段资产修复模拟', { exact: true }).click()
  await page.getByRole('button', { name: '载入可编辑的 A/B 示例' }).click()
  await page.getByRole('button', { name: '运行模拟' }).click()
  await expect(page.getByText('假设基准 · 研究结果')).toBeVisible()
  await expect(page.getByRole('heading', { name: '路径 A' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '路径 B' })).toBeVisible()
  await expect(page.getByText(/action:sale.*UNSETTLED/)).toBeVisible()
  expect(writes).toEqual(['/api/allocation/simulate-transition'])
})
