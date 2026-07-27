'use client'

import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext'
import { useAssignmentSubmission } from '@components/Contexts/Assignments/AssignmentSubmissionContext'
import {
  useAssignmentsTask,
  useAssignmentsTaskDispatch,
} from '@components/Contexts/Assignments/AssignmentsTaskContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI'
import {
  getAssignmentTask,
  getAssignmentTaskSubmissionsMe,
  getAssignmentTaskSubmissionsUser,
  handleAssignmentTaskSubmission,
  updateAssignmentTask,
} from '@services/courses/assignments'
import { queryKeys } from '@/lib/query/keys'
import { CheckCircle2, CircleAlert, Sparkles } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import React, { useEffect, useMemo, useState } from 'react'
import toast from 'react-hot-toast'

type EssayCriterion = {
  key: string
  label: string
  description: string
}

type EssayContents = {
  prompt: string
  min_words: string
  max_words: string
  rubric: EssayCriterion[]
}

type TaskEssayObjectProps = {
  view: 'teacher' | 'student' | 'grading'
  assignmentTaskUUID?: string
  user_id?: string
}

const DEFAULT_RUBRIC: EssayCriterion[] = [
  { key: 'content', label: '內容切題', description: '回應題目，觀點清楚，例子合適。' },
  { key: 'structure', label: '結構組織', description: '開頭、段落、承接和結尾清晰。' },
  { key: 'language', label: '語言表達', description: '用詞、句式和語氣準確。' },
  { key: 'mechanics', label: '錯別字與標點', description: '錯別字、標點和基本語法。' },
  { key: 'creativity', label: '創意與思考', description: '有個人思考、細節和吸引力。' },
]

const DEFAULT_CONTENTS: EssayContents = {
  prompt: '',
  min_words: '150',
  max_words: '400',
  rubric: DEFAULT_RUBRIC,
}

function normalizeContents(raw: any): EssayContents {
  const rubric = Array.isArray(raw?.rubric)
    ? raw.rubric
        .map((item: any, index: number) => ({
          key: String(item?.key || DEFAULT_RUBRIC[index]?.key || `criterion_${index + 1}`),
          label: String(item?.label || item?.title || DEFAULT_RUBRIC[index]?.label || `評分項 ${index + 1}`),
          description: String(item?.description || DEFAULT_RUBRIC[index]?.description || ''),
        }))
        .filter((item: EssayCriterion) => item.key && item.label)
    : DEFAULT_RUBRIC

  return {
    prompt: String(raw?.prompt || raw?.question || ''),
    min_words: String(raw?.min_words || ''),
    max_words: String(raw?.max_words || ''),
    rubric: rubric.length > 0 ? rubric : DEFAULT_RUBRIC,
  }
}

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  if (response instanceof Error && response.message) return response.message
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；')
  return fallback
}

function parseEssayReport(value: unknown) {
  if (!value || typeof value !== 'string') return null
  try {
    const parsed = JSON.parse(value)
    return parsed?.type === 'ai_essay_grading' ? parsed : null
  } catch {
    return null
  }
}

function wordCount(text: string) {
  const compact = text.replace(/\s+/g, '')
  return compact.length
}

