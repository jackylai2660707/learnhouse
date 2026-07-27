export type LearnerTestCase = {
  id: string
  label: string
  stdin: string
  expectedStdout: string
}

export type ChallengeRunResult = {
  id: string
  label: string
  passed: boolean
  actual_stdout: string | null
  expected_stdout: string | null
  stderr: string | null
  compile_output: string | null
  status: { id: number; description: string } | null
  time: string | null
  memory: number | null
}

export type ProgrammingAction = 'run' | 'submit'

export type ProgrammingActionCoordinator = {
  // eslint-disable-next-line no-unused-vars
  tryStart: (action: ProgrammingAction) => boolean
  // eslint-disable-next-line no-unused-vars
  finish: (action: ProgrammingAction) => void
  isBusy: () => boolean
}

/**
 * Coordinate Run and Submit synchronously. React state updates are scheduled,
 * so two click/keyboard handlers in the same tick cannot safely use state as
 * a mutex. This tiny coordinator closes that gap without coupling tests to UI.
 */
export function createProgrammingActionCoordinator(): ProgrammingActionCoordinator {
  let activeAction: ProgrammingAction | null = null
  return {
    tryStart(action) {
      if (activeAction !== null) return false
      activeAction = action
      return true
    },
    finish(action) {
      if (activeAction === action) activeAction = null
    },
    isBusy() {
      return activeAction !== null
    },
  }
}

type FetchLike = (
  // eslint-disable-next-line no-unused-vars
  ...args: Parameters<typeof fetch>
) => Promise<Pick<Response, 'ok' | 'json'>>

type RunChallengeWithLearnerTestsOptions = {
  apiUrl: string
  challengeUuid: string
  accessToken: string
  languageId: number
  sourceCode: string
  learnerTests: LearnerTestCase[]
  sqliteDbPath?: string
  additionalFiles?: Array<{ name: string; content: string }>
  fetchImpl?: FetchLike
}

const LEARNER_TEST_PREFIX = 'learner_test:'

function resultsFromPayload(payload: unknown): ChallengeRunResult[] {
  if (!payload || typeof payload !== 'object') return []
  const results = (payload as { results?: unknown }).results
  return Array.isArray(results) ? results as ChallengeRunResult[] : []
}

/**
 * Run a durable challenge's server-owned visible checks and the learner's
 * optional scratch checks. Scratch checks use the generic execution endpoint:
 * they never enter the formal submission/progress path and can never unlock a
 * solution or count as a passing attempt.
 */
export async function runChallengeWithLearnerTests({
  apiUrl,
  challengeUuid,
  accessToken,
  languageId,
  sourceCode,
  learnerTests,
  sqliteDbPath,
  additionalFiles = [],
  fetchImpl = fetch,
}: RunChallengeWithLearnerTestsOptions): Promise<ChallengeRunResult[]> {
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${accessToken}`,
  }
  const visibleResponse = await fetchImpl(
    `${apiUrl}coding-challenges/${encodeURIComponent(challengeUuid)}/run`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({ source_code: sourceCode }),
    }
  )
  if (!visibleResponse.ok) throw new Error('challenge_visible_run_failed')
  const visibleResults = resultsFromPayload(await visibleResponse.json())

  if (learnerTests.length === 0) return visibleResults

  const learnerResponse = await fetchImpl(`${apiUrl}code/execute-batch`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      language_id: languageId,
      source_code: sourceCode,
      test_cases: learnerTests.map((testCase) => ({
        id: `${LEARNER_TEST_PREFIX}${testCase.id}`,
        label: testCase.label,
        stdin: testCase.stdin,
        expected_stdout: testCase.expectedStdout,
      })),
      ...(sqliteDbPath ? { sqlite_db_path: sqliteDbPath } : {}),
      ...(additionalFiles.length > 0 ? { additional_files: additionalFiles } : {}),
    }),
  })
  if (!learnerResponse.ok) throw new Error('challenge_learner_run_failed')

  return [...visibleResults, ...resultsFromPayload(await learnerResponse.json())]
}

export function learnerResultId(testCaseId: string): string {
  return `${LEARNER_TEST_PREFIX}${testCaseId}`
}
