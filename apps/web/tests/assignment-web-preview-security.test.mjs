import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const component = fs.readFileSync(
  path.join(
    root,
    'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskCodeObject.tsx'
  ),
  'utf8'
)

test('student web checks exclude hidden checks and share multiline regex flags', () => {
  assert.match(component, /filter\(\(check\) => includeHidden \|\| !check\.hidden\)/)
  assert.match(component, /new RegExp\(check\.pattern, 'im'\)/)
  assert.match(component, /runWebPreviewChecks\(contents, htmlCode, cssCode, jsCode\)/)
  assert.match(component, /runWebPreviewChecks\(contents, submittedHtml, submittedCss, submittedJs, true\)/)
})

test('submission gate is tied to the current source and persisted payload omits results', () => {
  assert.match(component, /lastRunSourceKey === currentSourceKey/)
  assert.match(component, /lastRunSourceKey !== currentSourceKey/)
  assert.doesNotMatch(
    component,
    /task_submission:\s*\{[\s\S]{0,260}?results:/
  )
})

test('teacher preview grading consumes the server-returned grade', () => {
  const gradingSource = component.slice(component.indexOf('// --- GRADE (grading view) ---'))
  const webGradeBranch = gradingSource.match(
    /if \(contents\.mode === 'web_preview'\) \{[\s\S]*?\n      return\n    \}/
  )?.[0] || ''
  assert.doesNotMatch(webGradeBranch, /finalGrade|grade:\s*|task_submission_grade_feedback:/)
  assert.match(webGradeBranch, /setUserSubmissionObject\(res\.data\)/)
  assert.match(webGradeBranch, /res\.data\.grade/)
})

test('teacher hydration and authoring preserve all three web source editors', () => {
  assert.match(component, /setHtmlCode\(c\.starter_html \?\? ''\)/)
  assert.match(component, /setCssCode\(c\.starter_css \?\? ''\)/)
  assert.match(component, /setJsCode\(c\.starter_js \?\? ''\)/)
  assert.match(component, /contents\.mode === 'web_preview' && \(/)
  assert.match(component, /\(\['html', 'css', 'js'\] as const\)\.map/)
})
