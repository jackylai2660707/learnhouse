'use client'

import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getQuestionBankItems } from '@services/question-bank/question-bank'
import { getOrgSelfTestAttempts, reviewSelfTestAttempt } from '@services/self-tests/self-tests'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { AlertCircle, BookOpenCheck, CheckCircle2, ClipboardList, ClipboardPenLine, Download, Search } from 'lucide-react'
import Link from 'next/link'
import React from 'react'
import toast from 'react-hot-toast'
import { getUriWithOrg } from '@services/config/config'
import { buildQuestionBankReadiness } from '@lib/question-bank-readiness'

type Props = {
  org_id: number
  orgslug: string
}

type TeacherScoreParseResult =
  | { ok: true; value: number | null }
  | { ok: false; message: string }

const SCHOOL_DATETIME_LOCALE = 'zh-HK'

function schoolDateTime(value: unknown) {
  const date = new Date(String(value || ''))
  if (!Number.isFinite(date.getTime())) return '-'
  return date.toLocaleString(SCHOOL_DATETIME_LOCALE, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function statusLabel(status: string) {
  if (status === 'STARTED') return '進行中'
  if (status === 'SUBMITTED') return '系統自動批改'
  if (status === 'REVIEWED') return '已確認'
  return status || '未開始'
}

function questionPrompt(question: any) {
  return (
    question.contents?.prompt
    || question.contents?.questions?.[0]?.questionText
    || question.contents?.questions?.[0]?.question
    || question.description
    || question.title
    || ''
  )
}

function isSimpleBooleanTrue(value: any) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value === 1
  if (typeof value === 'string') {
    return ['true', '1', 'yes', 'y', 'correct', 'right', '是', '對', '正確'].includes(
      value.trim().toLowerCase()
    )
  }
  return false
}

function selectedQuizOptions(question: any) {
  const selected = new Set(
    (question.answer?.submissions || [])
      .filter((submission: any) => isSimpleBooleanTrue(submission.answer))
      .map((submission: any) => `${submission.questionUUID}:${submission.optionUUID}`)
  )
  return (question.contents?.questions || []).flatMap((quizQuestion: any) => (
    (quizQuestion.options || [])
      .filter((option: any) => selected.has(`${quizQuestion.questionUUID}:${option.optionUUID}`))
      .map((option: any) => option.text || option.option)
  )).filter(Boolean)
}

function formAnswers(question: any) {
  return (question.answer?.submissions || [])
    .map((submission: any) => String(submission.answer || '').trim())
    .filter(Boolean)
}

function studentAnswerText(question: any) {
  if (question.assignment_type === 'QUIZ') {
    const answers = selectedQuizOptions(question)
    return answers.length ? answers.join('、') : '未選擇'
  }
  if (question.assignment_type === 'FORM') {
    const answers = formAnswers(question)
    return answers.length ? answers.join('、') : '未填寫'
  }
  const answer = String(question.answer?.answer || '').trim()
  return answer || '未作答'
}

function answerKeyText(question: any) {
  if (question.assignment_type === 'QUIZ') {
    const answers = (question.contents?.questions || []).flatMap((quizQuestion: any) => (
      (quizQuestion.options || [])
        .filter((option: any) => isSimpleBooleanTrue(option.assigned_right_answer))
        .map((option: any) => option.text || option.option)
    )).filter(Boolean)
    return answers.length ? answers.join('、') : '未設定'
  }
  if (question.assignment_type === 'FORM') {
    const answers = (question.contents?.questions || []).flatMap((formQuestion: any) => (
      (formQuestion.blanks || [])
        .map((blank: any) => blank.correctAnswer || blank.correct_answer)
    )).filter(Boolean)
    return answers.length ? answers.join('、') : '未設定'
  }
  const answers = question.contents?.correct_answers || question.contents?.accepted_answers || []
  if (Array.isArray(answers) && answers.length > 0) return answers.join('、')
  return String(question.contents?.correct_answer || '').trim() || '未設定'
}

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail || response?.data?.message || response?.HTTPmessage
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || item?.message || '')
      .filter(Boolean)
      .join('；') || fallback
  }
  return fallback
}

async function requireSuccess(responsePromise: Promise<any>, fallback: string) {
  const response = await responsePromise
  if (response?.success === false) {
    throw new Error(responseErrorMessage(response, fallback))
  }
  return response?.data
}

