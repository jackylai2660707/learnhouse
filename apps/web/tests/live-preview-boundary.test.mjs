import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const livePreview = readFileSync(
  new URL('../components/Objects/Editor/Extensions/CodePlayground/LivePreview.tsx', import.meta.url),
  'utf8'
)
const assignmentPreview = readFileSync(
  new URL(
    '../app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskCodeObject.tsx',
    import.meta.url
  ),
  'utf8'
)
const magicBlockPreview = readFileSync(
  new URL(
    '../components/Objects/Editor/Extensions/MagicBlocks/MagicBlockPreview.tsx',
    import.meta.url
  ),
  'utf8'
)

test('assignment web preview uses the shared hardened preview', () => {
  assert.match(assignmentPreview, /import LivePreview from/)
  assert.match(assignmentPreview, /<LivePreview/)
  assert.doesNotMatch(assignmentPreview, /buildWebPreviewDocument/)
  assert.doesNotMatch(assignmentPreview, /srcDoc=/)
  assert.doesNotMatch(assignmentPreview, /<iframe/)
})

test('shared preview keeps an opaque origin and no referrer', () => {
  assert.match(livePreview, /sandbox="allow-scripts"/)
  assert.match(livePreview, /referrerPolicy="no-referrer"/)
  assert.doesNotMatch(livePreview, /sandbox="[^"]*allow-same-origin/)
})

test('MagicBlock preview also keeps an opaque origin and no referrer', () => {
  assert.match(magicBlockPreview, /sandbox="allow-scripts"/)
  assert.match(magicBlockPreview, /referrerPolicy="no-referrer"/)
  assert.doesNotMatch(magicBlockPreview, /sandbox="[^"]*allow-same-origin/)
})

test('web submissions keep source fields separate', () => {
  assert.match(assignmentPreview, /html_code: htmlCode/)
  assert.match(assignmentPreview, /css_code: cssCode/)
  assert.match(assignmentPreview, /js_code: jsCode/)
  assert.match(assignmentPreview, /html_code: submittedHtml/)
  assert.match(assignmentPreview, /css_code: submittedCss/)
  assert.match(assignmentPreview, /js_code: submittedJs/)
  assert.doesNotMatch(assignmentPreview, /source_code: build/)
})
