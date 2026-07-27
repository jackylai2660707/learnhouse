'use client'

import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getUriWithOrg } from '@services/config/config'
import { discardStartedSelfTest, getMySelfTestAttempts, startSelfTest, submitSelfTest } from '@services/self-tests/self-tests'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import Link from 'next/link'
import { AlertCircle, BookOpenCheck, CheckCircle2, ClipboardList, FileQuestion, ListChecks, RotateCcw, SlidersHorizontal } from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast'
import {
  SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT,
  SIMPLE_SELF_TEST_MAX_QUESTION_COUNT,
} from '@lib/question-bank-readiness'
import { formatZhHkDateTime } from '@lib/date-format'

type Props = {
  org_id: number
  orgslug: string
}

type Attempt = {
  attempt_uuid: string
  status: string
  score: number
  max_score: number
  percentage: number
  teacher_score?: number | null
  teacher_feedback?: string
  questions: any[]
  creation_date: string
}

const TYPE_META: Record<string, { label: string; Icon: any }> = {
  QUIZ: { label: '選擇題', Icon: ListChecks },
  FORM: { label: '填空題', Icon: FileQuestion },
  SHORT_ANSWER: { label: '短問答', Icon: BookOpenCheck },
}
function splitPracticeTags(value: string) {
  return value.split(/[,，、;；\n]+/).map((tag) => tag.trim()).filter(Boolean)
}

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message ?? response?.HTTPmessage
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
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

function statusLabel(status: string) {
  if (status === 'STARTED') return '進行中'
  if (status === 'SUBMITTED') return '已提交'
  if (status === 'REVIEWED') return '老師已確認'
  return status || '未開始'
}

function isCompletedAttempt(attempt: Attempt | null) {
  return !!attempt && attempt.status !== 'STARTED'
}

function attemptSummary(attempt: Attempt | null) {
  if (!attempt) return { total: 0, correct: 0, partial: 0, practice: 0 }
  const questions = attempt.questions || []
  const correct = questions.filter((question) => Number(question.grade || 0) >= Number(question.max_grade || 100)).length
  const partial = questions.filter((question) => {
    const grade = Number(question.grade || 0)
    return grade > 0 && grade < Number(question.max_grade || 100)
  }).length
  return {
    total: questions.length,
    correct,
    partial,
    practice: Math.max(0, questions.length - correct - partial),
  }
}

function completedAttemptStats(attempts: Attempt[]) {
  if (attempts.length === 0) {
    return {
      count: 0,
      latestPercentage: '-',
      bestPercentage: '-',
      correctSummary: '-',
    }
  }
  const sortedAttempts = [...attempts].sort((left, right) => (
    Date.parse(right.creation_date || '') - Date.parse(left.creation_date || '')
  ))
  const percentages = attempts
    .map((attempt) => Number(attempt.percentage))
    .filter((value) => Number.isFinite(value))
  const questionTotals = attempts.reduce((total, attempt) => {
    const summary = attemptSummary(attempt)
    return {
      correct: total.correct + summary.correct,
      questions: total.questions + summary.total,
    }
  }, { correct: 0, questions: 0 })

  return {
    count: attempts.length,
    latestPercentage: `${Math.round(Number(sortedAttempts[0]?.percentage || 0))}%`,
    bestPercentage: percentages.length > 0 ? `${Math.round(Math.max(...percentages))}%` : '-',
    correctSummary: questionTotals.questions > 0
      ? `${questionTotals.correct}/${questionTotals.questions}`
      : '-',
  }
}

