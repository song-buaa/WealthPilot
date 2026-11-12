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
