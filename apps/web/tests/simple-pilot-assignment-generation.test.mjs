import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getMissingSimplePilotGeneratedTaskTypes,
  getSimplePilotGeneratedTaskRepairTypes,
  isCompleteSimplePilotGeneratedTaskSet,
  repairSimplePilotGeneratedTaskSet,
  simplePilotQuestionBankMetadataMatches,
} from '../lib/simple-pilot-assignments.ts'

test('repairs a partial AI response by requesting only the missing short-answer type', async () => {
  const tasks = [
    { assignment_type: 'QUIZ' },
    { assignment_type: 'FORM' },
  ]

  assert.deepEqual(getSimplePilotGeneratedTaskRepairTypes(tasks), ['SHORT_ANSWER'])
  assert.equal(isCompleteSimplePilotGeneratedTaskSet(tasks), false)

  const repairCalls = []
  const repairedTasks = await repairSimplePilotGeneratedTaskSet(tasks, async (taskTypes) => {
    repairCalls.push(taskTypes)
    return [{ assignment_type: 'SHORT_ANSWER' }]
  })

  assert.deepEqual(repairCalls, [['SHORT_ANSWER']])
  assert.deepEqual(getMissingSimplePilotGeneratedTaskTypes(repairedTasks), [])
  assert.equal(isCompleteSimplePilotGeneratedTaskSet(repairedTasks), true)
})

test('does not accept three generated tasks when one required type is duplicated', () => {
  const tasks = [
    { assignment_type: 'QUIZ' },
    { assignment_type: 'FORM' },
    { assignment_type: 'FORM' },
  ]

  assert.deepEqual(getSimplePilotGeneratedTaskRepairTypes(tasks), [])
  assert.deepEqual(getMissingSimplePilotGeneratedTaskTypes(tasks), ['SHORT_ANSWER'])
  assert.equal(isCompleteSimplePilotGeneratedTaskSet(tasks), false)
})

test('does not call the repair generator when the initial response already has three tasks', async () => {
  const tasks = [
    { assignment_type: 'QUIZ' },
    { assignment_type: 'FORM' },
    { assignment_type: 'SHORT_ANSWER' },
  ]
  let repairCalled = false

  const result = await repairSimplePilotGeneratedTaskSet(tasks, async () => {
    repairCalled = true
    return []
  })

  assert.equal(repairCalled, false)
  assert.equal(result, tasks)
})

test('normalizes enum-shaped assignment types returned by the API', () => {
  const tasks = [
    { assignment_type: { value: 'QUIZ' } },
    { assignment_type: { value: 'FORM' } },
    { assignment_type: { value: 'SHORT_ANSWER' } },
  ]

  assert.equal(isCompleteSimplePilotGeneratedTaskSet(tasks), true)
})

test('allows reusable bank questions with blank school metadata', () => {
  assert.equal(
    simplePilotQuestionBankMetadataMatches(
      { subject: '', grade_level: null, unit: '' },
      { subject: '數學', gradeLevel: '小四', unit: '加法' }
    ),
    true
  )
})

test('keeps explicitly mismatched bank questions out of a quick assignment', () => {
  assert.equal(
    simplePilotQuestionBankMetadataMatches(
      { subject: '英文', grade_level: '小五', unit: '閱讀' },
      { subject: '數學', gradeLevel: '小四', unit: '加法' }
    ),
    false
  )
})
