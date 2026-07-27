'use client'

import React, { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Users } from 'lucide-react'
import { getAPIUrl } from '@services/config/config'

interface ChallengeAnalyticsData {
  eligible_students: number
  passed_students: number
  pass_rate: number
  average_attempts: number
  not_passed_students: Array<{
    user_id: number
    user_uuid: string
    display_name: string
    attempt_count: number
  }>
  common_failing_tests: Array<{
    test_uuid: string
    label: string
    failure_count: number
  }>
}

interface Props {
  challengeUuid: string
  accessToken: string
  showPendingState?: boolean
}

export default function ChallengeAnalytics({ challengeUuid, accessToken, showPendingState = false }: Props) {
  const [data, setData] = useState<ChallengeAnalyticsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    if (!challengeUuid || !accessToken) return
    setLoading(true)
    setError(false)
    try {
      const response = await fetch(
        `${getAPIUrl()}coding-challenges/${encodeURIComponent(challengeUuid)}/analytics`,
        { headers: { Authorization: `Bearer ${accessToken}` } }
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      setData(await response.json())
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [accessToken, challengeUuid])

  useEffect(() => {
    load()
  }, [load])

  // Learners and teachers share the course view. Probe the teacher-only
  // endpoint silently for learners; editors still see loading and errors.
  if (!data && !showPendingState) return null

  return (
    <section className="rounded-xl border border-neutral-200 bg-neutral-50/70 p-3.5 nice-shadow">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Users size={14} className="text-neutral-500" />
          <h3 className="text-[12px] font-semibold text-neutral-700">學生學習概況</h3>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1 text-[10px] font-medium text-neutral-400 hover:text-neutral-600 disabled:opacity-50"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          更新
        </button>
      </div>

      {loading && !data ? (
        <div className="flex justify-center py-5">
          <Loader2 size={16} className="animate-spin text-neutral-400" />
        </div>
      ) : error && !data ? (
        <p className="py-4 text-center text-[11px] text-red-500">暫時未能載入統計，請稍後重試。</p>
      ) : data?.eligible_students === 0 ? (
        <p className="py-4 text-center text-[11px] text-neutral-500">
          此課程尚未分配班級或學生。
        </p>
      ) : data ? (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <Metric label="通過率" value={`${data.pass_rate}%`} />
            <Metric label="已通過" value={`${data.passed_students}/${data.eligible_students}`} />
            <Metric label="平均提交" value={`${data.average_attempts} 次`} />
          </div>

          <div>
            <p className="mb-1.5 text-[10px] font-semibold text-neutral-500">尚未通過</p>
            {data.not_passed_students.length === 0 ? (
              <p className="text-[11px] text-emerald-600">全班已通過。</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {data.not_passed_students.map((student) => (
                  <span
                    key={student.user_uuid}
                    className="rounded-md border border-amber-100 bg-amber-50 px-2 py-1 text-[10px] text-amber-700"
                  >
                    {student.display_name} · {student.attempt_count} 次
                  </span>
                ))}
              </div>
            )}
          </div>

          {data.common_failing_tests.length > 0 && (
            <div>
              <p className="mb-1.5 text-[10px] font-semibold text-neutral-500">常見未通過測試</p>
              <div className="space-y-1">
                {data.common_failing_tests.map((test) => (
                  <div
                    key={test.test_uuid}
                    className="flex items-center justify-between rounded-md bg-white px-2 py-1.5 text-[10px]"
                  >
                    <span className="truncate text-neutral-600">{test.label}</span>
                    <span className="ml-2 shrink-0 font-semibold text-red-500">
                      {test.failure_count} 次
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-neutral-100 bg-white px-2 py-2 text-center">
      <div className="text-[13px] font-semibold text-neutral-700">{value}</div>
      <div className="mt-0.5 text-[9px] text-neutral-400">{label}</div>
    </div>
  )
}
