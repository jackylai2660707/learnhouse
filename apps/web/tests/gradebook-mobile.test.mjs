import assert from 'node:assert/strict'
import test from 'node:test'

import { gradebookMobileRecordLabel } from '../lib/gradebook-mobile.ts'

test('builds an accessible label for a mobile gradebook record', () => {
  assert.equal(
    gradebookMobileRecordLabel({ student_name: '陳小明', assignment_title: '分數練習' }),
    '陳小明：分數練習'
  )
})

test('uses safe Traditional Chinese fallbacks for incomplete mobile records', () => {
  assert.equal(gradebookMobileRecordLabel({ source_type: 'self_test' }), '未命名學生：自測記錄')
  assert.equal(gradebookMobileRecordLabel(null), '未命名學生：未命名作業')
})
