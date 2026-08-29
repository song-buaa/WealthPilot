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
    ['投研观点', '/research'],
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
