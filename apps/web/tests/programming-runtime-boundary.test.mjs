import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const playground = readFileSync(
  new URL('../components/Objects/Editor/Extensions/CodePlayground/CodePlaygroundComponent.tsx', import.meta.url),
  'utf8'
)
const assignment = readFileSync(
  new URL('../app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskCodeObject.tsx', import.meta.url),
  'utf8'
)
const assignmentBox = readFileSync(
  new URL('../components/Objects/Activities/Assignment/AssignmentBoxUI.tsx', import.meta.url),
  'utf8'
)
const en = JSON.parse(readFileSync(new URL('../locales/en.json', import.meta.url), 'utf8'))
const zh = JSON.parse(readFileSync(new URL('../locales/zh.json', import.meta.url), 'utf8'))

function leafKeys(value, prefix = '') {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return child && typeof child === 'object' ? leafKeys(child, path) : [path]
  })
}

test('saved unsupported language ids fail closed on both programming surfaces', () => {
  assert.match(playground, /const canRun = isPreviewMode \|\| \([\s\S]{0,180}!executionUnavailable && !isRunning && !isSubmitting/)
  assert.match(playground, /disabled=\{!canRun\}/)
  assert.match(playground, /if \(runtimeUnavailable\) \{[\s\S]{0,220}?return/)
  assert.match(playground, /role="alert"/)

  assert.match(assignment, /studentActionDisabled=\{runtimeUnavailable\}/)
  assert.match(assignment, /disabled=\{isRunning \|\| runtimeUnavailable\}/)
  assert.match(assignment, /if \(runtimeUnavailable\) \{[\s\S]{0,300}?return/)
  assert.match(assignmentBox, /disabled=\{actionIsDisabled \|\| studentActionDisabled\}/)
  assert.match(assignmentBox, /studentActionDisabledDescriptionId/)
})

test('unauthenticated or unbound playground actions are visibly and accessibly unavailable', () => {
  assert.match(playground, /const canRun = isPreviewMode \|\| \([\s\S]{0,140}Boolean\(accessToken\)/)
  assert.match(playground, /const canSubmit = !executionUnavailable[\s\S]{0,160}Boolean\(accessToken\) && Boolean\(challengeUuid\)/)
  assert.match(playground, /disabled=\{!canRun\}/)
  assert.match(playground, /disabled=\{!canSubmit\}/)
  assert.match(playground, /aria-describedby=\{actionDescriptionIds\}/)
  assert.match(playground, /id=\{actionAvailabilityWarningId\}[\s\S]{0,220}\{actionAvailabilityMessage\}/)
  assert.match(playground, /!canRun[\s\S]{0,120}\? 'bg-white\/\[0\.06\] text-neutral-500 cursor-not-allowed'/)
  assert.match(playground, /!canSubmit[\s\S]{0,120}\? 'bg-emerald-500\/10 text-emerald-700 cursor-not-allowed'/)
  assert.equal(zh.code_playground.errors.authentication_required, '請先登入，或等待登入狀態完成後，再執行或提交程式。')
  assert.equal(zh.code_playground.errors.challenge_required, '此程式挑戰尚未準備完成，請稍後重試或聯絡老師。')
})

test('Run and Submit acquire one synchronous cross-action coordinator', () => {
  const runBlock = playground.match(
    /const runCode = useCallback[\s\S]*?const submitChallenge/
  )?.[0] || ''
  const submitBlock = playground.match(
    /const submitChallenge = useCallback[\s\S]*?const toggleSolution/
  )?.[0] || ''

  assert.match(runBlock, /!canRun[\s\S]*?tryStart\('run'\)/)
  assert.match(runBlock, /finally[\s\S]*?finish\('run'\)/)
  assert.match(submitBlock, /!canSubmit[\s\S]*?tryStart\('submit'\)/)
  assert.match(submitBlock, /finally[\s\S]*?finish\('submit'\)/)
  assert.match(playground, /onRun: \(\) => runCodeRef\.current\(\)/)
})

test('SQL execution requires the manifest-declared SQLite prerequisite', () => {
  const runBlock = playground.match(
    /const runCode = useCallback[\s\S]*?const submitChallenge/
  )?.[0] || ''
  const submitBlock = playground.match(
    /const submitChallenge = useCallback[\s\S]*?const toggleSolution/
  )?.[0] || ''

  assert.match(playground, /getApiAdapterRequirements\(languageId\)/)
  assert.match(playground, /sqlDatabaseRequired = isSqlLanguage && !sqliteDbPath/)
  assert.match(playground, /code_playground\.errors\.sqlite_required/)
  assert.equal(
    zh.code_playground.errors.sqlite_required,
    '請先上傳 SQLite 資料庫檔案，才可執行或提交 SQL。'
  )
  assert.match(runBlock, /if \(sqlDatabaseRequired\)[\s\S]{0,220}?return/)
  assert.match(submitBlock, /if \(sqlDatabaseRequired\)[\s\S]{0,220}?return/)
  assert.match(playground, /const canSubmit = !executionUnavailable && !isRunning && !isSubmitting/)
  assert.match(playground, /disabled=\{!canSubmit\}/)
})

test('execution failures do not log raw errors or response bodies', () => {
  assert.doesNotMatch(playground, /console\.error|data\.detail/)
  assert.doesNotMatch(assignment, /console\.error|resp\.text\(/)
})

test('formal challenge submit remains server-authoritative and excludes learner tests', () => {
  const submitBlock = playground.match(
    /const submitChallenge = useCallback[\s\S]*?const toggleSolution/
  )?.[0] || ''
  assert.match(submitBlock, /coding-challenges\/\$\{challengeUuid\}\/submit/)
  assert.match(submitBlock, /JSON\.stringify\(\{ source_code: code \}\)/)
  assert.doesNotMatch(submitBlock, /studentTestCases|test_cases/)
})

test('assignment student code never consumes reference solutions or an insecure reveal flag', () => {
  const studentView = assignment.match(
    /\{\/\* === STUDENT VIEW === \*\/[\s\S]*?\{\/\* === GRADING VIEW === \*\//
  )?.[0] || ''

  assert.doesNotMatch(assignment, /show_solution_after_submit/)
  assert.doesNotMatch(studentView, /contents\.solution_code|reference_solution_label/)
  assert.match(assignment, /view === 'grading'[\s\S]*?contents\.solution_code/)
})

test('assignment students retain only the server-owned hidden-test count', () => {
  assert.match(assignment, /hidden_test_count\?: number/)
  assert.match(
    assignment,
    /hidden_test_count: includePrivateAuthoringFields[\s\S]{0,180}?parsedHiddenCount/
  )
  assert.match(
    assignment,
    /const hiddenTestCount = view === 'student'[\s\S]{0,120}?contents\.test_cases\.filter/
  )
})

test('assignment code labels have matching English and Traditional Chinese keys', () => {
  const enCode = en.dashboard.assignments.editor.task_editor.code
  const zhCode = zh.dashboard.assignments.editor.task_editor.code
  assert.deepEqual(leafKeys(enCode).sort(), leafKeys(zhCode).sort())
  assert.equal(zhCode.run_tests, '執行測試')
  assert.match(zhCode.unsupported_runtime_student, /請聯絡老師/)
})
