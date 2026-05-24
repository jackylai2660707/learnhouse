'use client'

import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getMySelfTestAttempts, startSelfTest, submitSelfTest } from '@services/self-tests/self-tests'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpenCheck, CheckCircle2, ClipboardList, Code2, FileQuestion, Hash, ListChecks, RotateCcw } from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast'

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
  QUIZ: { label: 'Quiz', Icon: ListChecks },
  FORM: { label: 'Fill blanks', Icon: FileQuestion },
  CODE: { label: 'Code', Icon: Code2 },
  SHORT_ANSWER: { label: 'Short answer', Icon: BookOpenCheck },
  NUMBER_ANSWER: { label: 'Number', Icon: Hash },
}

export default function SelfTestClient({ org_id }: Props) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [questionCount, setQuestionCount] = React.useState(5)
  const [tags, setTags] = React.useState('')
  const [activeAttempt, setActiveAttempt] = React.useState<Attempt | null>(null)
  const [answers, setAnswers] = React.useState<Record<string, any>>({})

  const attemptsQuery = useQuery({
    queryKey: ['self-tests-me', org_id],
    queryFn: async () => (await getMySelfTestAttempts(org_id, accessToken)).data,
    enabled: !!org_id && !!accessToken,
  })

  const startMutation = useMutation({
    mutationFn: async () => {
      const res = await startSelfTest({
        org_id,
        question_count: questionCount,
        tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean),
      }, accessToken)
      if (res.success === false) throw new Error(res?.data?.detail || 'Could not start self-test')
      return res.data
    },
    onSuccess: (data) => {
      setActiveAttempt(data)
      setAnswers({})
      toast.success('Self-test started')
      queryClient.invalidateQueries({ queryKey: ['self-tests-me', org_id] })
    },
    onError: (error: any) => toast.error(error.message || 'Could not start self-test'),
  })

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!activeAttempt) return null
      const payload = activeAttempt.questions.map((question) => ({
        attempt_question_uuid: question.attempt_question_uuid,
        answer: buildAnswer(question, answers[question.attempt_question_uuid] || {}),
      }))
      const res = await submitSelfTest(activeAttempt.attempt_uuid, payload, accessToken)
      if (res.success === false) throw new Error(res?.data?.detail || 'Submit failed')
      return res.data
    },
    onSuccess: (data) => {
      if (data) setActiveAttempt(data)
      toast.success('Self-test submitted')
      queryClient.invalidateQueries({ queryKey: ['self-tests-me', org_id] })
    },
    onError: (error: any) => toast.error(error.message || 'Submit failed'),
  })

  const attempts: Attempt[] = attemptsQuery.data || []
  const completedAttempts = attempts.filter((attempt) => attempt.status !== 'STARTED')

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-3xl font-black tracking-tight text-gray-950">Self-test</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              Practice with random questions from the shared teacher question bank. Every attempt is recorded for teacher review.
            </p>
          </div>
          <button
            type="button"
            onClick={() => startMutation.mutate()}
            disabled={startMutation.isPending}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:bg-gray-300"
          >
            <RotateCcw size={16} />
            New self-test
          </button>
        </div>

        {!activeAttempt && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
            <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-bold uppercase text-gray-400">Setup</p>
              <label className="mt-4 block text-sm font-bold text-gray-700">Question count</label>
              <input
                type="number"
                min={1}
                max={50}
                value={questionCount}
                onChange={(event) => setQuestionCount(Number(event.target.value))}
                className="mt-2 h-10 w-full rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
              />
              <label className="mt-4 block text-sm font-bold text-gray-700">Tags</label>
              <input
                value={tags}
                onChange={(event) => setTags(event.target.value)}
                placeholder="python, basics"
                className="mt-2 h-10 w-full rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
              />
              <p className="mt-3 text-xs text-gray-500">Leave tags empty to draw from all shared questions.</p>
            </section>

            <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="flex items-center gap-2">
                <ClipboardList size={18} className="text-gray-500" />
                <h2 className="text-sm font-black text-gray-900">Your recent attempts</h2>
              </div>
              <div className="mt-4 space-y-2">
                {completedAttempts.slice(0, 8).map((attempt) => (
                  <button
                    key={attempt.attempt_uuid}
                    type="button"
                    onClick={() => setActiveAttempt(attempt)}
                    className="flex w-full items-center justify-between rounded-lg border border-gray-100 px-3 py-2 text-left hover:bg-gray-50"
                  >
                    <span className="text-sm font-semibold text-gray-800">{new Date(attempt.creation_date).toLocaleString()}</span>
                    <span className="text-sm font-black text-gray-950">{attempt.percentage}%</span>
                  </button>
                ))}
                {completedAttempts.length === 0 && (
                  <p className="rounded-lg border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">No attempts yet.</p>
                )}
              </div>
            </section>
          </div>
        )}

        {activeAttempt && (
          <section className="space-y-4">
            <div className="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-xs font-bold uppercase text-gray-400">{activeAttempt.status}</p>
                <p className="mt-1 text-xl font-black text-gray-950">
                  {activeAttempt.status === 'STARTED' ? `${activeAttempt.questions.length} questions` : `${activeAttempt.score}/${activeAttempt.max_score} points (${activeAttempt.percentage}%)`}
                </p>
                {activeAttempt.teacher_feedback && (
                  <p className="mt-1 text-sm text-gray-600">Teacher feedback: {activeAttempt.teacher_feedback}</p>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setActiveAttempt(null)}
                  className="h-10 rounded-lg border border-gray-200 px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
                >
                  Back
                </button>
                {activeAttempt.status === 'STARTED' && (
                  <button
                    type="button"
                    onClick={() => submitMutation.mutate()}
                    disabled={submitMutation.isPending}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:bg-gray-300"
                  >
                    <CheckCircle2 size={16} />
                    Submit
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
                  onChange={(value: any) => setAnswers((current) => ({ ...current, [question.attempt_question_uuid]: value }))}
                />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

function QuestionPanel({ question, index, disabled, value, onChange }: any) {
  const meta = TYPE_META[question.assignment_type] || { label: question.assignment_type, Icon: FileQuestion }
  const Icon = meta.Icon
  const prompt = question.contents?.prompt || question.description
  return (
    <article className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-700">
          <Icon size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-black text-gray-950">Question {index + 1}</p>
            <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{meta.label}</span>
            {question.grade > 0 && <span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">{question.grade}/{question.max_grade}</span>}
          </div>
          <p className="mt-2 text-sm text-gray-700">{prompt}</p>
          {question.hint && <p className="mt-1 text-xs text-gray-400">Hint: {question.hint}</p>}
          <div className="mt-4">
            <AnswerInput question={question} disabled={disabled} value={value} onChange={onChange} />
          </div>
        </div>
      </div>
    </article>
  )
}

function AnswerInput({ question, disabled, value, onChange }: any) {
  if (question.assignment_type === 'SHORT_ANSWER' || question.assignment_type === 'NUMBER_ANSWER') {
    return (
      <input
        value={value.answer || ''}
        disabled={disabled}
        onChange={(event) => onChange({ ...value, answer: event.target.value })}
        className="h-10 w-full rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900 disabled:bg-gray-50"
        placeholder="Your answer"
      />
    )
  }
  if (question.assignment_type === 'CODE') {
    return (
      <textarea
        value={value.source_code || ''}
        disabled={disabled}
        onChange={(event) => onChange({ ...value, source_code: event.target.value, language_id: question.contents?.language_id })}
        className="min-h-[180px] w-full rounded-lg border border-gray-200 bg-gray-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-gray-900 disabled:bg-gray-100 disabled:text-gray-500"
        placeholder="# Write code here"
      />
    )
  }
  if (question.assignment_type === 'QUIZ') {
    return (
      <div className="space-y-3">
        {(question.contents?.questions || []).map((quizQuestion: any) => (
          <div key={quizQuestion.questionUUID} className="space-y-2">
            <p className="text-sm font-bold text-gray-800">{quizQuestion.question}</p>
            {(quizQuestion.options || []).map((option: any) => {
              const key = `${quizQuestion.questionUUID}:${option.optionUUID}`
              return (
                <label key={key} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={!!value[key]}
                    onChange={(event) => onChange({ ...value, [key]: event.target.checked })}
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
            <p className="text-sm font-bold text-gray-800">{formQuestion.question}</p>
            {(formQuestion.blanks || []).map((blank: any) => {
              const key = `${formQuestion.questionUUID}:${blank.blankUUID}`
              return (
                <input
                  key={key}
                  value={value[key] || ''}
                  disabled={disabled}
                  onChange={(event) => onChange({ ...value, [key]: event.target.value })}
                  className="h-10 w-full rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900 disabled:bg-gray-50"
                  placeholder={blank.label || 'Blank answer'}
                />
              )
            })}
          </div>
        ))}
      </div>
    )
  }
  return <p className="text-sm text-gray-500">This question type is not supported in self-test yet.</p>
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
