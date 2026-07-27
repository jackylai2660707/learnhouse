import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

const serviceUrl = new URL('../services/ai/courseplanning.ts', import.meta.url)
const clientUrl = new URL('../app/orgs/[orgslug]/dash/courses/pdf-build/client.tsx', import.meta.url)
const pageUrl = new URL('../app/orgs/[orgslug]/dash/courses/pdf-build/page.tsx', import.meta.url)

async function loadPDFBuildHelpers() {
  const source = await readFile(serviceUrl, 'utf8')
  const testSource = source.replace(
    "import { getAPIUrl } from '@services/config/config'",
    "const getAPIUrl = () => 'http://test.invalid/api/v1/'",
  )
  const directory = await mkdtemp(path.join(os.tmpdir(), 'learnhouse-pdf-build-'))
  const modulePath = path.join(directory, 'courseplanning.ts')
  await writeFile(modulePath, testSource)
  try {
    return await import(`${pathToFileURL(modulePath).href}?test=${Date.now()}`)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}

test('PDF build helpers preserve terminal, org-scoped, error, and degraded behavior', async () => {
  const helpers = await loadPDFBuildHelpers()
  const job = {
    job_uuid: 'job_1', org_id: 12, creator_user_id: 5, stage: 'done',
    progress_current: 4, progress_total: 4, chapters_created: 1,
    activities_created: 2, source_documents_created: 1,
    indexing: { status: 'degraded', code: 'embedding_unavailable', chunks: 3 },
    warning: null, error: null, attempts: 1,
    created_at: '2026-07-26T00:00:00Z', updated_at: '2026-07-26T00:00:00Z',
  }

  assert.equal(helpers.isTerminalPDFBuild('done'), true)
  assert.equal(helpers.isTerminalPDFBuild('failed'), true)
  assert.equal(helpers.isTerminalPDFBuild('indexing'), false)
  assert.equal(helpers.pdfBuildStorageKey(12), 'learnhouse:pdf-build:12:active-job')
  assert.notEqual(helpers.pdfBuildStorageKey(12), helpers.pdfBuildStorageKey(13))
  assert.equal(helpers.hasDegradedPDFBuildIndexing(job), true)
  assert.equal(helpers.hasDegradedPDFBuildIndexing({ ...job, indexing: { status: 'success', chunks: 3 } }), false)
  assert.equal(
    helpers.formatPDFBuildError({ detail: { code: 'PDF_INDEXING_FAILED', message: '索引稍後重試' } }),
    'PDF_INDEXING_FAILED：索引稍後重試',
  )
  assert.equal(helpers.formatPDFBuildError({ detail: '請重新上傳 PDF' }), '請重新上傳 PDF')
  assert.doesNotMatch(helpers.formatPDFBuildError({ detail: { unexpected: true } }), /\[object Object\]/)
})

test('PDF file validation keeps distinct same-name files and enforces browser caps', async () => {
  const helpers = await loadPDFBuildHelpers()
  const sameNameSameSize = [
    { name: '教材.pdf', size: 1024, lastModified: 1 },
    { name: '教材.pdf', size: 1024, lastModified: 2 },
  ]

  assert.equal(helpers.validatePDFBuildFiles(sameNameSameSize), null)
  assert.match(
    helpers.validatePDFBuildFiles([{ name: '太大.pdf', size: helpers.PDF_BUILD_MAX_FILE_BYTES + 1 }]),
    /每份 100 MiB/,
  )
  assert.match(
    helpers.validatePDFBuildFiles([
      { name: '一.pdf', size: helpers.PDF_BUILD_MAX_FILE_BYTES },
      { name: '二.pdf', size: helpers.PDF_BUILD_MAX_FILE_BYTES },
      { name: '三.pdf', size: 1 },
    ]),
    /合計超過 200 MiB/,
  )
  assert.match(
    helpers.validatePDFBuildFiles(Array.from({ length: 9 }, (_, index) => ({ name: `${index}.pdf`, size: 1 }))),
    /最多只可上傳 8 份/,
  )
})

test('a delayed recovery cannot overwrite a newly submitted PDF build', async () => {
  const helpers = await loadPDFBuildHelpers()
  const controller = helpers.createPDFBuildRecoveryController()
  const recoveryGeneration = controller.beginRecovery()
  let resolveRecovery
  const delayedRecovery = new Promise((resolve) => { resolveRecovery = resolve })
  const appliedJobs = []

  const recovery = delayedRecovery.then((job) => {
    if (controller.isCurrent(recoveryGeneration)) appliedJobs.push(job.job_uuid)
  })
  controller.beginSubmission()
  appliedJobs.push('newly-submitted-job')
  resolveRecovery({ job_uuid: 'stale-recovered-job' })
  await recovery

  assert.deepEqual(appliedJobs, ['newly-submitted-job'])
})

test('PDF build client polls server snapshots rather than elapsed-time guesses', async () => {
  const [service, client, page] = await Promise.all([
    readFile(serviceUrl, 'utf8'),
    readFile(clientUrl, 'utf8'),
    readFile(pageUrl, 'utf8'),
  ])

  assert.match(service, /ai\/courseplanning\/pdf-builds/)
  assert.match(service, /'Idempotency-Key': idempotencyKey/)
  assert.match(service, /active: 'true', mine: 'true'/)
  assert.doesNotMatch(service, /ai\/courseplanning\/pdf-build['"]/)
  assert.doesNotMatch(client, /stageThresholds|stageForElapsed|約 3-4 分鐘/)
  assert.match(client, /window\.localStorage\.getItem\(storageKey\)/)
  assert.match(client, /window\.setTimeout\(\(\) => void poll\(\), pollDelay\)/)
  assert.match(client, /isTerminalPDFBuild\(job\.stage\)\) return/)
  assert.match(client, /hasDegradedPDFBuildIndexing\(job\)/)
  assert.match(client, /課程草稿已建立/)
  assert.match(client, /validatePDFBuildFiles\(nextFiles\)/)
  assert.doesNotMatch(client, /new Map\(current\.map\(\(file\) => \[`\$\{file\.name\}:\$\{file\.size\}`/)
  assert.doesNotMatch(client, /\.slice\(0, 8\)/)
  assert.match(client, /aria-label=\{`移除 \$\{file\.name\}`\}/)
  assert.match(client, /role="status" aria-live="polite"/)
  assert.match(client, /beginSubmission\(\)/)
  assert.match(client, /isCurrent\(recoveryGeneration\)/)
  assert.match(page, /getServerSession\(\)/)
  assert.match(page, /getOrganizationContextInfo\(/)
  assert.match(page, /courses\?\.action_create === true/)
  assert.match(page, /resolved_features\?\.ai\?\.enabled !== true/)
  assert.match(page, /redirect\(getUriWithOrg\(orgslug, '\/dash\/courses'\)\)/)
})
