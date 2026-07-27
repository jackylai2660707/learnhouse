'use client'
import { getAPIUrl } from '@services/config/config'
import { apiFetch } from '@services/utils/ts/requests'
import React, { createContext, useContext, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { useLHSession } from '@components/Contexts/LHSessionContext'

export const AssignmentContext = createContext({})

function AssignmentLoadState({
    title,
    detail,
    tone = 'neutral',
}: {
    title: string
    detail: string
    tone?: 'neutral' | 'error'
}) {
    const toneClass = tone === 'error'
        ? 'border-rose-100 bg-rose-50 text-rose-800'
        : 'border-gray-100 bg-gray-50 text-gray-700'

    return (
        <div className="flex min-h-[220px] w-full items-center justify-center p-6">
            <div className={`max-w-md rounded-xl border px-5 py-4 text-center ${toneClass}`}>
                <p className="text-sm font-black">{title}</p>
                <p className="mt-1 text-xs font-semibold leading-relaxed opacity-80">{detail}</p>
            </div>
        </div>
    )
}

export function AssignmentProvider({ children, assignment_uuid }: { children: React.ReactNode, assignment_uuid: string }) {
    const session = useLHSession() as any
    const accessToken = session?.data?.tokens?.access_token
    const sessionStatus = session?.status

    const { data: assignment, error: assignmentError, isLoading: assignmentLoading } = useQuery({
        queryKey: queryKeys.assignments.detail(assignment_uuid),
        queryFn: () => apiFetch(`${getAPIUrl()}assignments/${assignment_uuid}`, accessToken),
        enabled: !!(assignment_uuid && accessToken),
        staleTime: 60_000,
    })

    const { data: assignment_tasks, error: assignmentTasksError, isLoading: assignmentTasksLoading } = useQuery({
        queryKey: queryKeys.assignments.tasks(assignment_uuid),
        queryFn: () => apiFetch(`${getAPIUrl()}assignments/${assignment_uuid}/tasks`, accessToken),
        enabled: !!(assignment_uuid && accessToken),
        staleTime: 60_000,
    })

    // course_uuid/activity_uuid are now embedded in the assignment payload
    // (joined server-side) so we don't need separate /courses/id and
    // /activities/id round trips. We synthesize tiny shim objects to keep
    // existing consumers (which read .course_uuid / .activity_uuid) working
    // without changes. useMemo (vs useState+useEffect) means the provider
    // value is correct on the same render the SWR data lands, with no
    // wasted null-context render cycle.
    const assignmentsFull = useMemo(() => {
        if (!assignment || !assignment_tasks) return null
        return {
            assignment_object: assignment,
            assignment_tasks: assignment_tasks,
            course_object: assignment.course_uuid
                ? { course_uuid: assignment.course_uuid }
                : null,
            activity_object: assignment.activity_uuid
                ? { activity_uuid: assignment.activity_uuid }
                : null,
        }
    }, [assignment, assignment_tasks])

    if (!assignment_uuid) {
        return (
            <AssignmentLoadState
                tone="error"
                title="找不到作業"
                detail="這個作業連結不完整，請返回課程或請老師重新提供連結。"
            />
        )
    }

    if (assignmentError || assignmentTasksError) {
        return (
            <AssignmentLoadState
                tone="error"
                title="讀取作業失敗"
                detail="請重新整理頁面；如果仍然看不到，請聯絡老師確認作業是否已發布。"
            />
        )
    }

    if (!accessToken && sessionStatus !== 'loading') {
        return (
            <AssignmentLoadState
                tone="error"
                title="請先登入"
                detail="登入後才可以查看作業、作答和提交。"
            />
        )
    }

    if (!accessToken || assignmentLoading || assignmentTasksLoading || !assignmentsFull) {
        return (
            <AssignmentLoadState
                title="正在載入作業"
                detail="系統正在讀取題目和提交狀態，請稍候。"
            />
        )
    }

    return <AssignmentContext.Provider value={assignmentsFull}>{children}</AssignmentContext.Provider>
}

export function useAssignments() {
    return useContext(AssignmentContext)
}
