import { getAPIUrl } from '@services/config/config'

// Feature flag: Enable activity content generation (disabled for now)
export const ENABLE_ACTIVITY_CONTENT_GENERATION = true

export interface Attachment {
  id: string
  type: 'image' | 'video' | 'file' | 'youtube'
  name: string
  url?: string
  file?: File
  preview?: string
}

export interface AttachmentData {
  type: 'image' | 'video' | 'file' | 'youtube'
  name: string
  url?: string
  content_base64?: string
  mime_type?: string
}

export interface ActivityPlan {
  name: string
  type: string
  description: string
  suggested_blocks: string[]
}

export interface ChapterPlan {
  name: string
  description: string
  activities: ActivityPlan[]
}

export interface CoursePlan {
  name: string
  description: string
  learnings: string
  tags: string
  chapters: ChapterPlan[]
}

export interface CoursePlanningMessage {
  role: 'user' | 'model'
  content: string
}

export interface CoursePlanningSession {
  session_uuid: string
  planning_iteration_count: number
  max_planning_iterations: number
  current_plan: CoursePlan | null
  message_history: CoursePlanningMessage[]
  course_id: number | null
}

export interface CreatedChapter {
  chapter_uuid: string
  chapter_id: number
  name: string
  activities: {
    activity_uuid: string
    activity_id: number
    name: string
    description: string
    suggested_blocks: string[]
  }[]
}

export interface FinalizeCoursePlanResponse {
  course_uuid: string
  course_id: number
  chapters: CreatedChapter[]
}

export interface CoursePlanningIntakeResponse {
  ready_to_generate: boolean
  assistant_message: string
  questions: string[]
  generation_prompt: string
}

export type PDFBuildStage = 'extracting' | 'planning' | 'creating' | 'indexing' | 'done' | 'failed'

export interface PDFBuildIssue {
  code: string
  message: string
  retryable: boolean
}

export interface PDFBuildDraftCourse {
  course_id: number
  course_uuid: string
  public: false
  published: false
}

export interface PDFBuildIndexing {
  status: 'success' | 'skipped' | 'failed' | 'degraded'
  code?: string | null
  chunks: number
}

export interface PDFCourseBuildJob {
  job_uuid: string
  org_id: number
  creator_user_id: number
  stage: PDFBuildStage
  progress_current: number
  progress_total: number
  draft_course?: PDFBuildDraftCourse | null
  chapters_created: number
  activities_created: number
  source_documents_created: number
  indexing?: PDFBuildIndexing | null
  warning?: PDFBuildIssue | null
  error?: PDFBuildIssue | null
  attempts: number
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
}

export const PDF_BUILD_STAGE_LABELS: Record<PDFBuildStage, string> = {
  extracting: '拆解 PDF',
  planning: '生成課程架構',
  creating: '建立課堂內容',
  indexing: '建立 RAG 知識庫',
  done: '完成',
  failed: '失敗',
}

export const PDF_BUILD_MAX_FILES = 8
export const PDF_BUILD_MAX_FILE_BYTES = 100 * 1024 * 1024
export const PDF_BUILD_MAX_TOTAL_BYTES = 200 * 1024 * 1024

export interface PDFBuildFileLike {
  name: string
  size: number
}

export function validatePDFBuildFiles(files: PDFBuildFileLike[]): string | null {
  if (files.length > PDF_BUILD_MAX_FILES) {
    return `最多只可上傳 ${PDF_BUILD_MAX_FILES} 份 PDF 教材，請先移除部分檔案。`
  }

  const oversized = files.find((file) => file.size > PDF_BUILD_MAX_FILE_BYTES)
  if (oversized) {
    return `「${oversized.name}」超過每份 100 MiB 的上限，請壓縮或分拆 PDF 後再上傳。`
  }

  const totalSize = files.reduce((total, file) => total + file.size, 0)
  if (totalSize > PDF_BUILD_MAX_TOTAL_BYTES) {
    return '所有 PDF 合計超過 200 MiB 上限，請移除、壓縮或分拆教材後再上傳。'
  }
  return null
}

export interface PDFBuildRecoveryController {
  beginRecovery: () => number
  beginSubmission: () => number
  currentGeneration: () => number
  isCurrent: (generation: number) => boolean
}