function csvCell(value: unknown) {
  const text = String(value ?? '').replace(/\r?\n/g, ' ').trim()
  if (/[",\n\r]/.test(text)) return `"${text.replace(/"/g, '""')}"`
  return text
}

function selfTestQuestionStats(attempt: any) {
  const questions = Array.isArray(attempt?.questions) ? attempt.questions : []
  const correct = questions.filter((question: any) => (
    Number(question.grade || 0) >= Number(question.max_grade || 100)
  )).length
  return { total: questions.length, correct }
}

function selfTestCsvStatus(status: string) {
  if (status === 'STARTED') return '進行中'
  if (status === 'SUBMITTED') return '系統自動批改'
  if (status === 'REVIEWED') return '老師已確認'
  return status || ''
}

function buildSelfTestEvidenceCsv(attempts: any[]) {
  const headers = [
    '學生姓名',
    '電郵',
    '狀態',
    '建立時間',
    '提交時間',
    '系統分數',
    '滿分',
    '百分比',
    '答對題數',
    '題數',
    '老師分數',
    '是否計入平時分',
    '老師評語',
    '自測編號',
  ]
  const rows = attempts.map((attempt: any) => {
    const stats = selfTestQuestionStats(attempt)
    return [
      attempt.user_name || '',
      attempt.user_email || '',
      selfTestCsvStatus(attempt.status),
      schoolDateTime(attempt.creation_date),
      schoolDateTime(attempt.submitted_at),
      attempt.score ?? '',
      attempt.max_score ?? '',
      Number.isFinite(Number(attempt.percentage)) ? `${Math.round(Number(attempt.percentage))}%` : '',
      stats.correct,
      stats.total,
      attempt.teacher_score ?? '',
      attempt.counts_for_grade ? '是' : '否',
      attempt.teacher_feedback || '',
      attempt.attempt_uuid || '',
    ]
  })
  return `\ufeff${[headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n')}`
}

function downloadSelfTestEvidenceCsv(attempts: any[], orgId: number) {
  if (typeof window === 'undefined') return
  const csvText = buildSelfTestEvidenceCsv(attempts)
  const date = new Date().toISOString().slice(0, 10)
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `learnhouse-self-tests-org-${orgId}-${date}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

export default function SelfTestsDashboardClient({ org_id, orgslug }: Props) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [selected, setSelected] = React.useState<any | null>(null)
  const [teacherScore, setTeacherScore] = React.useState('')
  const [teacherFeedback, setTeacherFeedback] = React.useState('')
  const [countsForGrade, setCountsForGrade] = React.useState(true)

  const attemptsQuery = useQuery({
    queryKey: queryKeys.selfTests.org(org_id),
    queryFn: async () => requireSuccess(
      getOrgSelfTestAttempts(org_id, accessToken),
      '載入自測記錄失敗'
    ),
    enabled: !!org_id && !!accessToken,
  })

  const questionBankQuery = useQuery({
    queryKey: queryKeys.questionBank.selfTestReadiness(org_id),
    queryFn: async () => requireSuccess(
      getQuestionBankItems({ org_id, visibility: 'ORG' }, accessToken),
      '載入題庫狀態失敗'
    ),
    enabled: !!org_id && !!accessToken,
    staleTime: 60_000,
  })

  const attempts = Array.isArray(attemptsQuery.data) ? attemptsQuery.data : []
  const questionBankItems = Array.isArray(questionBankQuery.data) ? questionBankQuery.data : []
  const questionBankReadiness = React.useMemo(
    () => buildQuestionBankReadiness(questionBankItems),
    [questionBankItems]
  )
  const selfTestPilotMetrics = React.useMemo(
    () => buildSelfTestPilotMetrics(attempts),
    [attempts]
  )
  const queryError = attemptsQuery.error as any
  const questionBankError = questionBankQuery.error as any
  const isRetryingAttempts = attemptsQuery.isFetching
  const filtered = attempts.filter((attempt: any) => {
    const q = search.trim().toLowerCase()
    if (!q) return true
    return [attempt.user_name, attempt.user_email, attempt.attempt_uuid].some((value) => String(value || '').toLowerCase().includes(q))
  })

  function parseTeacherScore(): TeacherScoreParseResult {
    const raw = teacherScore.trim()
    if (!raw) return { ok: true, value: null }
    const value = Number(raw)
    const maxScore = Number(selected?.max_score || 0)
    if (!Number.isFinite(value)) {
      return { ok: false, message: '請輸入有效分數' }
    }
    if (!Number.isInteger(value)) {
      return { ok: false, message: '分數請輸入整數' }
    }
    if (value < 0) {
      return { ok: false, message: '分數不能小於 0' }
    }
    if (value > maxScore) {
      return { ok: false, message: `分數不能超過滿分 ${maxScore}` }
    }
    return { ok: true, value }
  }

  function syncReviewForm(attempt: any) {
    setSelected(attempt)
    setTeacherScore(attempt.teacher_score ?? attempt.score ?? '')
    setTeacherFeedback(attempt.teacher_feedback || '')
    setCountsForGrade(!!attempt.counts_for_grade)
  }

  const reviewMutation = useMutation({
    mutationFn: async () => {
      if (!selected) return null
      const parsedScore = parseTeacherScore()
      if (!parsedScore.ok) throw new Error(parsedScore.message)
      const res = await reviewSelfTestAttempt(selected.attempt_uuid, {
        teacher_score: parsedScore.value,
        teacher_feedback: teacherFeedback,
        counts_for_grade: countsForGrade,
      }, accessToken)
      if (res.success === false) throw new Error(res?.data?.detail || '儲存失敗')
      return res.data
    },
    onSuccess: (data) => {
      toast.success('自測記錄已更新')
      if (data) syncReviewForm(data)
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.org(org_id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(org_id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org_id) })
    },
    onError: (error: any) => toast.error(error.message || '儲存失敗'),
  })

  function selectAttempt(attempt: any) {
    syncReviewForm(attempt)
  }

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-6 py-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <Breadcrumbs
          items={[
            { label: '自測記錄', href: '/dash/self-tests', icon: <ClipboardList size={14} /> },
          ]}
        />

        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-tight text-gray-950">自測記錄</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              查看學生從題庫抽選擇、填空、短問答完成的自測，老師可以選擇是否計入平時分。
            </p>
          </div>
          <button
            type="button"
            onClick={() => downloadSelfTestEvidenceCsv(filtered, org_id)}
            disabled={filtered.length === 0}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-800 hover:border-gray-900 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
          >
            <Download size={16} />
            匯出目前列表
          </button>
        </div>

        <SelfTestReadinessPanel
          orgslug={orgslug}
          readiness={questionBankReadiness}
          isLoading={questionBankQuery.isLoading}
          error={questionBankError}
          isRetrying={questionBankQuery.isFetching}
          onRetry={() => questionBankQuery.refetch()}
        />
        <SelfTestEvidencePanel
          orgslug={orgslug}
          metrics={selfTestPilotMetrics}
          readiness={questionBankReadiness}
        />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[420px_1fr]">
          <section className="space-y-3">
            {queryError && (
              <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800 sm:flex-row sm:items-center sm:justify-between">
                <span>{queryError?.message || '自測記錄載入失敗，請稍後再試。'}</span>
                <button
                  type="button"
                  onClick={() => attemptsQuery.refetch()}
                  disabled={isRetryingAttempts}
                  className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isRetryingAttempts ? '重新載入中' : '重新載入自測記錄'}
                </button>
              </div>
            )}

            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜尋學生或自測編號"
                  className="h-10 w-full rounded-lg border border-gray-200 pl-9 pr-3 text-sm outline-none focus:border-gray-900"
                />
              </div>
            </div>
            <div className="space-y-2">
              {filtered.map((attempt: any) => (
                <button
                  type="button"
                  key={attempt.attempt_uuid}
                  onClick={() => selectAttempt(attempt)}
                  className={`w-full rounded-lg border bg-white p-4 text-left shadow-sm hover:border-gray-400 ${selected?.attempt_uuid === attempt.attempt_uuid ? 'border-gray-950' : 'border-gray-200'}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-black text-gray-950">{attempt.user_name || attempt.user_email || `用戶 ${attempt.user_id}`}</p>
                      <p className="mt-1 text-xs text-gray-500">{schoolDateTime(attempt.creation_date)}</p>
                    </div>
                    <span className="rounded-full bg-gray-950 px-2 py-1 text-[11px] font-bold text-white">{attempt.percentage}%</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge>{statusLabel(attempt.status)}</Badge>
                    <Badge>{attempt.score}/{attempt.max_score} 分</Badge>
                    {attempt.counts_for_grade && <Badge>計入平時分</Badge>}
                  </div>
                </button>
              ))}
              {!queryError && !attemptsQuery.isLoading && filtered.length === 0 && (
                <div className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center">
                  <ClipboardList className="mx-auto text-gray-300" size={38} />
                  <p className="mt-3 text-sm font-bold text-gray-800">
                    {search.trim() ? '沒有符合搜尋的自測記錄' : '暫時沒有自測記錄'}
                  </p>
                  <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-gray-500">
                    {search.trim()
                      ? '可以清除搜尋，再查看其他學生記錄。'
                      : '先建立校本題庫，學生完成自測後，記錄會自動出現在這裡。'}
                  </p>
                  <div className="mt-4 flex flex-col items-center justify-center gap-2 sm:flex-row">
                    {search.trim() ? (
                      <button
                        type="button"
                        onClick={() => setSearch('')}
                        className="inline-flex h-9 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
                      >
                        清除搜尋
                      </button>
                    ) : (
                      <Link
                        href={getUriWithOrg(orgslug, '/dash/question-bank')}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
                      >
                        <BookOpenCheck size={15} />
                        前往題庫
                      </Link>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>

          <main className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            {!selected && (
              <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                <ClipboardPenLine size={42} className="text-gray-300" />
                <p className="mt-3 text-sm font-bold text-gray-800">選擇一份自測</p>
                <p className="mt-1 text-xs text-gray-500">學生答案和老師確認操作會顯示在這裡。</p>
              </div>
            )}
            {selected && (
              <div className="space-y-5">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <p className="text-xs font-bold uppercase text-gray-400">自測</p>
                    <h2 className="mt-1 text-2xl font-black text-gray-950">{selected.user_name || selected.user_email}</h2>
                    <p className="mt-1 text-sm text-gray-500">{selected.attempt_uuid}</p>
                  </div>
                  <div className="text-left md:text-right">
                    <p className="text-xs font-bold uppercase text-gray-400">系統分數</p>
                    <p className="mt-1 text-2xl font-black text-gray-950">{selected.score}/{selected.max_score}</p>
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-3 md:grid-cols-[160px_1fr_auto]">
                  <input
                    type="number"
                    value={teacherScore}
                    min={0}
                    max={selected.max_score}
                    onChange={(event) => setTeacherScore(event.target.value)}
                    className="h-10 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
                    placeholder="老師分數"
                  />
                  <input
                    value={teacherFeedback}
                    onChange={(event) => setTeacherFeedback(event.target.value)}
                    className="h-10 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
                    placeholder="老師評語"
                  />
                  <label className="flex h-10 items-center gap-2 rounded-lg border border-gray-200 px-3 text-sm font-semibold text-gray-700">
                    <input type="checkbox" checked={countsForGrade} onChange={(event) => setCountsForGrade(event.target.checked)} />
                    計分
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => reviewMutation.mutate()}
                  disabled={!accessToken || reviewMutation.isPending || selected.status === 'STARTED'}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:bg-gray-300"
                >
                  <CheckCircle2 size={16} />
                  {reviewMutation.isPending ? '儲存中' : '儲存確認'}
                </button>

                <div className="space-y-3">
                  {(selected.questions || []).map((question: any, index: number) => (
                    <article key={question.attempt_question_uuid} className="rounded-lg border border-gray-200 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-black text-gray-950">第 {index + 1} 題：{question.title}</p>
                          <p className="mt-1 text-sm text-gray-600">{questionPrompt(question)}</p>
                        </div>
                        <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{question.grade}/{question.max_grade}</span>
                      </div>
                      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
                        <AnswerSummary label="學生答案" value={studentAnswerText(question)} />
                        <AnswerSummary label="正確答案" value={answerKeyText(question)} />
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}

function Badge({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{children}</span>
}

function AnswerSummary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2">
      <p className="text-[11px] font-bold text-gray-400">{label}</p>
      <p className="mt-1 text-sm font-semibold text-gray-800">{value}</p>
    </div>
  )
}

function buildSelfTestPilotMetrics(attempts: any[]) {
  const completedAttempts = attempts.filter((attempt: any) => attempt.status !== 'STARTED')
  const startedAttempts = attempts.filter((attempt: any) => attempt.status === 'STARTED')
  const submittedAttempts = attempts.filter((attempt: any) => attempt.status === 'SUBMITTED')
  const reviewedAttempts = attempts.filter((attempt: any) => attempt.status === 'REVIEWED')
  const countForGradeAttempts = completedAttempts.filter((attempt: any) => !!attempt.counts_for_grade)
  const studentKey = (attempt: any) => String(attempt.user_id || attempt.user_email || attempt.user_name || '').trim()
  const attemptStudents = new Set(attempts.map(studentKey).filter(Boolean))
  const completedStudents = new Set(completedAttempts.map(studentKey).filter(Boolean))
  const percentages = completedAttempts
    .map((attempt: any) => Number(attempt.percentage))
    .filter((value: number) => Number.isFinite(value))
  const averagePercentage = percentages.length > 0
    ? percentages.reduce((sum, value) => sum + value, 0) / percentages.length
    : null
  const latestCompletedAttempt = [...completedAttempts].sort((left: any, right: any) => (
    Date.parse(right.creation_date || '') - Date.parse(left.creation_date || '')
  ))[0]

  return {
    totalAttempts: attempts.length,
    completedAttempts: completedAttempts.length,
    startedAttempts: startedAttempts.length,
    submittedAttempts: submittedAttempts.length,
    reviewedAttempts: reviewedAttempts.length,
    countForGradeAttempts: countForGradeAttempts.length,
    studentCount: attemptStudents.size,
    completedStudentCount: completedStudents.size,
    averagePercentage,
    averagePercentageDisplay: averagePercentage === null ? '-' : `${Math.round(averagePercentage)}%`,
    latestCompletedAt: latestCompletedAttempt?.creation_date || '',
  }
}

function selfTestEvidenceStatus(
  metrics: ReturnType<typeof buildSelfTestPilotMetrics>,
  readiness: ReturnType<typeof buildQuestionBankReadiness>
) {
  if (!readiness.canStartPractice) {
    return {
      label: '先補題庫',
      detail: '自測需要全校共享的選擇題、填空題或短問答。至少先補 1 題即可試用，正式試行建議補到 3 題。',
      className: 'bg-amber-50 text-amber-700',
    }
  }
  if (!readiness.enoughForDefaultPractice) {
    return {
      label: '可先試用',
      detail: `題庫已有 ${readiness.total} 題可用簡單題，學生可以先練現有題目；正式試行建議補到 3 題。`,
      className: 'bg-blue-50 text-blue-700',
    }
  }
  if (metrics.totalAttempts === 0) {
    return {
      label: '可開始試行',
      detail: '題庫已可用。請安排學生完成一次 3 題自測，這裡會保留分數和作答記錄。',
      className: 'bg-blue-50 text-blue-700',
    }
  }
  if (metrics.startedAttempts > 0) {
    return {
      label: '有未完成',
      detail: `${metrics.startedAttempts} 份自測正在進行中。學生完成提交後，老師就能看到分數和答案。`,
      className: 'bg-amber-50 text-amber-700',
    }
  }
  if (metrics.submittedAttempts > 0) {
    return {
      label: '待老師確認',
      detail: `${metrics.submittedAttempts} 份自測已由系統自動批改，老師可選擇是否計入平時分。`,
      className: 'bg-cyan-50 text-cyan-700',
    }
  }
  return {
    label: '運作正常',
    detail: '學生已有完成記錄，老師可用自測成績作為平時表現或課後練習證據。',
    className: 'bg-emerald-50 text-emerald-700',
  }
}

function SelfTestEvidencePanel({
  orgslug,
  metrics,
  readiness,
}: {
  orgslug: string
  metrics: ReturnType<typeof buildSelfTestPilotMetrics>
  readiness: ReturnType<typeof buildQuestionBankReadiness>
}) {
  const status = selfTestEvidenceStatus(metrics, readiness)
  const items = [
    {
      label: '題庫可用題',
      value: `${readiness.total} 題`,
      detail: readiness.enoughForDefaultPractice ? '足夠 3 題快速自測' : '建議補到至少 3 題',
    },
    {
      label: '完成自測',
      value: `${metrics.completedAttempts} 次`,
      detail: `${metrics.completedStudentCount} 名學生有完成記錄`,
    },
    {
      label: '平均分',
      value: metrics.averagePercentageDisplay,
      detail: '只計已完成自測',
    },
    {
      label: '待確認',
      value: `${metrics.submittedAttempts} 份`,
      detail: '系統已批改，老師可確認',
    },
    {
      label: '計入平時分',
      value: `${metrics.countForGradeAttempts} 份`,
      detail: '老師已標記可計分',
    },
  ]

  return (
    <section className="rounded-lg border border-cyan-100 bg-cyan-50/70 p-4">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div className="max-w-2xl">
          <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">校內自測摘要</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h2 className="text-base font-black text-gray-950">學生是否有自主練習，一眼看清</h2>
            <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-black ${status.className}`}>
              {status.label}
            </span>
          </div>
          <p className="mt-1 text-sm leading-relaxed text-gray-600">
            自測只用選擇、填空、短問答，維護簡單；學生完成後自動留下分數、答案和老師確認狀態。
          </p>
          <p className="mt-2 text-xs font-semibold leading-relaxed text-cyan-800">{status.detail}</p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:min-w-[760px] xl:grid-cols-5">
          {items.map((item) => (
            <div key={item.label} className="rounded-lg border border-white bg-white/85 px-3 py-3">
              <p className="text-xs font-bold text-gray-500">{item.label}</p>
              <p className="mt-1 text-xl font-black text-gray-950">{item.value}</p>
              <p className="mt-1 text-[11px] font-medium leading-snug text-gray-500">{item.detail}</p>
            </div>
          ))}
        </div>
      </div>
      <div className="mt-3 flex flex-col gap-2 border-t border-cyan-100 pt-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs font-semibold text-cyan-800">
          最近完成：{metrics.latestCompletedAt ? schoolDateTime(metrics.latestCompletedAt) : '暫時沒有完成記錄'}
        </p>
        <div className="flex flex-wrap gap-2">
          <Link
            href={getUriWithOrg(orgslug, '/dash/question-bank')}
            className="inline-flex h-8 items-center justify-center rounded-lg border border-cyan-200 bg-white px-3 text-xs font-bold text-cyan-800 hover:border-cyan-700"
          >
            管理題庫
          </Link>
          <Link
            href={getUriWithOrg(orgslug, '/dash/gradebook')}
            className="inline-flex h-8 items-center justify-center rounded-lg bg-cyan-700 px-3 text-xs font-bold text-white hover:bg-cyan-800"
          >
            查看成績表
          </Link>
        </div>
      </div>
    </section>
  )
}

function SelfTestReadinessPanel({
  orgslug,
  readiness,
  isLoading,
  error,
  isRetrying,
  onRetry,
}: {
  orgslug: string
  readiness: ReturnType<typeof buildQuestionBankReadiness>
  isLoading: boolean
  error?: Error | null
  isRetrying?: boolean
  onRetry?: () => void
}) {
  if (isLoading) {
    return (
      <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p className="text-sm font-black text-gray-950">正在檢查題庫準備狀態...</p>
        <p className="mt-1 text-xs text-gray-500">確認全校共享題庫是否已有可供學生自測的簡單題。</p>
      </section>
    )
  }

  if (error) {
    return (
      <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex gap-3">
            <AlertCircle className="mt-0.5 shrink-0 text-amber-700" size={18} />
            <div>
              <p className="text-sm font-black text-amber-900">暫時無法確認題庫狀態</p>
              <p className="mt-1 text-xs font-semibold leading-relaxed text-amber-800">
                {error.message || '題庫狀態載入失敗。學生自測仍可嘗試開始，如沒有題目系統會提示。'}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                disabled={isRetrying}
                className="inline-flex h-9 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-bold text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isRetrying ? '重新檢查中' : '重新檢查題庫'}
              </button>
            )}
            <Link
              href={getUriWithOrg(orgslug, '/dash/question-bank')}
              className="inline-flex h-9 items-center justify-center rounded-lg bg-amber-700 px-3 text-xs font-bold text-white hover:bg-amber-800"
            >
              查看題庫
            </Link>
          </div>
        </div>
      </section>
    )
  }

  if (!readiness.canStartPractice) {
    const fallbackMessage = readiness.aiFallbackStarterItems > 0
      ? `題庫有 ${readiness.aiFallbackStarterItems} 道 AI 備用題，請老師先改成正式題目和答案後，學生才可以自測。`
      : ''
    return (
      <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex gap-3">
            <AlertCircle className="mt-0.5 shrink-0 text-amber-700" size={18} />
            <div>
              <p className="text-sm font-black text-amber-900">學生暫時未能自測</p>
              <p className="mt-1 max-w-3xl text-xs font-semibold leading-relaxed text-amber-800">
                {fallbackMessage ||
                (readiness.incompleteSimpleItems > 0
                  ? `題庫有 ${readiness.incompleteSimpleItems} 道簡單題，但題目、選項或正確答案未完整，暫時不能給學生自測。`
                  : '全校共享題庫還沒有內容完整的選擇題、填空題或短問答。先建立幾道共享簡單題，學生就可以開始自我練習，老師也能看到記錄。')}
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Link
              href={getUriWithOrg(orgslug, '/dash/question-bank')}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-amber-700 px-3 text-xs font-bold text-white hover:bg-amber-800"
            >
              <BookOpenCheck size={14} />
              新增簡單題
            </Link>
            <Link
              href={getUriWithOrg(orgslug, '/dash/assignments')}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-amber-300 bg-white px-3 text-xs font-bold text-amber-800 hover:bg-amber-100"
            >
              <ClipboardList size={14} />
              從作業加入題目
            </Link>
          </div>
        </div>
      </section>
    )
  }

  if (!readiness.enoughForDefaultPractice) {
    return (
      <section className="rounded-lg border border-blue-100 bg-blue-50/80 p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-sm font-black text-blue-950">自測可先試用，建議補到 3 題</p>
            <p className="mt-1 text-xs font-semibold leading-relaxed text-blue-800">
              全校共享題庫已有 {readiness.total} 道內容完整的簡單題。學生可以先練現有題目；正式校內試行建議至少 3 題，才算一次完整短練習。
              {readiness.aiFallbackStarterItems > 0 ? ` 另有 ${readiness.aiFallbackStarterItems} 道 AI 備用題需要先改成正式題目。` : ''}
              {readiness.incompleteSimpleItems > 0 ? ` 另有 ${readiness.incompleteSimpleItems} 道簡單題需要補齊答案後才可用。` : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <ReadinessBadge label="選擇題" value={readiness.counts.QUIZ} />
            <ReadinessBadge label="填空題" value={readiness.counts.FORM} />
            <ReadinessBadge label="短問答" value={readiness.counts.SHORT_ANSWER} />
            <Link
              href={getUriWithOrg(orgslug, '/dash/question-bank')}
              className="inline-flex h-8 items-center justify-center rounded-full bg-blue-700 px-3 text-[11px] font-bold text-white hover:bg-blue-800"
            >
              補充題庫
            </Link>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="rounded-lg border border-emerald-100 bg-emerald-50/80 p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-sm font-black text-emerald-950">自測已準備好</p>
          <p className="mt-1 text-xs font-semibold leading-relaxed text-emerald-800">
            全校共享題庫已有 {readiness.total} 道內容完整的簡單題。學生可以直接開始 3 題快速自測。
            {readiness.aiFallbackStarterItems > 0 ? ` 另有 ${readiness.aiFallbackStarterItems} 道 AI 備用題需要先改成正式題目。` : ''}
            {readiness.incompleteSimpleItems > 0 ? ` 另有 ${readiness.incompleteSimpleItems} 道簡單題需要補齊答案後才可用。` : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ReadinessBadge label="選擇題" value={readiness.counts.QUIZ} />
          <ReadinessBadge label="填空題" value={readiness.counts.FORM} />
          <ReadinessBadge label="短問答" value={readiness.counts.SHORT_ANSWER} />
          <Link
            href={getUriWithOrg(orgslug, '/dash/question-bank')}
            className="inline-flex h-8 items-center justify-center rounded-full bg-emerald-700 px-3 text-[11px] font-bold text-white hover:bg-emerald-800"
          >
            管理題庫
          </Link>
        </div>
      </div>
    </section>
  )
}

function ReadinessBadge({ label, value }: { label: string; value: number }) {
  return (
    <span className="inline-flex h-8 items-center rounded-full bg-white px-3 text-[11px] font-bold text-emerald-800">
      {label}：{value}
    </span>
  )
}