function TaskEssayObject({ view, assignmentTaskUUID, user_id }: TaskEssayObjectProps) {
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const assignmentTaskState = useAssignmentsTask() as any
  const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
  const assignment = useAssignments() as any
  const assignmentSubmission = useAssignmentSubmission() as any
  const queryClient = useQueryClient()

  const assignmentSubmissionStatus = Array.isArray(assignmentSubmission) && assignmentSubmission.length > 0
    ? assignmentSubmission[0].submission_status
    : null
  const submissionIsFinal = view === 'student'
    && Array.isArray(assignmentSubmission)
    && assignmentSubmission.length > 0
    && !['PENDING', 'NOT_SUBMITTED'].includes(assignmentSubmissionStatus || '')

  const [contents, setContents] = useState<EssayContents>(DEFAULT_CONTENTS)
  const [essay, setEssay] = useState('')
  const [initialEssay, setInitialEssay] = useState('')
  const [userSubmissions, setUserSubmissions] = useState<any>(null)
  const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null)
  const [assignmentTaskOutsideProvider, setAssignmentTaskOutsideProvider] = useState<any>(null)
  const [manualGrade, setManualGrade] = useState('')
  const [isSavingGrade, setIsSavingGrade] = useState(false)

  const showSavingDisclaimer = view === 'student' && essay !== initialEssay
  const essayReport = useMemo(
    () => parseEssayReport(userSubmissionObject?.task_submission_grade_feedback),
    [userSubmissionObject?.task_submission_grade_feedback]
  )
  const essayMaxPoints = Number(
    assignmentTaskOutsideProvider?.max_grade_value ||
    assignmentTaskState?.assignmentTask?.max_grade_value ||
    0
  )

  useEffect(() => {
    if (view === 'teacher' && assignmentTaskState?.assignmentTask?.contents) {
      setContents(normalizeContents(assignmentTaskState.assignmentTask.contents))
    }
  }, [view, assignmentTaskState])

  async function loadTaskDefinition() {
    if (!assignmentTaskUUID || !access_token) return
    const res = await getAssignmentTask(assignmentTaskUUID, access_token)
    if (res.success) {
      setAssignmentTaskOutsideProvider(res.data)
      setContents(normalizeContents(res.data.contents || {}))
    }
  }

  async function loadOwnSubmission() {
    if (!assignmentTaskUUID || !access_token) return
    const res = await getAssignmentTaskSubmissionsMe(
      assignmentTaskUUID,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success && res.data) {
      setUserSubmissions(res.data)
      const saved = res.data.task_submission?.essay ?? res.data.task_submission?.answer ?? ''
      setEssay(saved)
      setInitialEssay(saved)
    }
  }

  async function loadUserSubmission() {
    if (!assignmentTaskUUID || !user_id || !access_token) return
    const res = await getAssignmentTaskSubmissionsUser(
      assignmentTaskUUID,
      user_id,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success && res.data) {
      setUserSubmissions(res.data)
      setUserSubmissionObject(res.data)
      const saved = res.data.task_submission?.essay ?? res.data.task_submission?.answer ?? ''
      setEssay(saved)
      setInitialEssay(saved)
      setManualGrade(String(res.data.grade ?? ''))
    }
  }

  useEffect(() => {
    if (view === 'student') {
      loadTaskDefinition()
      loadOwnSubmission()
    } else if (view === 'grading') {
      loadTaskDefinition()
      loadUserSubmission()
    }
  }, [view, assignmentTaskUUID, assignment, access_token, assignmentSubmissionStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  async function saveFC() {
    if (!assignmentTaskState?.assignmentTask?.assignment_task_uuid) return
    if (!contents.prompt.trim()) {
      toast.error('請先輸入作文題目。')
      return
    }
    const updatedContents = {
      ...contents,
      rubric: contents.rubric.filter((item) => item.key && item.label),
    }
    try {
      const res = await updateAssignmentTask(
        { contents: updatedContents },
        assignmentTaskState.assignmentTask.assignment_task_uuid,
        assignment.assignment_object.assignment_uuid,
        access_token
      )
      if (res.success) {
        assignmentTaskStateHook({ type: 'reload' })
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
        toast.success('作文題已更新')
      } else {
        toast.error(responseErrorMessage(res, '更新作文題失敗'))
      }
    } catch (error) {
      toast.error(responseErrorMessage(error, '更新作文題失敗'))
    }
  }

  async function submitFC() {
    if (!assignmentTaskUUID) return
    if (submissionIsFinal) {
      toast.error('這份作業已提交，請按「重做」後再修改作文。')
      return
    }
    if (!essay.trim()) {
      toast.error('請先輸入作文，再儲存本題。')
      return
    }
    const values = {
      assignment_task_submission_uuid: userSubmissions?.assignment_task_submission_uuid || null,
      task_submission: { essay },
      grade: 0,
      task_submission_grade_feedback: '',
    }
    try {
      const res = await handleAssignmentTaskSubmission(
        values,
        assignmentTaskUUID,
        assignment.assignment_object.assignment_uuid,
        access_token
      )
      if (res.success) {
        setUserSubmissions(res.data)
        setInitialEssay(essay)
        queryClient.invalidateQueries({
          queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid),
        })
        toast.success('作文已儲存，完成後請提交批改。')
      } else {
        toast.error(responseErrorMessage(res, '儲存作文失敗'))
      }
    } catch (error) {
      toast.error(responseErrorMessage(error, '儲存作文失敗'))
    }
  }

  async function saveEssayGradeFC(nextGrade?: number | string) {
    if (!assignmentTaskUUID || !userSubmissionObject || !access_token) return
    const parsedGrade = Number(nextGrade ?? manualGrade)
    if (!Number.isFinite(parsedGrade)) {
      toast.error('請輸入有效分數。')
      return
    }
    const clampedGrade = Math.max(0, Math.min(Math.round(parsedGrade), Math.max(essayMaxPoints, 0)))
    setIsSavingGrade(true)
    try {
      const res = await handleAssignmentTaskSubmission(
        {
          assignment_task_submission_uuid: userSubmissionObject.assignment_task_submission_uuid || null,
          task_submission: userSubmissionObject.task_submission || { essay },
          grade: clampedGrade,
          task_submission_grade_feedback: userSubmissionObject.task_submission_grade_feedback || '',
        },
        assignmentTaskUUID,
        assignment.assignment_object.assignment_uuid,
        access_token
      )
      if (res.success) {
        setUserSubmissions(res.data)
        setUserSubmissionObject(res.data)
        setManualGrade(String(clampedGrade))
        queryClient.invalidateQueries({
          queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid),
        })
        toast.success('作文分數已儲存，按「完成批改」後會更新總分。')
      } else {
        toast.error(responseErrorMessage(res, '儲存作文分數失敗'))
      }
    } catch (error) {
      toast.error(responseErrorMessage(error, '儲存作文分數失敗'))
    } finally {
      setIsSavingGrade(false)
    }
  }

  async function applyAiEssayGrade() {
    if (!essayReport) return
    const suggestedScore = Number(essayReport.score)
    if (!Number.isFinite(suggestedScore)) {
      toast.error('AI 建議分格式不正確，請手動輸入分數。')
      return
    }
    await saveEssayGradeFC(suggestedScore)
  }

  function updateCriterion(index: number, field: keyof EssayCriterion, value: string) {
    setContents((prev) => ({
      ...prev,
      rubric: prev.rubric.map((item, itemIndex) => (
        itemIndex === index ? { ...item, [field]: value } : item
      )),
    }))
  }

  return (
    <AssignmentBoxUI
      type="essay"
      view={view}
      saveFC={saveFC}
      submitFC={submitFC}
      gradeFC={view === 'grading' ? () => saveEssayGradeFC() : undefined}
      currentPoints={userSubmissionObject?.grade}
      maxPoints={
        essayMaxPoints
      }
      showSavingDisclaimer={showSavingDisclaimer}
    >
      <div className="flex w-full min-w-0 flex-col space-y-4">
        {view === 'teacher' && (
          <>
            <div className="rounded-lg border border-violet-100 bg-violet-50 px-3 py-2 text-xs font-semibold leading-relaxed text-violet-800">
              作文題會使用 AI 生成初步分數、分項評語和改善建議；老師仍可在提交記錄中覆核確認。
            </div>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-xs font-semibold text-slate-500">作文題目</span>
              <textarea
                value={contents.prompt}
                onChange={(e) => setContents((prev) => ({ ...prev, prompt: e.target.value }))}
                rows={3}
                placeholder="例如：以「一次難忘的校園活動」為題，寫一篇短文。"
                className="w-full min-w-0 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm"
              />
            </label>
            <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex min-w-0 flex-col gap-1">
                <span className="text-xs font-semibold text-slate-500">建議最少字數</span>
                <input
                  value={contents.min_words}
                  onChange={(e) => setContents((prev) => ({ ...prev, min_words: e.target.value }))}
                  className="w-full min-w-0 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm"
                />
              </label>
              <label className="flex min-w-0 flex-col gap-1">
                <span className="text-xs font-semibold text-slate-500">建議最多字數</span>
                <input
                  value={contents.max_words}
                  onChange={(e) => setContents((prev) => ({ ...prev, max_words: e.target.value }))}
                  className="w-full min-w-0 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm"
                />
              </label>
            </div>
            <div className="space-y-2">
              <p className="text-xs font-semibold text-slate-500">AI 評分項目</p>
              {contents.rubric.map((item, index) => (
                <div key={item.key} className="grid min-w-0 grid-cols-1 gap-2 rounded-lg border border-gray-100 bg-white p-3 sm:grid-cols-[minmax(0,160px)_minmax(0,1fr)]">
                  <input
                    value={item.label}
                    onChange={(e) => updateCriterion(index, 'label', e.target.value)}
                    className="w-full min-w-0 rounded-md border border-gray-200 px-2 py-1.5 text-sm font-semibold"
                  />
                  <input
                    value={item.description}
                    onChange={(e) => updateCriterion(index, 'description', e.target.value)}
                    className="w-full min-w-0 rounded-md border border-gray-200 px-2 py-1.5 text-sm"
                  />
                </div>
              ))}
            </div>
          </>
        )}

        {view === 'student' && (
          <>
            {contents.prompt && (
              <div className="rounded-lg border border-gray-100 bg-white px-3 py-3">
                <p className="text-sm font-semibold leading-relaxed text-slate-800 whitespace-pre-wrap">{contents.prompt}</p>
                {(contents.min_words || contents.max_words) && (
                  <p className="mt-2 text-xs font-medium text-slate-400">
                    建議字數：{contents.min_words || '不限'} - {contents.max_words || '不限'}
                  </p>
                )}
              </div>
            )}
            <textarea
              value={essay}
              onChange={(e) => !submissionIsFinal && setEssay(e.target.value)}
              readOnly={submissionIsFinal}
              rows={12}
              placeholder="在這裡輸入作文。可以先寫草稿，按「儲存本題答案」，完成全部題目後再提交批改。"
              className="min-h-[260px] w-full min-w-0 rounded-lg border-2 border-gray-200 bg-white px-3 py-3 text-sm leading-relaxed outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-100"
            />
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs font-semibold text-slate-400">
              <span>已輸入約 {wordCount(essay)} 字</span>
              {submissionIsFinal && <span>已提交，如需修改請按「重做」。</span>}
            </div>
          </>
        )}

        {view === 'grading' && (
          <>
            <div className="rounded-lg border border-gray-100 bg-white px-3 py-3">
              <p className="text-xs font-bold text-slate-400">作文題目</p>
              <p className="mt-1 text-sm font-semibold leading-relaxed text-slate-800 whitespace-pre-wrap">
                {contents.prompt || '未設定作文題目'}
              </p>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-3">
              <div className="mb-2 flex min-w-0 flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-bold text-slate-400">學生作文</p>
                <span className="text-xs font-semibold text-slate-400">約 {wordCount(essay)} 字</span>
              </div>
              <p className="min-w-0 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700">
                {essay || '學生未提交作文內容'}
              </p>
            </div>

            {essayReport ? (
              <div className="min-w-0 space-y-4 rounded-xl border border-violet-100 bg-white p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <div className="flex items-center gap-2 text-violet-700">
                      <Sparkles size={16} />
                      <p className="text-sm font-black">AI 作文評分報告</p>
                    </div>
                    <p className="mt-1 text-xs font-medium leading-relaxed text-slate-500">
                      {essayReport.summary}
                    </p>
                  </div>
                  <div className="rounded-xl bg-violet-50 px-4 py-3 text-center">
                    <p className="text-[10px] font-bold uppercase text-violet-500">AI 建議分</p>
                    <p className="text-3xl font-black text-violet-900">{essayReport.score}</p>
                  </div>
                </div>

                <div className="space-y-2">
                  {Array.isArray(essayReport.criteria) && essayReport.criteria.map((criterion: any) => {
                    const score = Math.max(0, Math.min(100, Number(criterion.score || 0)))
                    return (
                      <div key={criterion.key} className="min-w-0 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                        <div className="mb-1 flex min-w-0 items-center justify-between gap-3">
                          <span className="min-w-0 break-words text-xs font-bold text-slate-700">{criterion.label}</span>
                          <span className="text-xs font-black text-violet-700">{score}/100</span>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-white">
                          <div className="h-full rounded-full bg-violet-500" style={{ width: `${score}%` }} />
                        </div>
                        {criterion.comment && (
                          <p className="mt-1.5 text-xs leading-relaxed text-slate-500">{criterion.comment}</p>
                        )}
                      </div>
                    )
                  })}
                </div>

                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <EssayList title="優點" items={essayReport.strengths} tone="emerald" />
                  <EssayList title="改善建議" items={essayReport.improvements} tone="amber" />
                </div>
                <EssayList title="下一步練習" items={essayReport.next_steps} tone="sky" />
                <div className="rounded-lg border border-violet-100 bg-violet-50 px-3 py-3">
                  <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                    <label className="flex w-full min-w-0 flex-col gap-1 sm:w-auto">
                      <span className="text-xs font-black text-violet-700">老師確認分數</span>
                      <div className="flex min-w-0 items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          max={essayMaxPoints || undefined}
                          value={manualGrade}
                          onChange={(event) => setManualGrade(event.target.value)}
                          className="h-10 w-full min-w-0 rounded-lg border border-violet-200 bg-white px-3 text-sm font-black text-slate-900 outline-none focus:border-violet-500 sm:w-28"
                        />
                        <span className="text-xs font-bold text-violet-700">/ {essayMaxPoints || '-'}</span>
                      </div>
                    </label>
                    <div className="grid w-full min-w-0 grid-cols-1 gap-2 sm:flex sm:w-auto sm:flex-wrap">
                      <button
                        type="button"
                        onClick={applyAiEssayGrade}
                        disabled={isSavingGrade}
                        className="inline-flex h-10 w-full items-center justify-center rounded-lg bg-violet-700 px-3 text-xs font-black text-white hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
                      >
                        {isSavingGrade ? '儲存中' : '套用 AI 建議分'}
                      </button>
                      <button
                        type="button"
                        onClick={() => saveEssayGradeFC()}
                        disabled={isSavingGrade}
                        className="inline-flex h-10 w-full items-center justify-center rounded-lg border border-violet-200 bg-white px-3 text-xs font-black text-violet-700 hover:bg-violet-50 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
                      >
                        {isSavingGrade ? '儲存中' : '儲存作文分數'}
                      </button>
                    </div>
                  </div>
                  <p className="mt-2 text-xs font-semibold leading-relaxed text-violet-700">
                    分數儲存後，再按下方「完成批改」確認覆核；學生端和成績表才會視為老師已確認。
                  </p>
                </div>
                <div className="flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800">
                  <CircleAlert size={14} className="mt-0.5 shrink-0" />
                  <span>AI 分數只作建議，作文最終分數仍建議由老師覆核確認。</span>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-start gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs font-semibold leading-relaxed text-slate-500">
                  <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                  <span>提交後系統會嘗試產生 AI 作文評分報告；如未看到報告，請稍後重新批改或由老師人工給分。</span>
                </div>
                <div className="rounded-lg border border-gray-100 bg-white px-3 py-3">
                  <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                    <label className="flex w-full min-w-0 flex-col gap-1 sm:w-auto">
                      <span className="text-xs font-black text-slate-600">老師人工分數</span>
                      <div className="flex min-w-0 items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          max={essayMaxPoints || undefined}
                          value={manualGrade}
                          onChange={(event) => setManualGrade(event.target.value)}
                          className="h-10 w-full min-w-0 rounded-lg border border-gray-200 bg-white px-3 text-sm font-black text-slate-900 outline-none focus:border-slate-500 sm:w-28"
                        />
                        <span className="text-xs font-bold text-slate-500">/ {essayMaxPoints || '-'}</span>
                      </div>
                    </label>
                    <button
                      type="button"
                      onClick={() => saveEssayGradeFC()}
                      disabled={isSavingGrade}
                      className="inline-flex h-10 w-full items-center justify-center rounded-lg bg-slate-900 px-3 text-xs font-black text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
                    >
                      {isSavingGrade ? '儲存中' : '儲存作文分數'}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </AssignmentBoxUI>
  )
}

function EssayList({ title, items, tone }: { title: string; items: any; tone: 'emerald' | 'amber' | 'sky' }) {
  const values = Array.isArray(items) ? items.filter(Boolean) : []
  if (values.length === 0) return null
  const toneClass = {
    emerald: 'border-emerald-100 bg-emerald-50 text-emerald-800',
    amber: 'border-amber-100 bg-amber-50 text-amber-800',
    sky: 'border-sky-100 bg-sky-50 text-sky-800',
  }[tone]
  return (
    <div className={`rounded-lg border px-3 py-2 ${toneClass}`}>
      <p className="text-xs font-black">{title}</p>
      <ul className="mt-1 space-y-1">
        {values.map((item: string, index: number) => (
          <li key={`${title}-${index}`} className="text-xs font-medium leading-relaxed">
            {index + 1}. {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default TaskEssayObject