export function createPDFBuildRecoveryController(): PDFBuildRecoveryController {
  let generation = 0
  return {
    beginRecovery: () => ++generation,
    beginSubmission: () => ++generation,
    currentGeneration: () => generation,
    isCurrent: (candidate) => candidate === generation,
  }
}

export const isTerminalPDFBuild = (stage: PDFBuildStage) => stage === 'done' || stage === 'failed'

export const hasDegradedPDFBuildIndexing = (job: PDFCourseBuildJob) => (
  job.indexing?.status === 'failed' || job.indexing?.status === 'degraded'
)

export const pdfBuildStorageKey = (orgId: number) => `learnhouse:pdf-build:${orgId}:active-job`

export const createPDFBuildIdempotencyKey = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `pdf-build-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function formatPDFBuildError(error: unknown, fallback = 'PDF 建課暫時失敗，請稍後重試。') {
  if (typeof error === 'string' && error.trim()) return error
  if (!error || typeof error !== 'object') return fallback

  const record = error as Record<string, unknown>
  const nested = record.detail && typeof record.detail === 'object'
    ? record.detail as Record<string, unknown>
    : null
  const message = [record.message, record.detail, nested?.message, nested?.detail]
    .find((value): value is string => typeof value === 'string' && value.trim().length > 0)
  const code = [record.code, nested?.code]
    .find((value): value is string => typeof value === 'string' && value.trim().length > 0)

  if (message && code) return `${code}：${message}`
  return message || code || fallback
}

interface StreamChunk {
  type: 'chunk' | 'done' | 'error'
  content?: string
  session_uuid?: string
  message?: string
}

const buildAttachmentKey = (attachment: Attachment) => (
  `${attachment.type}:${attachment.name}:${attachment.url || ''}:${attachment.file?.name || ''}:${attachment.file?.size || 0}`
)

/**
 * Convert File to base64 string
 */
async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      // Remove the data URL prefix (e.g., "data:image/png;base64,")
      const base64 = result.split(',')[1]
      resolve(base64)
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

/**
 * Convert attachments to API format
 */
async function prepareAttachments(attachments: Attachment[]): Promise<AttachmentData[]> {
  const prepared: AttachmentData[] = []

  for (const attachment of attachments) {
    if (attachment.type === 'youtube' && attachment.url) {
      prepared.push({
        type: 'youtube',
        name: attachment.name,
        url: attachment.url,
      })
    } else if (attachment.file) {
      const base64 = await fileToBase64(attachment.file)
      prepared.push({
        type: attachment.type,
        name: attachment.name,
        content_base64: base64,
        mime_type: attachment.file.type,
      })
    }
  }

  return prepared
}

/**
 * Clarify requirements before generating a course plan.
 */
export async function clarifyCoursePlanningRequirements(
  orgId: number,
  prompt: string,
  messages: CoursePlanningMessage[],
  accessToken: string,
  language: string = 'en',
  attachments?: Attachment[]
): Promise<{ success: boolean; data?: CoursePlanningIntakeResponse; error?: string }> {
  const uniqueAttachments = attachments
    ? Array.from(new Map(attachments.map((attachment) => [buildAttachmentKey(attachment), attachment])).values())
    : undefined
  const attachmentData = uniqueAttachments && uniqueAttachments.length > 0
    ? await prepareAttachments(uniqueAttachments)
    : undefined

  const data: Record<string, unknown> = {
    org_id: orgId,
    prompt,
    language,
    messages,
  }

  if (attachmentData && attachmentData.length > 0) {
    data.attachments = attachmentData
  }

  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/intake`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(data),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      const detail = errorData.detail
      return {
        success: false,
        error: typeof detail === 'string'
          ? detail
          : typeof detail?.message === 'string'
            ? detail.message
            : `PDF 建課暫時失敗（HTTP ${response.status}），請稍後重試。`,
      }
    }

    return { success: true, data: await response.json() }
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : 'PDF 建課暫時失敗，請稍後重試。',
    }
  }
}

async function readPDFBuildError(response: Response) {
  const payload: unknown = await response.json().catch(() => null)
  return formatPDFBuildError(payload, `PDF 建課暫時失敗（HTTP ${response.status}），請稍後重試。`)
}

