import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const contentOverviewUrl = new URL('../components/Dashboard/Home/ContentOverview.tsx', import.meta.url)
const recentMembersUrl = new URL('../components/Dashboard/Home/RecentMembers.tsx', import.meta.url)
const documentModalUrl = new URL('../components/Objects/Modals/Activities/Create/NewActivityModal/DocumentActivityModal.tsx', import.meta.url)
const courseOverviewTopUrl = new URL('../components/Dashboard/Misc/CourseOverviewTop.tsx', import.meta.url)

test('dashboard member data is only fetched and rendered with users read permission', async () => {
  const [contentOverview, recentMembers] = await Promise.all([
    readFile(contentOverviewUrl, 'utf8'),
    readFile(recentMembersUrl, 'utf8'),
  ])

  for (const source of [contentOverview, recentMembers]) {
    assert.match(source, /useAdminStatus/)
    assert.match(source, /rights\?\.users\?\.action_read === true/)
    assert.match(source, /enabled: !!token && !!orgId && canReadUsers/)
  }
  assert.match(contentOverview, /show: canReadUsers/)
  assert.match(recentMembers, /if \(!canReadUsers\) return null/)
})

test('single PDF activities stay single activities and link to the guarded course builder', async () => {
  const source = await readFile(documentModalUrl, 'utf8')

  assert.match(source, /這會建立一個單一 PDF 學習活動，不會自動建立課程。/)
  assert.match(source, /resolved_features\?\.ai\?\.enabled === true/)
  assert.match(source, /rights\?\.courses\?\.action_create === true/)
  assert.match(source, /href="\/dash\/courses\/pdf-build"/)
  assert.match(source, /改用 PDF 智能建課/)
  assert.match(source, /activity_type: 'TYPE_DOCUMENT'/)
  assert.match(source, /activity_sub_type: 'SUBTYPE_DOCUMENT_PDF'/)
})

test('RAG reindex controls require course content-update permission and retain Chinese status', async () => {
  const source = await readFile(courseOverviewTopUrl, 'utf8')

  assert.match(source, /useCourseRights\(/)
  assert.match(source, /hasCoursePermission\('update_content'\)/)
  assert.match(source, /const canIndexCourseForAI = isAIEnabled && hasCoursePermission\('update_content'\)/)
  assert.match(source, /if \(isIndexing \|\| !canIndexCourseForAI \|\| !courseStructure\?\.course_uuid\) return/)
  assert.match(source, /\{canIndexCourseForAI && \(/)
  assert.match(source, /正在為 AI 重建課程索引，請稍候。/)
  assert.match(source, /已完成 AI 課程索引，共建立 \$\{data\.chunks_indexed\} 個內容片段。/)
  assert.match(source, /role=\{indexStatus\.type === 'error' \? 'alert' : 'status'\}/)
  assert.doesNotMatch(source, /Indexing course for AI|Indexed \$\{data\.chunks_indexed\} chunks for AI|Failed to index course/)
})
