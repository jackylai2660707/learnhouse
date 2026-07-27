import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildPreviewDocument,
  MAX_PREVIEW_LOG_TEXT_LENGTH,
  normalizePreviewMessage,
  previewFilesKey,
} from '../components/Objects/Editor/Extensions/CodePlayground/preview-document.ts'

const previewSources = [
  new URL('../components/Objects/Editor/Extensions/CodePlayground/LivePreview.tsx', import.meta.url),
  new URL('../components/Objects/Editor/Extensions/CodePlayground/preview-document.ts', import.meta.url),
]

test('preview source files contain no unsafe C0 control bytes', () => {
  for (const sourceUrl of previewSources) {
    const source = readFileSync(sourceUrl)
    const unsafe = [...source].filter((byte) => byte < 0x20 && ![0x09, 0x0a, 0x0d].includes(byte))
    assert.deepEqual(unsafe, [], sourceUrl.pathname)
  }
})

test('preview file keys are deterministic and collision-safe', () => {
  const first = [{ name: 'a', content: 'b\u0001c\u0000d' }]
  const second = [
    { name: 'a', content: 'b' },
    { name: 'c', content: 'd' },
  ]

  assert.equal(previewFilesKey(first), previewFilesKey(structuredClone(first)))
  assert.notEqual(previewFilesKey(first), previewFilesKey(second))
})

test('preview composition escapes closing tags and file names', () => {
  const document = buildPreviewDocument(
    '<!doctype html><html><head></head><body><main>學習</main></body></html>',
    'token',
    [
      { name: 'bad"<.css', content: 'body{} </style><script>badCss()</script>' },
      { name: 'bad"<.js', content: 'console.log("ok")</script><p>bad</p>' },
    ]
  )

  assert.ok(document.includes('"name":"bad\\"\\u003c.css"'))
  assert.ok(document.includes('"name":"bad\\"\\u003c.js"'))
  assert.ok(document.includes('body{} \\u003c/style>\\u003cscript>badCss()\\u003c/script>'))
  assert.ok(document.includes('console.log(\\"ok\\")\\u003c/script>\\u003cp>bad\\u003c/p>'))
  assert.equal(document.includes('body{} </style><script>badCss()'), false)
  assert.equal(document.includes('console.log("ok")</script><p>bad</p>'), false)
})

test('preview composition preserves document order and doctype', () => {
  const document = buildPreviewDocument(
    '<!doctype html><html><head><title>T</title></head><body><p>Body</p><script>studentInline()</script></body></html>',
    'order-token',
    [
      { name: 'styles.css', content: '.lesson { color: green; }' },
      { name: 'script.js', content: 'studentFile()' },
    ]
  )

  assert.match(document, /^<!doctype html>/i)
  assert.ok(document.indexOf('__lhPreview') < document.indexOf('studentInline()'))
  assert.ok(document.indexOf('__lhPreview') < document.indexOf("body.appendChild(script)"))
  assert.ok(document.indexOf("head.appendChild(style)") < document.indexOf("body.appendChild(script)"))
  assert.ok(document.indexOf("DOMContentLoaded") < document.indexOf("body.appendChild(script)"))
})

test('preview composition never searches closing-tag text inside source content', () => {
  const source = `<!doctype html><html><head><script>
const headMarker = "</head>";
const bodyMarker = "</body>";
</script></head><body><!-- </body> --><textarea></head></textarea></body></html>`
  const document = buildPreviewDocument(source, 'token', [
    { name: 'script.js', content: 'console.log("attached")' },
  ])

  assert.ok(document.endsWith(source.replace(/^<!doctype html>/i, '')))
  assert.equal(document.match(/const bodyMarker = "<\/body>";/g)?.length, 1)
  assert.equal(document.match(/<textarea><\/head><\/textarea>/g)?.length, 1)
})

test('preview messages require the current frame and token and cap text', () => {
  const expectedSource = {}
  const payload = {
    __lhPreview: 'current-token',
    level: 'error',
    text: 'x'.repeat(MAX_PREVIEW_LOG_TEXT_LENGTH + 500),
    line: 7,
    col: 3,
  }

  assert.equal(normalizePreviewMessage({}, expectedSource, payload, 'current-token'), null)
  assert.equal(normalizePreviewMessage(expectedSource, expectedSource, payload, 'stale-token'), null)
  assert.equal(normalizePreviewMessage(expectedSource, expectedSource, null, 'current-token'), null)

  const accepted = normalizePreviewMessage(
    expectedSource,
    expectedSource,
    payload,
    'current-token'
  )
  assert.equal(accepted?.level, 'error')
  assert.equal(accepted?.text.length, MAX_PREVIEW_LOG_TEXT_LENGTH)
  assert.equal(accepted?.line, 7)
  assert.equal(accepted?.col, 3)
})
