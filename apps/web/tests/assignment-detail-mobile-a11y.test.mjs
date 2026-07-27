import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(
  new URL('../app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/page.tsx', import.meta.url),
  'utf8'
)
const taskEditor = readFileSync(
  new URL('../app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/TaskEditor.tsx', import.meta.url),
  'utf8'
)
const numberAnswer = readFileSync(
  new URL('../app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskNumberAnswerObject.tsx', import.meta.url),
  'utf8'
)
const essay = readFileSync(
  new URL('../app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskEssayObject.tsx', import.meta.url),
  'utf8'
)
const assignmentBox = readFileSync(
  new URL('../components/Objects/Activities/Assignment/AssignmentBoxUI.tsx', import.meta.url),
  'utf8'
)

test('assignment detail keeps the full workflow available below 768px', () => {
  assert.doesNotMatch(page, /useMediaQuery/)
  assert.doesNotMatch(page, /dashboard\.assignments\.detail\.mobile\.(?:title|message1|message2)/)
  assert.match(page, /min-h-dvh w-full min-w-0 flex-col md:h-screen/)
  assert.doesNotMatch(page, /overflow-x-hidden/)
  assert.match(page, /overflow-x-auto px-4 pt-2/)
  assert.match(page, /min-h-0 flex-col max-md:\[&>\*\]:!h-auto/)
  assert.match(page, /max-md:\[&>\*\]:!w-full/)
  assert.match(page, /max-md:\[&>\*\]:!w-full md:flex-row/)
  assert.match(page, /max-md:\[&_\.px-10\]:!px-4/)
  assert.match(page, /max-md:\[&_\.grid\.grid-cols-4\]:!grid-cols-2/)
  assert.match(page, /max-md:\[&_\.grid\.grid-cols-2\]:!grid-cols-1/)
})

test('assignment sections expose one keyboard-operable ARIA tab set', () => {
  assert.match(page, /id: 'editor'/)
  assert.match(page, /id: 'submissions'/)
  assert.match(page, /id: 'analytics'/)
  assert.match(page, /role="tablist"/)
  assert.match(page, /role="tab"/)
  assert.match(page, /aria-selected=\{selected\}/)
  assert.match(page, /aria-controls=\{`assignment-\$\{subPage\.id\}-panel`\}/)
  assert.match(page, /tabIndex=\{selected \? 0 : -1\}/)
  assert.match(page, /role="tabpanel"/)
  assert.match(page, /aria-labelledby=\{`assignment-\$\{selectedSubPage\}-tab`\}/)
})

test('assignment tabs support arrows, Home and End with focus movement', () => {
  for (const key of ['ArrowRight', 'ArrowLeft', 'Home', 'End']) {
    assert.match(page, new RegExp(`event\\.key === '${key}'`))
  }
  assert.match(page, /event\.preventDefault\(\)/)
  assert.match(page, /tabRefs\.current\[index\]\?\.focus\(\)/)
  assert.match(page, /onKeyDown=\{\(event\) => handleTabKeyDown\(event, index\)\}/)
})

test('narrow assignment header actions remain semantic and wrapping', () => {
  assert.match(page, /flex flex-wrap items-center justify-center gap-2/)
  assert.match(page, /disabled=\{isPublishing\}/)
  assert.match(page, /disabled=\{publishDisabled\}/)
  assert.doesNotMatch(page, /const BADGE_EMERALD/)
})

test('nested task editor controls are semantic tabs and buttons', () => {
  assert.match(taskEditor, /<button\s+type="button"\s+onClick=\{\(\) => deleteTaskUI\(\)\}/)
  assert.match(taskEditor, /aria-label="題目編輯區"/)
  assert.equal(taskEditor.match(/role="tab"/g)?.length, 2)
  assert.match(taskEditor, /aria-selected=\{selectedSubPage === 'general'\}/)
  assert.match(taskEditor, /aria-selected=\{selectedSubPage === 'content'\}/)
  assert.match(taskEditor, /role="tabpanel"/)
  assert.match(taskEditor, /handleEditorTabKeyDown\(event, 0\)/)
  assert.match(taskEditor, /handleEditorTabKeyDown\(event, 1\)/)
  for (const key of ['ArrowRight', 'ArrowLeft', 'Home', 'End']) {
    assert.match(taskEditor, new RegExp(`event\\.key === '${key}'`))
  }
})

test('nested task editor header and body reflow without clipping at phone widths', () => {
  assert.match(taskEditor, /flex min-w-0 flex-col gap-3 py-1 sm:flex-row/)
  assert.match(taskEditor, /flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto/)
  assert.match(taskEditor, /mx-4 mt-5 rounded-lg[^\n]+sm:mx-6 md:mx-10/)
  assert.match(taskEditor, /mx-4 min-w-0[^\n]+sm:mx-6 sm:px-6 md:mx-10/)
  assert.doesNotMatch(taskEditor, /overflow-x-hidden/)
})

test('number-answer editor becomes one column and fields can shrink below sm', () => {
  assert.match(numberAnswer, /grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2/)
  assert.ok((numberAnswer.match(/w-full min-w-0/g) ?? []).length >= 5)
  assert.match(numberAnswer, /w-full min-w-0[^"\n]+sm:max-w-\[200px\]/)
  assert.match(numberAnswer, /flex min-w-0 flex-wrap items-center gap-1\.5/)
})

test('essay editor uses one-column phone layouts and shrinkable fields/actions', () => {
  assert.match(essay, /grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2/)
  assert.match(essay, /sm:grid-cols-\[minmax\(0,160px\)_minmax\(0,1fr\)\]/)
  assert.ok((essay.match(/w-full min-w-0/g) ?? []).length >= 8)
  assert.match(essay, /grid w-full min-w-0 grid-cols-1 gap-2 sm:flex/)
  assert.match(essay, /flex min-w-0 flex-wrap items-center justify-between gap-2/)
})

test('shared assignment teacher and grading actions are native keyboard controls', () => {
  for (const action of ['save', 'grade', 'custom-grade']) {
    const actionPattern = new RegExp(
      `<button[\\s\\S]{0,180}type="button"[\\s\\S]{0,180}runAction\\('${action}'`
    )
    assert.match(assignmentBox, actionPattern)
  }
  assert.ok((assignmentBox.match(/disabled=\{actionIsDisabled\}/g) ?? []).length >= 3)
  assert.match(assignmentBox, /aria-busy=\{isActionPending\('save'\)\}/)
  assert.match(assignmentBox, /aria-busy=\{isActionPending\('grade'\)\}/)
  assert.match(assignmentBox, /aria-busy=\{isActionPending\('custom-grade'\)\}/)
  assert.ok((assignmentBox.match(/focus-visible:ring-2/g) ?? []).length >= 3)
  assert.doesNotMatch(assignmentBox, /<div\s+onClick=\{\(\) => runAction\('(save|grade|custom-grade)'/)
})

test('shared assignment student disabled contract remains intact', () => {
  assert.match(assignmentBox, /disabled=\{actionIsDisabled \|\| studentActionDisabled\}/)
  assert.match(assignmentBox, /aria-describedby=\{studentActionDisabled \? studentActionDisabledDescriptionId : undefined\}/)
})
