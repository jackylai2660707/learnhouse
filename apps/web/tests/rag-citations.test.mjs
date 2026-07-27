import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  citationLabel,
  parseCitationText,
  sourceActivityPath,
} from '../lib/rag-citations.ts'

test('citation parser emits only bounded exact source labels', () => {
  assert.deepEqual(parseCitationText('答案 [1, 2]，錯誤 [0] [99]。', 2), [
    { type: 'text', value: '答案 ' },
    { type: 'citation', number: 1 },
    { type: 'citation', number: 2 },
    { type: 'text', value: '，錯誤 [0] [99]。' },
  ])
  assert.equal(citationLabel(2), '[2]')
})

test('citation parser bounds work for hostile or oversized labels', () => {
  const oversized = '[1,2,3,4,5,6,7,8,9,10,11]'
  assert.deepEqual(parseCitationText(oversized, 20), [{ type: 'text', value: oversized }])
  assert.deepEqual(parseCitationText('[9999]', 9999), [{ type: 'text', value: '[9999]' }])
})

test('source routes accept only canonical prefixed UUIDs', () => {
  assert.equal(
    sourceActivityPath(
      'course_123e4567-e89b-42d3-a456-426614174000',
      'activity_123e4567-e89b-42d3-a456-426614174001',
    ),
    '/course/123e4567-e89b-42d3-a456-426614174000/activity/123e4567-e89b-42d3-a456-426614174001',
  )
  assert.equal(sourceActivityPath('course_../../admin', 'activity_javascript:alert(1)'), null)
})

test('citation links announce new tabs and prevent opener access', () => {
  const component = readFileSync(
    new URL('../app/orgs/[orgslug]/(withmenu)/copilot/copilot.tsx', import.meta.url),
    'utf8',
  )
  const links = component.match(/<Link[^>]+target="_blank"[^>]+>/g) ?? []

  assert.equal(links.length, 2)
  for (const link of links) {
    assert.match(link, /rel="noopener noreferrer"/)
    assert.match(link, /aria-label=/)
  }
  assert.match(component, /（新分頁）/)
})