export async function startPDFCourseBuild(
  orgId: number,
  files: File[],
  accessToken: string,
  idempotencyKey: string,
  options?: {
    courseName?: string
    instructions?: string
    language?: string
    autoIndex?: boolean
  }
): Promise<{ success: boolean; data?: PDFCourseBuildJob; error?: string }> {
  const formData = new FormData()
  formData.append('org_id', String(orgId))
  formData.append('language', options?.language || 'zh-Hant')
  formData.append('auto_index', String(options?.autoIndex ?? true))

  if (options?.courseName?.trim()) {
    formData.append('course_name', options.courseName.trim())
  }
  if (options?.instructions?.trim()) {
    formData.append('instructions', options.instructions.trim())
  }

  files.forEach((file) => formData.append('files', file))

  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/pdf-builds`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Idempotency-Key': idempotencyKey,
      },
      body: formData,
    })

    if (!response.ok) {
      return {
        success: false,
        error: await readPDFBuildError(response),
      }
    }

    return { success: true, data: await response.json() }
  } catch (error) {
    return {
      success: false,
      error: formatPDFBuildError(error),
    }
  }
}

export async function getPDFCourseBuild(
  orgId: number,
  jobUuid: string,
  accessToken: string,
): Promise<{ success: boolean; data?: PDFCourseBuildJob; error?: string }> {
  try {
    const response = await fetch(
      `${getAPIUrl()}ai/courseplanning/pdf-builds/${encodeURIComponent(jobUuid)}?org_id=${encodeURIComponent(String(orgId))}`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
    )
    if (!response.ok) return { success: false, error: await readPDFBuildError(response) }
    return { success: true, data: await response.json() }
  } catch (error) {
    return { success: false, error: formatPDFBuildError(error) }
  }
}

export async function listActiveMyPDFCourseBuilds(
  orgId: number,
  accessToken: string,
): Promise<{ success: boolean; data?: PDFCourseBuildJob[]; error?: string }> {
  try {
    const params = new URLSearchParams({ org_id: String(orgId), active: 'true', mine: 'true' })
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/pdf-builds?${params.toString()}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (!response.ok) return { success: false, error: await readPDFBuildError(response) }
    return { success: true, data: await response.json() }
  } catch (error) {
    return { success: false, error: formatPDFBuildError(error) }
  }
}

/**
 * Start a new course planning session with streaming response
 */
export async function startCoursePlanningSession(
  orgId: number,
  prompt: string,
  accessToken: string,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void,
  onError: (error: string) => void,
  language: string = 'en',
  attachments?: Attachment[]
): Promise<void> {
  // Prepare attachments if provided (only send if there are actual attachments)
  const attachmentData = attachments && attachments.length > 0
    ? await prepareAttachments(attachments)
    : undefined

  const data: Record<string, unknown> = {
    org_id: orgId,
    prompt,
    language,
  }

  // Only include attachments if we have them
  if (attachmentData && attachmentData.length > 0) {
    data.attachments = attachmentData
  }

  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(data),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `HTTP error ${response.status}`)
    }

    await processStream(response, onChunk, onComplete, onError)
  } catch (error) {
    onError(error instanceof Error ? error.message : 'Unknown error occurred')
  }
}

/**
 * Continue an existing course planning session with a new message
 */
export async function iterateCoursePlanning(
  sessionUuid: string,
  message: string,
  accessToken: string,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void,
  onError: (error: string) => void,
  currentPlan?: CoursePlan | null,
  attachments?: Attachment[]
): Promise<void> {
  // Prepare attachments if provided (only send if there are actual attachments)
  const attachmentData = attachments && attachments.length > 0
    ? await prepareAttachments(attachments)
    : undefined

  const data: Record<string, unknown> = {
    session_uuid: sessionUuid,
    message,
    current_plan: currentPlan || undefined,
  }

  // Only include attachments if we have them
  if (attachmentData && attachmentData.length > 0) {
    data.attachments = attachmentData
  }

  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/iterate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(data),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `HTTP error ${response.status}`)
    }

    await processStream(response, onChunk, onComplete, onError)
  } catch (error) {
    onError(error instanceof Error ? error.message : 'Unknown error occurred')
  }
}

/**
 * Finalize the course plan and create the course structure in the database
 */
export async function finalizeCoursePlan(
  sessionUuid: string,
  plan: CoursePlan,
  accessToken: string
): Promise<{ success: boolean; data?: FinalizeCoursePlanResponse; error?: string }> {
  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/finalize`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        session_uuid: sessionUuid,
        plan,
      }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      return {
        success: false,
        error: errorData.detail || `HTTP error ${response.status}`,
      }
    }

    const data = await response.json()
    return { success: true, data }
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error occurred',
    }
  }
}

