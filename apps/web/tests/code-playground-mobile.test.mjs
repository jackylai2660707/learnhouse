import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const component = readFileSync(
  new URL('../components/Objects/Editor/Extensions/CodePlayground/CodePlaygroundComponent.tsx', import.meta.url),
  'utf8'
)
const zh = JSON.parse(readFileSync(new URL('../locales/zh.json', import.meta.url), 'utf8'))
const en = JSON.parse(readFileSync(new URL('../locales/en.json', import.meta.url), 'utf8'))

function leafKeys(value, prefix = '') {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return child && typeof child === 'object' ? leafKeys(child, path) : [path]
  })
}

test('mobile playground uses top-level panes and removes fixed horizontal sizing', () => {
  assert.match(component, /mobilePane === 'code'/)
  assert.match(component, /mobilePane === 'details'/)
  assert.match(component, /flex-col overflow-hidden md:h-\[560px\] md:flex-row/)
  assert.match(component, /max-md:!w-full max-md:!min-w-0 max-md:!max-w-none/)
  assert.match(component, /right: 'group max-md:hidden'/)
})

test('run and submit move mobile learners to reachable results', () => {
  const transitions = component.match(/setMobilePane\('details'\)/g) ?? []
  assert.ok(transitions.length >= 4)
  assert.match(component, /overflow-x-auto border-b/)
})

test('Traditional Chinese and English playground locales have matching leaf keys', () => {
  assert.deepEqual(leafKeys(zh.code_playground).sort(), leafKeys(en.code_playground).sort())
  assert.equal(zh.code_playground.actions.submit, '提交答案')
  assert.equal(zh.code_playground.tabs.history, '提交紀錄')
})
