'use client'
import React from 'react'
import { useLHSession } from '../LHSessionContext'
import { getAPIUrl } from '@services/config/config'
import { apiFetch } from '@services/utils/ts/requests'
import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { Loader2, RotateCcw } from 'lucide-react'

export const AssignmentSubmissionContext = React.createContext({})
export const AssignmentTaskSubmissionsContext = React.createContext<Record<string, any> | null>(null)

function AssignmentSubmissionLoadState({
    title,
    detail,
    tone = 'neutral',
    onRetry,
    isRetrying = false,
}: {
    title: string
    detail: string
    tone?: 'neutral' | 'error'
    onRetry?: () => void
    isRetrying?: boolean
}) {
    const toneClass = tone === 'error'
        ? 'border-rose-100 bg-rose-50 text-rose-800'
        : 'border-gray-100 bg-gray-50 text-gray-700'

    return (
        <div className="flex min-h-[180px] w-full items-center justify-center p-6">
            <div className={`max-w-md rounded-xl border px-5 py-4 text-center ${toneClass}`}>
                <p className="text-sm font-black">{title}</p>
                <p className="mt-1 text-xs font-semibold leading-relaxed opacity-80">{detail}</p>
                {onRetry && (
                    <button
                        type="button"
                        onClick={onRetry}
                        disabled={isRetrying}
                        className="mt-3 inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-white px-3 text-xs font-black text-gray-800 ring-1 ring-inset ring-gray-200 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                        {isRetrying ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                        {isRetrying ? '載入中' : '重新載入'}
                    </button>
                )}
            </div>
        </div>
    )
}

function AssignmentSubmissionProvider({ children, assignment_uuid }: { children: React.ReactNode, assignment_uuid: string }) {
    const session = useLHSession() as any
    const accessToken = session?.data?.tokens?.access_token
    const sessionStatus = session?.status

    const {
        data: assignmentSubmission,
        error: assignmentSubmissionError,
        isFetching: assignmentSubmissionFetching,
        isLoading: assignmentSubmissionLoading,
        refetch: refetchAssignmentSubmission,
    } = useQuery({
        queryKey: queryKeys.assignments.submission(assignment_uuid),
        queryFn: () => apiFetch(`${getAPIUrl()}assignments/${assignment_uuid}/submissions/me`, accessToken),
        enabled: !!(assignment_uuid && accessToken),
        staleTime: 60_000,
    })

    // Single batch fetch of every per-task submission for this user. Replaces
    // N per-task /submissions/me calls that each Task*Object used to make.
    const {
        data: taskSubmissionsMap,
        error: taskSubmissionsError,
        isFetching: taskSubmissionsFetching,
        isLoading: taskSubmissionsLoading,
        refetch: refetchTaskSubmissions,
    } = useQuery({
        queryKey: queryKeys.assignments.taskSubmission(assignment_uuid),
        queryFn: () => apiFetch(`${getAPIUrl()}assignments/${assignment_uuid}/tasks/submissions/me`, accessToken),
        enabled: !!(assignment_uuid && accessToken),
        staleTime: 60_000,
    })

    const isRetryingSubmissionState = assignmentSubmissionFetching || taskSubmissionsFetching
    const refetchSubmissionState = React.useCallback(() => {
        refetchAssignmentSubmission()
        refetchTaskSubmissions()
    }, [refetchAssignmentSubmission, refetchTaskSubmissions])

    if (!assignment_uuid) {
        return (
            <AssignmentSubmissionLoadState
                tone="error"
                title="找不到作業提交資料"
                detail="這個作業連結不完整，請返回課程或請老師重新提供連結。"
            />
        )
    }

    if (!accessToken && sessionStatus !== 'loading') {
        return (
            <AssignmentSubmissionLoadState
                tone="error"
                title="請先登入"
                detail="登入後才可以保存答案、提交作業和查看批改結果。"
            />
        )
    }

    if (assignmentSubmissionError || taskSubmissionsError) {
        return (
            <AssignmentSubmissionLoadState
                tone="error"
                title="讀取提交狀態失敗"
                detail="請先按重新載入；如果仍然看不到，請聯絡老師確認作業是否已發布。"
                onRetry={refetchSubmissionState}
                isRetrying={isRetryingSubmissionState}
            />
        )
    }

    if (!accessToken || assignmentSubmissionLoading || taskSubmissionsLoading) {
        return (
            <AssignmentSubmissionLoadState
                title="正在載入提交狀態"
                detail="系統正在讀取已保存答案和批改結果，請稍候。"
            />
        )
    }

    return (
        <AssignmentSubmissionContext.Provider value={assignmentSubmission}>
            <AssignmentTaskSubmissionsContext.Provider value={taskSubmissionsMap ?? null}>
                {children}
            </AssignmentTaskSubmissionsContext.Provider>
        </AssignmentSubmissionContext.Provider>
    )
}

export function useAssignmentSubmission() {
    return React.useContext(AssignmentSubmissionContext)
}

export function useAssignmentTaskSubmissions() {
    return React.useContext(AssignmentTaskSubmissionsContext)
}

export default AssignmentSubmissionProvider
