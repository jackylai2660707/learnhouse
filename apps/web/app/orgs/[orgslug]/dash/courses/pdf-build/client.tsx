'use client'

import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  BookCopy,
  BrainCircuit,
  CheckCircle2,
  FileText,
  Loader2,
  Sparkles,
  UploadCloud,
  X,
} from 'lucide-react'
import { useRouter } from 'next/navigation'
import toast from 'react-hot-toast'
import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { getUriWithOrg } from '@services/config/config'
import {
  createPDFBuildIdempotencyKey,
  createPDFBuildRecoveryController,
  formatPDFBuildError,
  getPDFCourseBuild,
  hasDegradedPDFBuildIndexing,
  isTerminalPDFBuild,
  listActiveMyPDFCourseBuilds,
  PDF_BUILD_STAGE_LABELS,
  PDFCourseBuildJob,
  PDFBuildStage,
  pdfBuildStorageKey,
  startPDFCourseBuild,
  validatePDFBuildFiles,
} from '@services/ai/courseplanning'

interface PDFCourseBuildClientProps {
  orgslug: string
}

const buildStages: PDFBuildStage[] = ['extracting', 'planning', 'creating', 'indexing']

const languageOptions = [
  { value: 'zh-Hant', label: '繁體中文' },
  { value: 'zh-Hans', label: '简体中文' },
  { value: 'en', label: 'English' },
] as const