/**
 * Generate content for a specific activity with streaming response
 */
export async function generateActivityContent(
  sessionUuid: string,
  activityUuid: string,
  activityName: string,
  activityDescription: string,
  chapterName: string,
  courseName: string,
  courseDescription: string,
  accessToken: string,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void,
  onError: (error: string) => void,
  prompt?: string
): Promise<void> {
  const data = {
    session_uuid: sessionUuid,
    activity_uuid: activityUuid,
    activity_name: activityName,
    activity_description: activityDescription,
    chapter_name: chapterName,
    course_name: courseName,
    course_description: courseDescription,
    prompt: prompt || undefined,
  }

  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/generate-activity`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(data),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `HTTP error ${response.status}`)
    }

    await processStream(response, onChunk, onComplete, onError)
  } catch (error) {
    onError(error instanceof Error ? error.message : 'Unknown error occurred')
  }
}

/**
 * Save AI-generated content to an activity
 */
export async function saveActivityContent(
  activityUuid: string,
  content: any,
  accessToken: string
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await fetch(`${getAPIUrl()}ai/courseplanning/save-activity-content`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        activity_uuid: activityUuid,
        content: content,
      }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      console.error('[saveActivityContent] Error:', errorData)
      return {
        success: false,
        error: errorData.detail || `HTTP error ${response.status}`,
      }
    }

    const data = await response.json()
    return { success: true }
  } catch (error) {
    console.error('[saveActivityContent] Exception:', error)
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error occurred',
    }
  }
}

/**
 * Get the current state of a course planning session
 */
export async function getCoursePlanningSession(
  sessionUuid: string,
  accessToken: string
): Promise<{ success: boolean; data?: CoursePlanningSession; error?: string }> {
  try {
    const response = await fetch(
      `${getAPIUrl()}ai/courseplanning/session/${sessionUuid}`,
      {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      }
    )

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      return {
        success: false,
        error: errorData.detail || `HTTP error ${response.status}`,
      }
    }

    const data = await response.json()
    return { success: true, data }
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error occurred',
    }
  }
}

/**
 * Process Server-Sent Events stream
 */
async function processStream(
  response: Response,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void | Promise<void>,
  onError: (error: string) => void
): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) {
    onError('No response body')
    return
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Process SSE events (each event starts with "data: ")
      const lines = buffer.split('\n')
      buffer = ''

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i]

        if (line.startsWith('data: ')) {
          const jsonStr = line.slice(6) // Remove "data: " prefix
          try {
            const event: StreamChunk = JSON.parse(jsonStr)

            if (event.type === 'chunk' && event.content) {
              onChunk(event.content)
            } else if (event.type === 'done' && event.session_uuid) {
              await onComplete(event.session_uuid)
            } else if (event.type === 'error' && event.message) {
              onError(event.message)
            }
          } catch {
            // Incomplete JSON, keep in buffer
            if (i === lines.length - 1) {
              buffer = line
            }
          }
        } else if (line.trim() !== '') {
          // Keep non-empty, non-data lines in buffer
          if (i === lines.length - 1) {
            buffer = line
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/**
 * Parse a course plan from streaming content
 */
export function parseCoursePlanFromStream(streamContent: string): CoursePlan | null {
  try {
    // Check if the content is an error message
    if (streamContent.trim().startsWith('Error:')) {
      console.error('Received error from AI:', streamContent)
      return null
    }

    // Clean up the response - remove markdown code blocks if present
    let cleaned = streamContent.trim()

    if (cleaned.includes('```json')) {
      const start = cleaned.indexOf('```json') + 7
      const end = cleaned.indexOf('```', start)
      if (end !== -1) {
        cleaned = cleaned.substring(start, end).trim()
      }
    } else if (cleaned.includes('```')) {
      const start = cleaned.indexOf('```') + 3
      const end = cleaned.indexOf('```', start)
      if (end !== -1) {
        cleaned = cleaned.substring(start, end).trim()
      }
    }

    // Try to find JSON object boundaries
    if (!cleaned.startsWith('{')) {
      const start = cleaned.indexOf('{')
      if (start !== -1) {
        cleaned = cleaned.substring(start)
      }
    }

    if (!cleaned.endsWith('}')) {
      const end = cleaned.lastIndexOf('}')
      if (end !== -1) {
        cleaned = cleaned.substring(0, end + 1)
      }
    }

    // Parse the JSON
    const data = JSON.parse(cleaned)
    return data as CoursePlan
  } catch (error) {
    console.error('Failed to parse course plan:', error)
    return null
  }
}

