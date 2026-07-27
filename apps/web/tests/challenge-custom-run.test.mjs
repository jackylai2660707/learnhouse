import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createProgrammingActionCoordinator,
  learnerResultId,
  runChallengeWithLearnerTests,
} from '../components/Objects/Editor/Extensions/CodePlayground/challenge-run.ts'

function jsonResponse(payload, ok = true) {
  return {
    ok,
    async json() {
      return payload
    },
  }
}

test('challenge Run combines server-owned visible checks with learner scratch checks', async () => {
  const calls = []
  const results = await runChallengeWithLearnerTests({
    apiUrl: 'https://school.invalid/api/v1/',
    challengeUuid: 'challenge/a b',
    accessToken: 'test-token',
    languageId: 71,
    sourceCode: 'print(input())',
    learnerTests: [
      { id: 'mine', label: '邊界資料', stdin: '澳門', expectedStdout: '澳門' },
    ],
    additionalFiles: [{ name: 'data.txt', content: 'safe' }],
    fetchImpl: async (url, init) => {
      calls.push({ url, init })
      return calls.length === 1
        ? jsonResponse({ results: [{ id: 'visible', label: '可見測試', passed: true }] })
        : jsonResponse({ results: [{ id: learnerResultId('mine'), label: '邊界資料', passed: true }] })
    },
  })

  assert.equal(calls.length, 2)
  assert.equal(
    calls[0].url,
    'https://school.invalid/api/v1/coding-challenges/challenge%2Fa%20b/run'
  )
  assert.equal(calls[1].url, 'https://school.invalid/api/v1/code/execute-batch')
  assert.deepEqual(JSON.parse(calls[0].init.body), { source_code: 'print(input())' })
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    language_id: 71,
    source_code: 'print(input())',
    test_cases: [{
      id: learnerResultId('mine'),
      label: '邊界資料',
      stdin: '澳門',
      expected_stdout: '澳門',
    }],
    additional_files: [{ name: 'data.txt', content: 'safe' }],
  })
  assert.deepEqual(results.map((result) => result.id), ['visible', learnerResultId('mine')])
  assert.equal(calls.some((call) => call.url.includes('/submit')), false)
})

test('challenge Run does not call generic execution when no learner checks exist', async () => {
  const calls = []
  const results = await runChallengeWithLearnerTests({
    apiUrl: '/api/v1/',
    challengeUuid: 'challenge_safe',
    accessToken: 'test-token',
    languageId: 63,
    sourceCode: 'console.log(1)',
    learnerTests: [],
    fetchImpl: async (url, init) => {
      calls.push({ url, init })
      return jsonResponse({ results: [{ id: 'visible', passed: true }] })
    },
  })

  assert.equal(calls.length, 1)
  assert.deepEqual(results.map((result) => result.id), ['visible'])
})

test('challenge Run rejects sanitized failures without reading provider bodies', async () => {
  let bodyRead = false
  await assert.rejects(
    runChallengeWithLearnerTests({
      apiUrl: '/api/v1/',
      challengeUuid: 'challenge_safe',
      accessToken: 'test-token',
      languageId: 71,
      sourceCode: 'print(1)',
      learnerTests: [],
      fetchImpl: async () => ({
        ok: false,
        async json() {
          bodyRead = true
          return { detail: 'raw provider body' }
        },
      }),
    }),
    /challenge_visible_run_failed/
  )
  assert.equal(bodyRead, false)
})

test('programming actions are single-flight across clicks and keyboard shortcuts', () => {
  const coordinator = createProgrammingActionCoordinator()

  assert.equal(coordinator.tryStart('run'), true)
  assert.equal(coordinator.tryStart('run'), false, 'two synchronous Run attempts')
  assert.equal(coordinator.tryStart('submit'), false, 'Run then Submit')
  assert.equal(coordinator.isBusy(), true)

  coordinator.finish('run')
  assert.equal(coordinator.tryStart('submit'), true)
  assert.equal(coordinator.tryStart('run'), false, 'Submit then keyboard Run')

  coordinator.finish('run')
  assert.equal(coordinator.isBusy(), true, 'a stale action cannot release another action')
  coordinator.finish('submit')
  assert.equal(coordinator.isBusy(), false)
  assert.equal(coordinator.tryStart('run'), true, 'release permits the next action')
})