function questionResult(question: any) {
  const grade = Number(question.grade || 0)
  const maxGrade = Number(question.max_grade || 100)
  if (grade >= maxGrade) {
    return { label: '答對', className: 'bg-emerald-50 text-emerald-700 border-emerald-100' }
  }
  if (grade > 0) {
    return { label: '部分正確', className: 'bg-amber-50 text-amber-700 border-amber-100' }
  }
  return { label: '要練習', className: 'bg-rose-50 text-rose-700 border-rose-100' }
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

function selectedQuizOptions(question: any, value: any) {
  const selected = new Set(
    (value?.submissions || [])
      .filter((submission: any) => isSimpleBooleanTrue(submission.answer))
      .map((submission: any) => `${submission.questionUUID}:${submission.optionUUID}`)
  )
  return (question.contents?.questions || []).flatMap((quizQuestion: any) => (
    (quizQuestion.options || [])
      .filter((option: any) => selected.has(`${quizQuestion.questionUUID}:${option.optionUUID}`))
      .map((option: any) => option.text || option.option)
  )).filter(Boolean)
}

function formAnswers(value: any) {
  return (value?.submissions || [])
    .map((submission: any) => String(submission.answer || '').trim())
    .filter(Boolean)
}

function studentAnswerText(question: any, value: any) {
  if (question.assignment_type === 'QUIZ') {
    const answers = selectedQuizOptions(question, value)
    return answers.length ? answers.join('、') : '未選擇'
  }
  if (question.assignment_type === 'FORM') {
    const answers = formAnswers(value)
    return answers.length ? answers.join('、') : '未填寫'
  }
  return String(value?.answer || '').trim() || '未作答'
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

function isQuestionAnswered(question: any, raw: any) {
  const value = raw || {}
  if (question.assignment_type === 'QUIZ') {
    const questions = question.contents?.questions || []
    return questions.length > 0 && questions.every((quizQuestion: any) => (
      (quizQuestion.options || []).length > 0 && (quizQuestion.options || []).some((option: any) => (
        isSimpleBooleanTrue(value[`${quizQuestion.questionUUID}:${option.optionUUID}`])
      ))
    ))
  }
  if (question.assignment_type === 'FORM') {
    const questions = question.contents?.questions || []
    return questions.length > 0 && questions.every((formQuestion: any) => (
      (formQuestion.blanks || []).length > 0 && (formQuestion.blanks || []).every((blank: any) => (
        String(value[`${formQuestion.questionUUID}:${blank.blankUUID}`] || '').trim().length > 0
      ))
    ))
  }
  if (question.assignment_type === 'SHORT_ANSWER') {
    return String(value.answer || '').trim().length > 0
  }
  return false
}

function selfTestDraftKey(attemptUuid: string) {
  return `learnhouse:self-test:${attemptUuid}:answers`
}

function selfTestQuestionDomId(attemptQuestionUuid: string) {
  return `self-test-question-${String(attemptQuestionUuid || '').replace(/[^a-zA-Z0-9_-]/g, '')}`
}

function loadDraftAnswers(attemptUuid: string) {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(selfTestDraftKey(attemptUuid))
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function saveDraftAnswers(attemptUuid: string, value: Record<string, any>) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(selfTestDraftKey(attemptUuid), JSON.stringify(value))
  } catch {
    // Local draft persistence is a convenience only; submission still works.
  }
}

function clearDraftAnswers(attemptUuid: string) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(selfTestDraftKey(attemptUuid))
  } catch {
    // Ignore local storage failures.
  }
}

