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
