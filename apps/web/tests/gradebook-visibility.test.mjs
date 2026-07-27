import assert from 'node:assert/strict'
import test from 'node:test'

import { isOperationalGradebookRow } from '../lib/gradebook-visibility.ts'

test('shows targeted assignments and self-tests in the teacher gradebook', () => {
  assert.equal(isOperationalGradebookRow({
    source_type: 'assignment',
    visible_to_students: true,
    target_usergroups_valid: true,
  }), true)
  assert.equal(isOperationalGradebookRow({ source_type: 'self_test' }), true)
})

test('hides legacy assignment rows that students cannot act on', () => {
  assert.equal(isOperationalGradebookRow({
    source_type: 'assignment',
    visible_to_students: true,
    target_usergroups_valid: false,
  }), false)
  assert.equal(isOperationalGradebookRow({
    source_type: 'assignment',
    visible_to_students: false,
    target_usergroups_valid: true,
  }), false)
  assert.equal(isOperationalGradebookRow(null), false)
})