export default function SelfTestClient({ org_id, orgslug }: Props) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [questionCount, setQuestionCount] = React.useState(SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT)
  const [tags, setTags] = React.useState('')
  const [showPracticeOptions, setShowPracticeOptions] = React.useState(false)
  const [activeAttempt, setActiveAttempt] = React.useState<Attempt | null>(null)
  const [answers, setAnswers] = React.useState<Record<string, any>>({})
  const [startError, setStartError] = React.useState('')

  const attemptsQuery = useQuery({
    queryKey: queryKeys.selfTests.me(org_id),
    queryFn: async () => requireSuccess(
      getMySelfTestAttempts(org_id, accessToken),
      '載入自測記錄失敗'
    ),
    enabled: !!org_id && !!accessToken,
  })

  const startMutation = useMutation({
    mutationFn: async () => {
      setStartError('')
      const res = await startSelfTest({
        org_id,
        question_count: questionCount,
        tags: splitPracticeTags(tags),
      }, accessToken)
      if (res.success === false) throw new Error(responseErrorMessage(res, '暫時未能開始自測'))
      return res.data
    },
    onSuccess: (data) => {
      setActiveAttempt(data)
      setAnswers(data?.status === 'STARTED' && data?.attempt_uuid ? loadDraftAnswers(data.attempt_uuid) : {})
      setStartError('')
      const actualQuestionCount = Number(data?.question_count || data?.questions?.length || 0)
      const loadedExistingAttempt = latestStartedAttempt?.attempt_uuid === data?.attempt_uuid
      if (loadedExistingAttempt) {
        toast.success('已載入未完成自測')
      } else if (actualQuestionCount > 0 && actualQuestionCount < questionCount) {
        toast(
          `題庫暫時只有 ${actualQuestionCount} 題可用，本次先練這些題目。`,
          { duration: 6000 }
        )
      } else {
        toast.success('自測已開始')
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.me(org_id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.studentHome(org_id) })
    },
    onError: (error: any) => {
      const message = error.message || '暫時未能開始自測'
      setStartError(message)
      toast.error(message)
    },
  })

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!activeAttempt) return null
      const payload = activeAttempt.questions.map((question) => ({
        attempt_question_uuid: question.attempt_question_uuid,
        answer: buildAnswer(question, answers[question.attempt_question_uuid] || {}),
      }))
      const res = await submitSelfTest(activeAttempt.attempt_uuid, payload, accessToken)
      if (res.success === false) throw new Error(responseErrorMessage(res, '提交失敗'))
      return res.data
    },
    onSuccess: (data) => {
      if (activeAttempt?.attempt_uuid) {
        clearDraftAnswers(activeAttempt.attempt_uuid)
      }
      if (data) setActiveAttempt(data)
      setAnswers({})
      toast.success('自測已提交')
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.me(org_id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.studentHome(org_id) })
    },
    onError: (error: any) => toast.error(error.message || '提交失敗'),
  })

  const discardAndRestartMutation = useMutation({
    mutationFn: async () => {
      if (!latestStartedAttempt?.attempt_uuid) return null
      const res = await discardStartedSelfTest(latestStartedAttempt.attempt_uuid, accessToken)
      if (res.success === false) throw new Error(responseErrorMessage(res, '未能放棄未完成自測'))
      clearDraftAnswers(latestStartedAttempt.attempt_uuid)
      return startSelfTest({
        org_id,
        question_count: questionCount,
        tags: splitPracticeTags(tags),
      }, accessToken)
    },
    onSuccess: (res: any) => {
      if (res?.success === false) {
        const message = responseErrorMessage(res, '未能開始新的自測')
        setStartError(message)
        toast.error(message)
        return
      }
      const data = res?.data
      if (data) {
        setActiveAttempt(data)
        setAnswers({})
        setStartError('')
        toast.success('已開始新的自測')
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.me(org_id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.selfTests.studentHome(org_id) })
    },
    onError: (error: any) => {
      const message = error.message || '未能重新開始自測'
      setStartError(message)
      toast.error(message)
    },
  })

  const attempts: Attempt[] = Array.isArray(attemptsQuery.data) ? attemptsQuery.data : []
  const latestStartedAttempt = attempts.find((attempt) => attempt.status === 'STARTED') || null
  const completedAttempts = attempts.filter((attempt) => attempt.status !== 'STARTED')
  const learningStats = completedAttemptStats(completedAttempts)
  const activeSummary = attemptSummary(activeAttempt)
  const queryError = attemptsQuery.error as any
  const answeredCount = activeAttempt?.status === 'STARTED'
    ? activeAttempt.questions.filter((question) => (
        isQuestionAnswered(question, answers[question.attempt_question_uuid] || {})
      )).length
    : 0
  const unansweredCount = activeAttempt?.status === 'STARTED'
    ? Math.max(0, activeAttempt.questions.length - answeredCount)
    : 0
  const firstUnansweredQuestionUuid = activeAttempt?.status === 'STARTED'
    ? activeAttempt.questions.find((question) => (
        !isQuestionAnswered(question, answers[question.attempt_question_uuid] || {})
      ))?.attempt_question_uuid
    : ''
  const canSubmitActiveAttempt = Boolean(activeAttempt?.status === 'STARTED' && unansweredCount === 0)

  function continueAttempt(attempt: Attempt) {
    setActiveAttempt(attempt)
    setAnswers(loadDraftAnswers(attempt.attempt_uuid))
    setStartError('')
  }

  function restartPractice() {
    setActiveAttempt(null)
    setAnswers({})
    setStartError('')
    startMutation.mutate()
  }

  function confirmDiscardAndRestart() {
    if (typeof window !== 'undefined') {
      const confirmed = window.confirm('這會清除目前未提交的自測答案，並重新抽題。確定要重新開始嗎？')
      if (!confirmed) return
    }
    discardAndRestartMutation.mutate()
  }

  function updateQuestionAnswer(attemptQuestionUuid: string, value: any) {
    setAnswers((current) => {
      const next = { ...current, [attemptQuestionUuid]: value }
      if (activeAttempt?.attempt_uuid) {
        saveDraftAnswers(activeAttempt.attempt_uuid, next)
      }
      return next
    })
  }

  function jumpToFirstUnansweredQuestion() {
    if (!firstUnansweredQuestionUuid || typeof document === 'undefined') return
    const target = document.getElementById(selfTestQuestionDomId(firstUnansweredQuestionUuid))
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => {
      const firstInput = target?.querySelector('input:not(:disabled), textarea:not(:disabled), button:not(:disabled)') as HTMLElement | null
      firstInput?.focus()
    }, 250)
  }

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-3xl font-black tracking-tight text-gray-950">自我練習</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              從老師題庫隨機抽選擇、填空、短問答，完成後會保留記錄。做錯也沒關係，可以再練一次。
            </p>
          </div>
          <button
            type="button"
            onClick={() => latestStartedAttempt ? continueAttempt(latestStartedAttempt) : startMutation.mutate()}
            disabled={!accessToken || startMutation.isPending || discardAndRestartMutation.isPending}
            className={`inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-bold text-white disabled:bg-gray-300 ${
              latestStartedAttempt ? 'bg-emerald-700 hover:bg-emerald-800' : 'bg-gray-950 hover:bg-black'
            }`}
          >
            <RotateCcw size={16} />
            {latestStartedAttempt
              ? '繼續未完成'
              : startMutation.isPending
                ? '準備中'
                : '開始新自測'}
          </button>
        </div>

        {queryError && (
          <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800 sm:flex-row sm:items-center sm:justify-between">
            <span>{queryError?.message || '自測記錄載入失敗，請稍後再試。'}</span>
            <button
              type="button"
              onClick={() => attemptsQuery.refetch()}
              className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700"
            >
              重新載入
            </button>
          </div>
        )}

        {!activeAttempt && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
            <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-bold uppercase text-gray-400">開始練習</p>
              <div className="mt-3 rounded-lg bg-gray-950 px-3 py-4 text-white">
                <p className="text-sm font-black">
                  {latestStartedAttempt ? '繼續未完成自測' : `${questionCount} 題簡單自測`}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-gray-300">
                  {latestStartedAttempt
                    ? '未提交前，系統會在本機暫存你輸入的答案。'
                    : '系統會從老師題庫抽選擇、填空、短問答。做錯也可以再練一次。'}
                </p>
                <button
                  type="button"
                  onClick={() => latestStartedAttempt ? continueAttempt(latestStartedAttempt) : startMutation.mutate()}
                  disabled={!accessToken || startMutation.isPending || discardAndRestartMutation.isPending}
                  className="mt-3 inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-white px-3 text-sm font-black text-gray-950 hover:bg-gray-100 disabled:bg-gray-300"
                >
                  <RotateCcw size={15} />
                  {latestStartedAttempt
                    ? '繼續未完成自測'
                    : startMutation.isPending
                      ? '準備中'
                      : '開始練習'}
                </button>
                {latestStartedAttempt && (
                  <button
                    type="button"
                    onClick={confirmDiscardAndRestart}
                    disabled={!accessToken || discardAndRestartMutation.isPending}
                    className="mt-2 inline-flex h-8 w-full items-center justify-center rounded-lg border border-white/20 px-3 text-xs font-bold text-gray-200 hover:bg-white/10 disabled:text-gray-400"
                  >
                    {discardAndRestartMutation.isPending ? '重新開始中' : '放棄未完成，重新開始'}
                  </button>
                )}
              </div>
              <button
                type="button"
                onClick={() => setShowPracticeOptions((value) => !value)}
                className="mt-4 inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-gray-200 px-3 text-xs font-bold text-gray-700 hover:bg-gray-50"
              >
                <SlidersHorizontal size={14} />
                {showPracticeOptions ? '收起自訂練習' : '自訂題數或練習範圍'}
              </button>
              {showPracticeOptions && (
                <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-3">
                  <label className="block text-sm font-bold text-gray-700">題數</label>
                  <select
                    value={questionCount}
                    onChange={(event) => {
                      const nextCount = Number(event.target.value) || SIMPLE_SELF_TEST_MAX_QUESTION_COUNT
                      setQuestionCount(Math.max(1, Math.min(nextCount, SIMPLE_SELF_TEST_MAX_QUESTION_COUNT)))
                    }}
                    className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
                  >
                    {[3, 5].map((count) => (
                      <option key={count} value={count}>{count} 題</option>
                    ))}
                  </select>
                  <label className="mt-4 block text-sm font-bold text-gray-700">練習範圍</label>
                  <input
                    value={tags}
                    onChange={(event) => setTags(event.target.value)}
                    placeholder="例如：分數、基礎"
                    className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
                  />
                  <p className="mt-3 text-xs text-gray-500">
                    不填也可以，系統會從老師共享題庫抽簡單題。多個範圍可用頓號、逗號或換行分隔。建議 3 題快速練習，最多 5 題，容易完成，也方便老師查看記錄。
                  </p>
                </div>
              )}
              {startError && (
                <SelfTestUnavailableNotice
                  message={startError}
                  orgslug={orgslug}
                  hasTags={!!tags.trim()}
                  isRetrying={startMutation.isPending}
                  onClearTags={() => {
                    setTags('')
                    setStartError('')
                    setShowPracticeOptions(true)
                  }}
                  onRetry={() => startMutation.mutate()}
                />
              )}
              <div className="mt-4 rounded-lg bg-cyan-50 px-3 py-3 text-xs leading-relaxed text-cyan-800">
                <p className="font-bold">練習方式很簡單</p>
                <p className="mt-1">按「開始新自測」後，系統會抽出老師已儲存的簡單題。完成後即時看到分數，老師也能在後台查看記錄。</p>
              </div>
            </section>

            <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="flex items-center gap-2">
                <ClipboardList size={18} className="text-gray-500" />
                <h2 className="text-sm font-black text-gray-900">最近自測記錄</h2>
              </div>
              {completedAttempts.length > 0 && (
                <SelfTestLearningEvidence stats={learningStats} />
              )}
              <div className="mt-4 space-y-2">
                {completedAttempts.slice(0, 8).map((attempt) => (
                  <button
                  key={attempt.attempt_uuid}
                  type="button"
                    onClick={() => {
                      setActiveAttempt(attempt)
                      setAnswers({})
                    }}
                    className="flex w-full items-center justify-between rounded-lg border border-gray-100 px-3 py-2 text-left hover:bg-gray-50"
                  >
                    <span className="text-sm font-semibold text-gray-800">{formatZhHkDateTime(attempt.creation_date)}</span>
                    <span className="flex items-center gap-2">
                      <span className="hidden text-xs font-bold text-gray-500 sm:inline">
                        答對 {attemptSummary(attempt).correct}/{attemptSummary(attempt).total}
                      </span>
                      <span className="text-sm font-black text-gray-950">{attempt.percentage}%</span>
                    </span>
                  </button>
                ))}
                {!queryError && completedAttempts.length === 0 && (
                  <div className="rounded-lg border border-dashed border-gray-200 p-6 text-center">
                    <BookOpenCheck className="mx-auto text-gray-300" size={34} />
                    <p className="mt-3 text-sm font-bold text-gray-800">先完成第一次自測</p>
                    <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-gray-500">
                      這裡會保留你的自測記錄和分數。現在可以從左邊開始一次簡單練習。
                    </p>
                  </div>
                )}
              </div>
            </section>
          </div>
        )}

        {activeAttempt && (
          <section className="space-y-4">
            <div className="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-xs font-bold uppercase text-gray-400">{statusLabel(activeAttempt.status)}</p>
                <p className="mt-1 text-xl font-black text-gray-950">
                  {activeAttempt.status === 'STARTED' ? `${activeAttempt.questions.length} 題` : `${activeAttempt.score}/${activeAttempt.max_score} 分（${activeAttempt.percentage}%）`}
                </p>
                {activeAttempt.status === 'STARTED' && (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <ResultPill label="已作答" value={`${answeredCount}/${activeAttempt.questions.length}`} tone={unansweredCount === 0 ? 'green' : 'amber'} />
                    <span className="text-xs font-semibold text-gray-500">
                      先完成全部題目，再提交留下記錄。
                    </span>
                    {unansweredCount > 0 && (
                      <button
                        type="button"
                        onClick={jumpToFirstUnansweredQuestion}
                        className="inline-flex h-7 items-center rounded-full border border-amber-200 bg-amber-50 px-3 text-xs font-bold text-amber-800 hover:border-amber-700"
                      >
                        還有 {unansweredCount} 題未作答，跳到第一題
                      </button>
                    )}
                  </div>
                )}
                {activeAttempt.teacher_feedback && (
                  <p className="mt-1 text-sm text-gray-600">老師評語：{activeAttempt.teacher_feedback}</p>
                )}
                {isCompletedAttempt(activeAttempt) && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <ResultPill label="已批改" value={`${activeSummary.total} 題`} tone="gray" />
                    <ResultPill label="答對" value={`${activeSummary.correct} 題`} tone="green" />
                    {activeSummary.partial > 0 && <ResultPill label="部分正確" value={`${activeSummary.partial} 題`} tone="amber" />}
                    <ResultPill label="要練習" value={`${activeSummary.practice} 題`} tone="rose" />
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setActiveAttempt(null)}
                  className="h-10 rounded-lg border border-gray-200 px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
                >
                  返回
                </button>
                {activeAttempt.status === 'STARTED' && (
                  <button
                    type="button"
                    onClick={() => submitMutation.mutate()}
                    disabled={!accessToken || submitMutation.isPending || !canSubmitActiveAttempt}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:bg-gray-300"
                  >
                    <CheckCircle2 size={16} />
                    {submitMutation.isPending ? '提交中' : unansweredCount > 0 ? '完成全部題目後提交' : '提交並留下記錄'}
                  </button>
                )}
                {activeAttempt.status !== 'STARTED' && (
                  <button
                    type="button"
                    onClick={restartPractice}
                    disabled={!accessToken || startMutation.isPending}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:bg-gray-300"
                  >
                    <RotateCcw size={16} />
                    {startMutation.isPending ? '準備中' : '再練一次'}
                  </button>
                )}
              </div>
            </div>

            <div className="space-y-3">
              {activeAttempt.questions.map((question, index) => (
                <QuestionPanel
                  key={question.attempt_question_uuid}
                  question={question}
                  index={index}
                  disabled={activeAttempt.status !== 'STARTED'}
                  value={answers[question.attempt_question_uuid] || question.answer || {}}
                  onChange={(value: any) => updateQuestionAnswer(question.attempt_question_uuid, value)}
                />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

function SelfTestLearningEvidence({
  stats,
}: {
  stats: ReturnType<typeof completedAttemptStats>
}) {
  const items = [
    { label: '完成次數', value: `${stats.count} 次` },
    { label: '最高分', value: stats.bestPercentage },
    { label: '最近分', value: stats.latestPercentage },
    { label: '答對題數', value: stats.correctSummary },
  ]
  return (
    <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="rounded-lg border border-emerald-100 bg-emerald-50/70 px-3 py-2">
          <p className="text-[11px] font-bold text-emerald-700">{item.label}</p>
          <p className="mt-1 text-base font-black text-gray-950">{item.value}</p>
        </div>
      ))}
    </div>
  )
}

function SelfTestUnavailableNotice({
  message,
  orgslug,
  hasTags,
  isRetrying,
  onClearTags,
  onRetry,
}: {
  message: string
  orgslug: string
  hasTags: boolean
  isRetrying: boolean
  onClearTags: () => void
  onRetry: () => void
}) {
  return (
    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-xs leading-relaxed text-amber-900">
      <div className="flex items-start gap-2">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-700" />
        <div className="min-w-0">
          <p className="font-black">暫時未能開始自我練習</p>
          <p className="mt-1 font-semibold">{message}</p>
          <p className="mt-2 text-amber-800">
            這不是學生操作錯誤。請先回課程學習，或等待老師加入選擇題、填空題和短問答。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onRetry}
              disabled={isRetrying}
              className="inline-flex h-8 items-center justify-center rounded-lg bg-amber-700 px-3 text-xs font-bold text-white hover:bg-amber-800 disabled:bg-amber-300"
            >
              {isRetrying ? '準備中' : '再試一次'}
            </button>
            {hasTags && (
              <button
                type="button"
                onClick={onClearTags}
                className="inline-flex h-8 items-center justify-center rounded-lg bg-amber-700 px-3 text-xs font-bold text-white hover:bg-amber-800"
              >
                清除練習範圍
              </button>
            )}
            <Link
              href={getUriWithOrg(orgslug, '/courses')}
              className="inline-flex h-8 items-center justify-center rounded-lg border border-amber-200 bg-white px-3 text-xs font-bold text-amber-900 hover:bg-amber-100"
            >
              返回課程
            </Link>
            <Link
              href={getUriWithOrg(orgslug, '/trail')}
              className="inline-flex h-8 items-center justify-center rounded-lg border border-amber-200 bg-white px-3 text-xs font-bold text-amber-900 hover:bg-amber-100"
            >
              查看學習進度
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}

function QuestionPanel({ question, index, disabled, value, onChange }: any) {
  const meta = TYPE_META[question.assignment_type] || { label: question.assignment_type, Icon: FileQuestion }
  const Icon = meta.Icon
  const prompt = question.contents?.prompt || question.description
  const result = questionResult(question)
  const answered = disabled || isQuestionAnswered(question, value)
  return (
    <article
      id={selfTestQuestionDomId(question.attempt_question_uuid)}
      className={`rounded-lg border bg-white p-4 shadow-sm scroll-mt-24 ${answered ? 'border-gray-200' : 'border-amber-200 ring-2 ring-amber-100'}`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-700">
          <Icon size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-black text-gray-950">第 {index + 1} 題</p>
            <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{meta.label}</span>
            {disabled && (
              <span className={`rounded-full border px-2 py-1 text-[11px] font-bold ${result.className}`}>
                {result.label} · {question.grade}/{question.max_grade}
              </span>
            )}
            {!answered && (
              <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-bold text-amber-800">
                未作答
              </span>
            )}
          </div>
          <p className="mt-2 text-sm text-gray-700">{prompt}</p>
          {question.hint && <p className="mt-1 text-xs text-gray-400">提示：{question.hint}</p>}
          <div className="mt-4">
            <AnswerInput question={question} disabled={disabled} value={value} onChange={onChange} />
          </div>
          {disabled && (
            <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
              <AnswerSummary label="你的答案" value={studentAnswerText(question, value)} />
              <AnswerSummary label="參考答案" value={answerKeyText(question)} />
              {question.feedback && (
                <div className="md:col-span-2">
                  <AnswerSummary label="系統回饋" value={question.feedback} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  )
}

function ResultPill({ label, value, tone }: { label: string; value: string; tone: 'gray' | 'green' | 'amber' | 'rose' }) {
  const tones = {
    gray: 'bg-gray-100 text-gray-700',
    green: 'bg-emerald-50 text-emerald-700',
    amber: 'bg-amber-50 text-amber-700',
    rose: 'bg-rose-50 text-rose-700',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-bold ${tones[tone]}`}>
      <span>{label}</span>
      <span className="tabular-nums">{value}</span>
    </span>
  )
}

function AnswerSummary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2">
      <p className="text-[11px] font-bold text-gray-400">{label}</p>
      <p className="mt-1 text-sm font-semibold text-gray-800">{value}</p>
    </div>
  )
}

function AnswerInput({ question, disabled, value, onChange }: any) {
  if (question.assignment_type === 'SHORT_ANSWER') {
    return (
      <input
        value={value.answer || ''}
        disabled={disabled}
        onChange={(event) => onChange({ ...value, answer: event.target.value })}
        className="h-10 w-full rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900 disabled:bg-gray-50"
        placeholder="輸入答案"
      />
    )
  }
  if (question.assignment_type === 'QUIZ') {
    return (
      <div className="space-y-3">
        {(question.contents?.questions || []).map((quizQuestion: any) => (
          <div key={quizQuestion.questionUUID} className="space-y-2">
            <p className="text-sm font-bold text-gray-800">{quizQuestion.questionText || quizQuestion.question}</p>
            {(quizQuestion.options || []).map((option: any) => {
              const key = `${quizQuestion.questionUUID}:${option.optionUUID}`
              return (
                <label key={key} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="radio"
                    name={`self-test-${question.attempt_question_uuid}-${quizQuestion.questionUUID}`}
                    disabled={disabled}
                    checked={isSimpleBooleanTrue(value[key])}
                    onChange={() => {
                      const next = { ...value }
                      ;(quizQuestion.options || []).forEach((item: any) => {
                        delete next[`${quizQuestion.questionUUID}:${item.optionUUID}`]
                      })
                      onChange({ ...next, [key]: true })
                    }}
                  />
                  {option.text || option.option}
                </label>
              )
            })}
          </div>
        ))}
      </div>
    )
  }
  if (question.assignment_type === 'FORM') {
    return (
      <div className="space-y-3">
        {(question.contents?.questions || []).map((formQuestion: any) => (
          <div key={formQuestion.questionUUID} className="space-y-2">
            <p className="text-sm font-bold text-gray-800">{formQuestion.questionText || formQuestion.question}</p>
            {(formQuestion.blanks || []).map((blank: any) => {
              const key = `${formQuestion.questionUUID}:${blank.blankUUID}`
              return (
                <input
                  key={key}
                  value={value[key] || ''}
                  disabled={disabled}
                  onChange={(event) => onChange({ ...value, [key]: event.target.value })}
                  className="h-10 w-full rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900 disabled:bg-gray-50"
                  placeholder={blank.placeholder || blank.label || '填寫答案'}
                />
              )
            })}
          </div>
        ))}
      </div>
    )
  }
  return <p className="text-sm text-gray-500">自測目前只支援選擇題、填空題和短問答。</p>
}

function buildAnswer(question: any, raw: any) {
  if (question.assignment_type === 'QUIZ') {
    const submissions: any[] = []
    ;(question.contents?.questions || []).forEach((quizQuestion: any) => {
      ;(quizQuestion.options || []).forEach((option: any) => {
        const key = `${quizQuestion.questionUUID}:${option.optionUUID}`
        submissions.push({
          questionUUID: quizQuestion.questionUUID,
          optionUUID: option.optionUUID,
          answer: !!raw[key],
        })
      })
    })
    return { submissions }
  }
  if (question.assignment_type === 'FORM') {
    const submissions: any[] = []
    ;(question.contents?.questions || []).forEach((formQuestion: any) => {
      ;(formQuestion.blanks || []).forEach((blank: any) => {
        const key = `${formQuestion.questionUUID}:${blank.blankUUID}`
        submissions.push({
          questionUUID: formQuestion.questionUUID,
          blankUUID: blank.blankUUID,
          answer: raw[key] || '',
        })
      })
    })
    return { submissions }
  }
  return raw
}
