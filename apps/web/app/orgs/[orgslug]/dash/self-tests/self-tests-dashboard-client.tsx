'use client'

import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getOrgSelfTestAttempts, reviewSelfTestAttempt } from '@services/self-tests/self-tests'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ClipboardList, ClipboardPenLine, Search } from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast'

type Props = {
  org_id: number
  orgslug: string
}

export default function SelfTestsDashboardClient({ org_id }: Props) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [selected, setSelected] = React.useState<any | null>(null)
  const [teacherScore, setTeacherScore] = React.useState('')
  const [teacherFeedback, setTeacherFeedback] = React.useState('')
  const [countsForGrade, setCountsForGrade] = React.useState(true)

  const attemptsQuery = useQuery({
    queryKey: ['self-tests-org', org_id],
    queryFn: async () => (await getOrgSelfTestAttempts(org_id, accessToken)).data,
    enabled: !!org_id && !!accessToken,
  })

  const attempts = attemptsQuery.data || []
  const filtered = attempts.filter((attempt: any) => {
    const q = search.trim().toLowerCase()
    if (!q) return true
    return [attempt.user_name, attempt.user_email, attempt.attempt_uuid].some((value) => String(value || '').toLowerCase().includes(q))
  })

  const reviewMutation = useMutation({
    mutationFn: async () => {
      if (!selected) return null
      const res = await reviewSelfTestAttempt(selected.attempt_uuid, {
        teacher_score: teacherScore === '' ? null : Number(teacherScore),
        teacher_feedback: teacherFeedback,
        counts_for_grade: countsForGrade,
      }, accessToken)
      if (res.success === false) throw new Error(res?.data?.detail || 'Review failed')
      return res.data
    },
    onSuccess: (data) => {
      toast.success('Review saved')
      setSelected(data)
      queryClient.invalidateQueries({ queryKey: ['self-tests-org', org_id] })
    },
    onError: (error: any) => toast.error(error.message || 'Review failed'),
  })

  function selectAttempt(attempt: any) {
    setSelected(attempt)
    setTeacherScore(attempt.teacher_score ?? attempt.score ?? '')
    setTeacherFeedback(attempt.teacher_feedback || '')
    setCountsForGrade(!!attempt.counts_for_grade)
  }

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-6 py-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <Breadcrumbs
          items={[
            { label: 'Self-test records', href: '/dash/self-tests', icon: <ClipboardList size={14} /> },
          ]}
        />

        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-tight text-gray-950">Self-test records</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              Review student self-tests generated from the shared question bank and mark attempts for grading.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[420px_1fr]">
          <section className="space-y-3">
            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search student or attempt"
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
                      <p className="text-sm font-black text-gray-950">{attempt.user_name || attempt.user_email || `User ${attempt.user_id}`}</p>
                      <p className="mt-1 text-xs text-gray-500">{new Date(attempt.creation_date).toLocaleString()}</p>
                    </div>
                    <span className="rounded-full bg-gray-950 px-2 py-1 text-[11px] font-bold text-white">{attempt.percentage}%</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge>{attempt.status}</Badge>
                    <Badge>{attempt.score}/{attempt.max_score}</Badge>
                    {attempt.counts_for_grade && <Badge>Counts for grade</Badge>}
                  </div>
                </button>
              ))}
              {!attemptsQuery.isLoading && filtered.length === 0 && (
                <div className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">No self-test attempts yet.</div>
              )}
            </div>
          </section>

          <main className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            {!selected && (
              <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                <ClipboardPenLine size={42} className="text-gray-300" />
                <p className="mt-3 text-sm font-bold text-gray-800">Select an attempt</p>
                <p className="mt-1 text-xs text-gray-500">Student answers and teacher grading controls appear here.</p>
              </div>
            )}
            {selected && (
              <div className="space-y-5">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <p className="text-xs font-bold uppercase text-gray-400">Attempt</p>
                    <h2 className="mt-1 text-2xl font-black text-gray-950">{selected.user_name || selected.user_email}</h2>
                    <p className="mt-1 text-sm text-gray-500">{selected.attempt_uuid}</p>
                  </div>
                  <div className="text-left md:text-right">
                    <p className="text-xs font-bold uppercase text-gray-400">Auto score</p>
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
                    placeholder="Teacher score"
                  />
                  <input
                    value={teacherFeedback}
                    onChange={(event) => setTeacherFeedback(event.target.value)}
                    className="h-10 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
                    placeholder="Teacher feedback"
                  />
                  <label className="flex h-10 items-center gap-2 rounded-lg border border-gray-200 px-3 text-sm font-semibold text-gray-700">
                    <input type="checkbox" checked={countsForGrade} onChange={(event) => setCountsForGrade(event.target.checked)} />
                    Counts
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => reviewMutation.mutate()}
                  disabled={reviewMutation.isPending || selected.status === 'STARTED'}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:bg-gray-300"
                >
                  <CheckCircle2 size={16} />
                  Save review
                </button>

                <div className="space-y-3">
                  {(selected.questions || []).map((question: any, index: number) => (
                    <article key={question.attempt_question_uuid} className="rounded-lg border border-gray-200 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-black text-gray-950">Q{index + 1}. {question.title}</p>
                          <p className="mt-1 text-sm text-gray-600">{question.contents?.prompt || question.description}</p>
                        </div>
                        <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{question.grade}/{question.max_grade}</span>
                      </div>
                      <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-gray-950 p-3 text-xs text-white">{JSON.stringify(question.answer || {}, null, 2)}</pre>
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