function isPdfFile(file: File) {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

function formatSize(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatIssue(issue: PDFCourseBuildJob['error'] | PDFCourseBuildJob['warning']) {
  return issue ? formatPDFBuildError(issue) : ''
}

export default function PDFCourseBuildClient({ orgslug }: PDFCourseBuildClientProps) {
  const router = useRouter()
  const session = useLHSession() as any
  const org = useOrg() as any
  const accessToken = session?.data?.tokens?.access_token
  const orgId = org?.id as number | undefined
  const canUseAI = org?.config?.config?.resolved_features?.ai?.enabled === true

  const [files, setFiles] = useState<File[]>([])
  const [courseName, setCourseName] = useState('')
  const [instructions, setInstructions] = useState('')
  const [language, setLanguage] = useState<string>('zh-Hant')
  const [job, setJob] = useState<PDFCourseBuildJob | null>(null)
  const [requestError, setRequestError] = useState('')
  const [pollDelay, setPollDelay] = useState(2000)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [announcedStatus, setAnnouncedStatus] = useState('')
  const startKeyRef = useRef<string | null>(null)
  const recoveryControllerRef = useRef(createPDFBuildRecoveryController())

  const totalSize = useMemo(
    () => files.reduce((sum, file) => sum + file.size, 0),
    [files]
  )
  const isWorking = !!job && !isTerminalPDFBuild(job.stage)
  const canSubmit = canUseAI && !!accessToken && !!orgId && files.length > 0 && !isWorking && !isSubmitting
  const buildError = formatIssue(job?.error) || requestError
  const stageIndex = job && job.stage !== 'failed'
    ? buildStages.indexOf(job.stage)
    : -1

  const clearStoredJob = (currentOrgId: number) => {
    window.localStorage.removeItem(pdfBuildStorageKey(currentOrgId))
  }

  const storeActiveJob = (snapshot: PDFCourseBuildJob) => {
    if (isTerminalPDFBuild(snapshot.stage)) {
      clearStoredJob(snapshot.org_id)
    } else {
      window.localStorage.setItem(pdfBuildStorageKey(snapshot.org_id), snapshot.job_uuid)
    }
  }

  useEffect(() => {
    const updateVisibility = () => {
      setPollDelay(document.visibilityState === 'hidden' ? 10_000 : 2_000)
    }
    updateVisibility()
    document.addEventListener('visibilitychange', updateVisibility)
    return () => document.removeEventListener('visibilitychange', updateVisibility)
  }, [])

  useEffect(() => {
    if (!job) {
      setAnnouncedStatus('')
      return
    }
    const issue = formatIssue(job.error) || formatIssue(job.warning)
    setAnnouncedStatus(`PDF 建課狀態：${PDF_BUILD_STAGE_LABELS[job.stage]}${issue ? `，${issue}` : ''}`)
  }, [job?.error?.code, job?.stage, job?.warning?.code])

  useEffect(() => {
    if (!orgId || !accessToken) return
    let cancelled = false
    const recoveryGeneration = recoveryControllerRef.current.beginRecovery()

    const recoverActiveJob = async () => {
      const storageKey = pdfBuildStorageKey(orgId)
      const rememberedUuid = window.localStorage.getItem(storageKey)
      const response = await listActiveMyPDFCourseBuilds(orgId, accessToken)
      if (cancelled || !recoveryControllerRef.current.isCurrent(recoveryGeneration) || !response.success) return

      const recovered = response.data?.find((item) => item.job_uuid === rememberedUuid)
        ?? response.data?.[0]
      if (!recovered) {
        if (rememberedUuid) window.localStorage.removeItem(storageKey)
        return
      }
      window.localStorage.setItem(storageKey, recovered.job_uuid)
      setJob(recovered)
    }

    void recoverActiveJob()
    return () => { cancelled = true }
  }, [accessToken, orgId])

  useEffect(() => {
    if (!orgId || !accessToken || !job || isTerminalPDFBuild(job.stage)) return
    let cancelled = false
    let timer: number | undefined
    const pollingGeneration = recoveryControllerRef.current.currentGeneration()

    const poll = async () => {
      const response = await getPDFCourseBuild(orgId, job.job_uuid, accessToken)
      if (cancelled || !recoveryControllerRef.current.isCurrent(pollingGeneration)) return
      if (response.success && response.data) {
        setRequestError('')
        setJob(response.data)
        storeActiveJob(response.data)
        if (isTerminalPDFBuild(response.data.stage)) return
      } else if (response.error) {
        setRequestError(response.error)
      }
      timer = window.setTimeout(() => void poll(), pollDelay)
    }

    timer = window.setTimeout(() => void poll(), pollDelay)
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [accessToken, job?.job_uuid, job?.stage, orgId, pollDelay])

  const addFiles = (incoming: FileList | File[]) => {
    const list = Array.from(incoming)
    const pdfs = list.filter(isPdfFile)
    const rejected = list.length - pdfs.length
    if (rejected > 0) toast.error('只支援 PDF 文件')
    const nextFiles = [...files, ...pdfs]
    const validationError = validatePDFBuildFiles(nextFiles)
    if (validationError) {
      toast.error(validationError)
      return
    }
    setFiles(nextFiles)
    startKeyRef.current = null
  }

  const removeFile = (index: number) => {
    setFiles((current) => current.filter((_, fileIndex) => fileIndex !== index))
    startKeyRef.current = null
  }

  const handleBuild = async () => {
    if (!orgId || !accessToken || !files.length) return

    recoveryControllerRef.current.beginSubmission()
    setIsSubmitting(true)
    setJob(null)
    setRequestError('')
    clearStoredJob(orgId)
    try {
      const response = await startPDFCourseBuild(
        orgId,
        files,
        accessToken,
        startKeyRef.current ?? (startKeyRef.current = createPDFBuildIdempotencyKey()),
        { courseName, instructions, language, autoIndex: true },
      )

      if (!response.success || !response.data) {
        const message = response.error || 'PDF 建課暫時失敗，請稍後重試。'
        setRequestError(message)
        toast.error(message)
        return
      }

      startKeyRef.current = null
      setJob(response.data)
      storeActiveJob(response.data)
      if (isTerminalPDFBuild(response.data.stage)) {
        toast.success(response.data.stage === 'done' ? 'PDF 課程已建立' : 'PDF 建課未完成')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const goToCourse = () => {
    const courseUuid = job?.draft_course?.course_uuid
    if (!courseUuid) return
    router.push(getUriWithOrg(orgslug, `/dash/courses/course/${courseUuid.replace('course_', '')}/content`))
  }

  return (
    <div className="h-full w-full bg-[#f8f8f8] px-6 lg:px-10">
      <div className="mb-6 pt-6">
        <Breadcrumbs
          items={[
            { label: '課程', href: '/dash/courses', icon: <BookCopy size={14} /> },
            { label: 'PDF 智能建課', icon: <FileText size={14} /> },
          ]}
        />
        <div className="mt-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold">PDF 智能建課</h1>
            <p className="mt-1 text-sm text-gray-500">上傳教材，生成課程、練習與學生 AI 問答知識庫。</p>
          </div>
          <button
            onClick={() => router.push(getUriWithOrg(orgslug, '/dash/courses'))}
            className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-xs font-bold text-gray-700 nice-shadow transition hover:scale-105"
          >
            返回課程
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="rounded-lg bg-white p-5 nice-shadow">
          <label
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              addFiles(event.dataTransfer.files)
            }}
            className="flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-200 bg-gray-50 px-5 py-8 text-center transition hover:border-black hover:bg-white"
          >
            <UploadCloud size={34} className="text-gray-500" />
            <span className="mt-4 text-sm font-bold text-gray-900">拖放 PDF 或點擊上傳</span>
            <span className="mt-1 text-xs text-gray-500">最多 8 份 PDF</span>
            <input
              type="file"
              accept="application/pdf,.pdf"
              multiple
              className="hidden"
              disabled={isWorking}
              onChange={(event) => {
                if (event.target.files) addFiles(event.target.files)
                event.target.value = ''
              }}
            />
          </label>

          {files.length > 0 && (
            <div className="mt-5 space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold text-gray-500">
                <span>已選教材</span>
                <span>{files.length} 份 / {formatSize(totalSize)}</span>
              </div>
              <div className="space-y-2">
                {files.map((file, index) => (
                  <div key={`${file.name}:${file.size}:${file.lastModified}:${index}`} className="flex items-center justify-between rounded-lg border border-gray-100 bg-white px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <FileText size={16} className="shrink-0 text-red-500" />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-gray-900">{file.name}</p>
                        <p className="text-xs text-gray-400">{formatSize(file.size)}</p>
                      </div>
                    </div>
                    <button onClick={() => removeFile(index)} disabled={isWorking} aria-label={`移除 ${file.name}`} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40">
                      <X size={15} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-gray-700">課程名稱</label>
              <input value={courseName} onChange={(event) => { startKeyRef.current = null; setCourseName(event.target.value) }} disabled={isWorking} placeholder="留空則由 AI 判斷" className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-black disabled:bg-gray-50" />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="pdf-build-language" className="text-xs font-bold text-gray-700">語言</label>
              <select id="pdf-build-language" value={language} onChange={(event) => { startKeyRef.current = null; setLanguage(event.target.value) }} disabled={isWorking} className="h-[38px] w-full rounded-lg border border-gray-200 bg-white px-3 text-sm font-semibold text-gray-700 outline-none transition focus:border-black disabled:bg-gray-50">
                {languageOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
          </div>

          <div className="mt-4 space-y-1.5">
            <label className="text-xs font-bold text-gray-700">老師要求</label>
            <textarea value={instructions} onChange={(event) => { startKeyRef.current = null; setInstructions(event.target.value) }} disabled={isWorking} rows={7} placeholder="例如：對象是中一學生；每章要有基礎任務和挑戰任務；多加入地圖判讀或實驗思考題。" className="w-full resize-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-black disabled:bg-gray-50" />
          </div>

          <div className="mt-5 flex justify-end">
            <button onClick={handleBuild} disabled={!canSubmit} className={`flex items-center gap-2 rounded-lg bg-black px-5 py-2 text-xs font-bold text-white nice-shadow transition ${canSubmit ? 'hover:scale-105' : 'cursor-not-allowed opacity-50'}`}>
              {isWorking ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
              <span>{isWorking && job ? PDF_BUILD_STAGE_LABELS[job.stage] : '生成課程'}</span>
            </button>
          </div>
        </section>

        <aside aria-label="PDF 建課狀態" className="rounded-lg bg-white p-5 nice-shadow">
          <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">{announcedStatus}</p>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <BrainCircuit size={18} className="text-purple-600" />
              <h2 className="text-sm font-bold text-gray-900">生成狀態</h2>
            </div>
            {job && <span aria-label={`進度 ${job.progress_current} / ${job.progress_total}`} className="text-xs font-semibold tabular-nums text-gray-500">{job.progress_current} / {job.progress_total}</span>}
          </div>

          <div className="mt-5 space-y-3">
            {buildStages.map((item, index) => {
              const done = job?.stage === 'done' || stageIndex > index
              const active = job?.stage === item
              return (
                <div key={item} className="flex items-center gap-3">
                  <div className={`flex h-7 w-7 items-center justify-center rounded-full ${done ? 'bg-green-500 text-white' : active ? 'bg-black text-white' : 'bg-gray-100 text-gray-400'}`}>
                    {done ? <CheckCircle2 size={15} /> : active ? <Loader2 size={14} className="animate-spin" /> : <span className="text-xs">•</span>}
                  </div>
                  <span className={`text-sm font-semibold ${active ? 'text-black' : done ? 'text-green-700' : 'text-gray-400'}`}>{PDF_BUILD_STAGE_LABELS[item]}</span>
                </div>
              )
            })}
          </div>

          {!canUseAI && <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">此組織尚未啟用 AI 功能。</div>}

          {buildError && <div className="mt-5 flex gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"><AlertTriangle size={16} className="mt-0.5 shrink-0" /><span>{buildError}</span></div>}

          {job?.warning && <div className="mt-5 flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"><AlertTriangle size={16} className="mt-0.5 shrink-0" /><span>{formatIssue(job.warning)}</span></div>}

          {job?.draft_course && isTerminalPDFBuild(job.stage) && (
            <div className="mt-5 space-y-4">
              <div className="rounded-lg border border-green-200 bg-green-50 p-3">
                <div className="flex items-center gap-2 text-sm font-bold text-green-800"><CheckCircle2 size={16} />課程草稿已建立</div>
                <p className="mt-1 text-xs text-green-700">尚未發佈，請由老師檢查後再決定是否公開。</p>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-md bg-white p-2"><p className="text-gray-400">章節</p><p className="text-lg font-bold text-gray-900">{job.chapters_created}</p></div>
                  <div className="rounded-md bg-white p-2"><p className="text-gray-400">課堂</p><p className="text-lg font-bold text-gray-900">{job.activities_created}</p></div>
                  <div className="rounded-md bg-white p-2"><p className="text-gray-400">PDF</p><p className="text-lg font-bold text-gray-900">{job.source_documents_created}</p></div>
                  <div className="rounded-md bg-white p-2"><p className="text-gray-400">RAG chunks</p><p className="text-lg font-bold text-gray-900">{job.indexing?.chunks ?? 0}</p></div>
                </div>
              </div>

              {hasDegradedPDFBuildIndexing(job) && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">課程草稿已建立，但知識庫索引未完整完成。你仍可先檢查及編輯課程。</div>}

              <button onClick={goToCourse} className="flex w-full items-center justify-center gap-2 rounded-lg bg-black px-4 py-2 text-xs font-bold text-white nice-shadow transition hover:scale-105">
                <span>前往課程編輯</span><ArrowRight size={15} />
              </button>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
