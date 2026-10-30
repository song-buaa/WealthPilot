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