/**
 * Validate ProseMirror document structure
 */
function validateProseMirrorDoc(content: any): { valid: boolean; error?: string } {
  if (!content || typeof content !== 'object') {
    return { valid: false, error: 'Content must be a JSON object' }
  }

  if (content.type !== 'doc') {
    return { valid: false, error: `Root must have type "doc", got "${content.type}"` }
  }

  if (!Array.isArray(content.content)) {
    return { valid: false, error: 'Content must have a "content" array' }
  }

  // Valid block types that the editor supports
  const validBlockTypes = new Set([
    'paragraph', 'heading', 'bulletList', 'orderedList', 'listItem',
    'codeBlock', 'blockQuiz', 'flipcard', 'calloutInfo', 'calloutWarning',
    'blockEmbed', 'blockImage', 'blockVideo', 'blockPDF', 'blockMathEquation',
    'table', 'tableRow', 'tableCell', 'tableHeader', 'horizontalRule',
    'hardBreak', 'text', 'scenarios', 'blockUser', 'blockWebPreview', 'button', 'badge'
  ])

  function checkNode(node: any, path: string): { valid: boolean; error?: string } {
    if (!node || typeof node !== 'object') {
      return { valid: false, error: `Node at ${path} must be an object` }
    }

    if (!node.type) {
      return { valid: false, error: `Node at ${path} missing "type" field` }
    }

    if (!validBlockTypes.has(node.type) && node.type !== 'doc') {
      console.warn(`[parseActivityContent] Unknown block type "${node.type}" at ${path}`)
    }

    if (Array.isArray(node.content)) {
      for (let i = 0; i < node.content.length; i++) {
        const result = checkNode(node.content[i], `${path}.content[${i}]`)
        if (!result.valid) {
          return result
        }
      }
    }

    return { valid: true }
  }

  for (let i = 0; i < content.content.length; i++) {
    const result = checkNode(content.content[i], `content[${i}]`)
    if (!result.valid) {
      return result
    }
  }

  return { valid: true }
}

/**
 * Parse activity content from streaming content
 */
export function parseActivityContentFromStream(streamContent: string): any | null {
  try {
    // Check if the content is an error message
    if (streamContent.trim().startsWith('Error:')) {
      console.error('[parseActivityContent] Received error from AI:', streamContent)
      return null
    }

    // Clean up the response
    let cleaned = streamContent.trim()

    // Remove markdown code blocks if present
    if (cleaned.includes('```json')) {
      const start = cleaned.indexOf('```json') + 7
      const end = cleaned.indexOf('```', start)
      if (end !== -1) {
        cleaned = cleaned.substring(start, end).trim()
      }
    } else if (cleaned.includes('```')) {
      const start = cleaned.indexOf('```') + 3
      const end = cleaned.indexOf('```', start)
      if (end !== -1) {
        cleaned = cleaned.substring(start, end).trim()
      }
    }

    // Try to find JSON object boundaries
    if (!cleaned.startsWith('{')) {
      const start = cleaned.indexOf('{')
      if (start !== -1) {
        cleaned = cleaned.substring(start)
      }
    }

    if (!cleaned.endsWith('}')) {
      const end = cleaned.lastIndexOf('}')
      if (end !== -1) {
        cleaned = cleaned.substring(0, end + 1)
      }
    }

    // Parse the JSON
    const parsed = JSON.parse(cleaned)

    // Validate ProseMirror structure
    const validation = validateProseMirrorDoc(parsed)
    if (!validation.valid) {
      console.error('[parseActivityContent] Validation failed:', validation.error)
      console.error('[parseActivityContent] Parsed content:', JSON.stringify(parsed).substring(0, 500))
      return null
    }

    return parsed
  } catch (error) {
    console.error('[parseActivityContent] Failed to parse activity content:', error)
    console.error('[parseActivityContent] Raw content preview:', streamContent.substring(0, 500))
    return null
  }
}
