'use client';
import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import { useAssignmentsTask, useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { deleteAssignmentTask } from '@services/courses/assignments';
import { saveAssignmentTaskToQuestionBank } from '@services/question-bank/question-bank';
import { AlertCircle, ArrowLeft, BookOpenCheck, GalleryVerticalEnd, Info, RotateCcw, Trash } from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import dynamic from 'next/dynamic';
import { AssignmentTaskGeneralEdit } from './Subs/AssignmentTaskGeneralEdit';
import {
    SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET,
    SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS,
    countAiFallbackStarterTasks,
    countNonSimplePilotAssignmentTasks,
    isAiFallbackStarterTask,
} from '@lib/simple-pilot-assignments';
const AssignmentTaskContentEdit = dynamic(() => import('./Subs/AssignmentTaskContentEdit'))

type TaskEditorSubPage = 'general' | 'content'

function AssignmentTaskEditor({ page }: any) {
    const assignment = useAssignments() as any
    const assignmentTaskState = useAssignmentsTask() as any
    const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
    const selectedTaskIdentity = assignmentTaskState?.selectedAssignmentTaskUUID ?? null
    const [tabState, setTabState] = React.useState<{
        taskIdentity: string | null
        subPage: TaskEditorSubPage
    }>({
        taskIdentity: selectedTaskIdentity,
        subPage: page === 'content' ? 'content' : 'general',
    })
    const selectedSubPage: TaskEditorSubPage = tabState.taskIdentity === selectedTaskIdentity
        ? tabState.subPage
        : 'general'
    const setSelectedSubPage = React.useCallback((subPage: TaskEditorSubPage) => {
        setTabState({ taskIdentity: selectedTaskIdentity, subPage })
    }, [selectedTaskIdentity])
    const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([])
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;
    const queryClient = useQueryClient();
    const selectedAssignmentTask = assignmentTaskState?.assignmentTask ?? {}
    const hasLoadedAssignmentTask = Object.keys(selectedAssignmentTask).length > 0
    const hasSelectedAssignmentTask = Boolean(assignmentTaskState?.selectedAssignmentTaskUUID)
    const isLoadingSelectedAssignmentTask = hasSelectedAssignmentTask && Boolean(assignmentTaskState?.isLoadingAssignmentTask)
    const assignmentTaskError = assignmentTaskState?.assignmentTaskError
    const canSaveToQuestionBank = SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET.has(
        selectedAssignmentTask?.assignment_type
    )
    const assignmentTasks = Array.isArray(assignment?.assignment_tasks) ? assignment.assignment_tasks : []
    const hasNoTasks = assignmentTasks.length === 0
    const isPublishedAssignment = Boolean(assignment?.assignment_object?.published)
    const aiFallbackTaskCount = countAiFallbackStarterTasks(assignmentTasks)
    const nonSimpleTaskCount = countNonSimplePilotAssignmentTasks(assignmentTasks)
    const isOverSimplePilotLimit = assignmentTasks.length > SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS
    const selectedTaskNeedsTeacherCheck = isAiFallbackStarterTask(selectedAssignmentTask)
    const shouldShowReadyToPublishHint = !isPublishedAssignment && !hasNoTasks
    const readyToPublishTone =
        aiFallbackTaskCount > 0 || nonSimpleTaskCount > 0 || isOverSimplePilotLimit ? 'amber' : 'emerald'
    const readyToPublishTitle =
        aiFallbackTaskCount > 0
            ? '先檢查 AI 備用題'
            : nonSimpleTaskCount > 0
            ? '先保持簡單題型'
            : isOverSimplePilotLimit
                ? '建議拆成多份簡單作業'
                : '簡單題已準備好'
    const retryScoreNote =
        assignment?.assignment_object?.score_policy === 'latest'
            ? '重新提交後會再計分。'
            : '最高分會保留。'
    const readyToPublishDescription =
        aiFallbackTaskCount > 0
            ? `這份作業有 ${aiFallbackTaskCount} 題 AI 備用題。請選擇左側標有「需檢查」的題目，改成正式課堂題目和答案；確認後刪除標題或提示中的「AI 備用」和「請老師檢查後再發布」字眼。`
            : nonSimpleTaskCount > 0
            ? `這份作業有 ${nonSimpleTaskCount} 題進階題型。校內試行建議只用選擇、填空、短問答，老師和學生最容易上手，也方便自動批改。`
            : isOverSimplePilotLimit
                ? `這份作業已有 ${assignmentTasks.length} 題。建議每份最多 ${SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題，拆成幾份小作業，學生比較容易完成。`
                : `下一步：右上角按「發布給學生」。發布時會檢查班級、自動批改、可重做和參考答案；學生答錯可按「再做一次」重新提交，${retryScoreNote}`
    const readyToPublishClass =
        readyToPublishTone === 'amber'
            ? 'border-amber-100 bg-amber-50 text-amber-900'
            : 'border-emerald-100 bg-emerald-50 text-emerald-900'
    const readyToPublishIconClass =
        readyToPublishTone === 'amber'
            ? 'text-amber-700'
            : 'text-emerald-700'

    async function deleteTaskUI() {
        const res = await deleteAssignmentTask(assignmentTaskState.assignmentTask.assignment_task_uuid, assignment.assignment_object.assignment_uuid, access_token)
        if (res && res.success !== false) {
            assignmentTaskStateHook({
                type: 'SET_MULTIPLE_STATES',
                payload: {
                    selectedAssignmentTaskUUID: null,
                    assignmentTask: {},
                },
            });
            queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignment.assignment_object.assignment_uuid) })
            queryClient.invalidateQueries({ queryKey: queryKeys.assignments.detail(assignment.assignment_object.assignment_uuid) })
            queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
            toast.success('題目已刪除')
        } else {
            toast.error('刪除題目失敗，請稍後再試。')
        }
    }

    async function saveToQuestionBankUI() {
        const taskUuid = assignmentTaskState?.assignmentTask?.assignment_task_uuid
        if (!taskUuid) return
        const res = await saveAssignmentTaskToQuestionBank({
            assignment_task_uuid: taskUuid,
            tags: [],
            difficulty: 'beginner',
            visibility: 'ORG',
        }, access_token)
        if (res.success === false) {
            toast.error(res?.data?.detail || '無法存入題庫')
            return
        }
        toast.success('已存入題庫')
    }

    function selectEditorTab(index: number) {
        const nextTab: TaskEditorSubPage = index === 1 ? 'content' : 'general'
        setSelectedSubPage(nextTab)
        tabRefs.current[index]?.focus()
    }

    function handleEditorTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
        let nextIndex: number | null = null
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
            nextIndex = currentIndex === 0 ? 1 : 0
        } else if (event.key === 'Home') {
            nextIndex = 0
        } else if (event.key === 'End') {
            nextIndex = 1
        }
        if (nextIndex === null) return
        event.preventDefault()
        selectEditorTab(nextIndex)
    }

    return (
        <div className="flex h-full min-h-0 w-full min-w-0 flex-col text-sm font-black z-20">
            {hasLoadedAssignmentTask && (
                <>
                    {/* Task header + tabs: flex-none so it stays fixed at the
                        top of the editor panel. No sticky/overflow here — the
                        surrounding page's tabs bar shadow renders cleanly
                        above it. */}
                    <div className='relative z-10 mb-3 flex flex-none flex-col bg-white px-4 pt-5 text-sm tracking-tight nice-shadow sm:px-6 md:px-10'>
                        <div className='flex min-w-0 flex-col gap-3 py-1 sm:flex-row sm:items-center sm:justify-between'>
                            <div className='min-w-0 break-words text-lg font-semibold'>
                                {assignmentTaskState?.assignmentTask.title}
                                {selectedTaskNeedsTeacherCheck && (
                                    <span className='ml-2 inline-flex align-middle rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-black text-rose-700'>
                                        需檢查
                                    </span>
                                )}
                            </div>
                            <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto sm:justify-end">
                                {canSaveToQuestionBank && !selectedTaskNeedsTeacherCheck && (
                                    <button
                                        type="button"
                                        onClick={saveToQuestionBankUI}
                                        className='flex min-w-0 flex-1 cursor-pointer items-center justify-center space-x-2 rounded-md bg-gray-900 px-2 py-1.5 text-white shadow-lg sm:flex-none'>
                                        <BookOpenCheck size={18} />
                                        <p className='text-xs font-semibold'>存入題庫</p>
                                    </button>
                                )}
                                {canSaveToQuestionBank && selectedTaskNeedsTeacherCheck && (
                                    <div className='flex min-w-0 flex-1 items-center rounded-md bg-rose-50 px-2 py-1.5 text-rose-700 ring-1 ring-rose-100 sm:flex-none'>
                                        <p className='break-words text-xs font-semibold'>先改成正式題目再存題庫</p>
                                    </div>
                                )}
                                <button
                                    type="button"
                                    onClick={() => deleteTaskUI()}
                                    className='flex min-w-0 flex-1 cursor-pointer items-center justify-center space-x-2 rounded-md border border-rose-600/10 bg-rose-100 bg-linear-to-bl px-2 py-1.5 text-red-800 shadow-lg shadow-rose-900/10 sm:flex-none'>
                                    <Trash size={18} />
                                    <p className='text-xs font-semibold'>刪除題目</p>
                                </button>
                            </div>
                        </div>
                        <div
                            role="tablist"
                            aria-label="題目編輯區"
                            className='flex min-w-0 gap-1 overflow-x-auto'
                        >
                            <button
                                ref={(element) => { tabRefs.current[0] = element }}
                                id="assignment-task-general-tab"
                                type="button"
                                role="tab"
                                aria-selected={selectedSubPage === 'general'}
                                aria-controls="assignment-task-general-panel"
                                tabIndex={selectedSubPage === 'general' ? 0 : -1}
                                onClick={() => setSelectedSubPage('general')}
                                onKeyDown={(event) => handleEditorTabKeyDown(event, 0)}
                                className={`flex w-fit shrink-0 cursor-pointer items-center gap-2 border-b-4 px-2 py-2 text-center transition-all ease-linear focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600 ${selectedSubPage === 'general'
                                    ? 'border-black text-gray-950'
                                    : 'border-transparent text-gray-500 hover:text-gray-800'
                                    }`}
                            >
                                <Info size={16} aria-hidden="true" />
                                <span>基本資料</span>
                            </button>
                            <button
                                ref={(element) => { tabRefs.current[1] = element }}
                                id="assignment-task-content-tab"
                                type="button"
                                role="tab"
                                aria-selected={selectedSubPage === 'content'}
                                aria-controls="assignment-task-content-panel"
                                tabIndex={selectedSubPage === 'content' ? 0 : -1}
                                onClick={() => setSelectedSubPage('content')}
                                onKeyDown={(event) => handleEditorTabKeyDown(event, 1)}
                                className={`flex w-fit shrink-0 cursor-pointer items-center gap-2 border-b-4 px-2 py-2 text-center transition-all ease-linear focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600 ${selectedSubPage === 'content'
                                    ? 'border-black text-gray-950'
                                    : 'border-transparent text-gray-500 hover:text-gray-800'
                                    }`}
                            >
                                <GalleryVerticalEnd size={16} aria-hidden="true" />
                                <span>題目內容</span>
                            </button>
                        </div>
                    </div>
                    {/* Scrollable body — only this area scrolls. flex-1
                        claims the remaining height; min-h-0 allows the flex
                        child to shrink below its content size so the
                        overflow kicks in correctly. */}
                    <div
                        id={`assignment-task-${selectedSubPage}-panel`}
                        role="tabpanel"
                        aria-labelledby={`assignment-task-${selectedSubPage}-tab`}
                        tabIndex={0}
                        className='min-h-0 min-w-0 flex-1 overflow-y-auto pb-10 focus-visible:outline-none'
                    >
                        {shouldShowReadyToPublishHint && (
                            <div className={`mx-4 mt-5 rounded-lg border px-4 py-3 sm:mx-6 md:mx-10 ${readyToPublishClass}`}>
                                <div className='flex items-start gap-3'>
                                    <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white ${readyToPublishIconClass}`}>
                                        {readyToPublishTone === 'amber' ? <AlertCircle size={17} /> : <BookOpenCheck size={17} />}
                                    </div>
                                    <div>
                                        <p className='text-sm font-black'>{readyToPublishTitle}</p>
                                        <p className='mt-1 text-xs font-semibold leading-relaxed opacity-80'>
                                            {readyToPublishDescription}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        )}
                        <div className={`mx-4 min-w-0 ${shouldShowReadyToPublishHint ? 'mt-4' : 'mt-10'} rounded-xl bg-white px-4 py-5 shadow-xs nice-shadow sm:mx-6 sm:px-6 md:mx-10`}>
                            {selectedSubPage === 'general' && <AssignmentTaskGeneralEdit />}
                            {selectedSubPage === 'content' && <AssignmentTaskContentEdit />}
                        </div>
                    </div>
                </>
            )}
            {!hasLoadedAssignmentTask && hasSelectedAssignmentTask && (
                <div className='relative z-10 flex h-full min-w-0 flex-col bg-white px-4 pt-5 text-sm tracking-tight nice-shadow sm:px-6 md:px-10'>
                    <div className='flex h-full items-center justify-center antialiased'>
                        <div className={`w-full min-w-0 max-w-lg rounded-2xl border px-4 py-6 text-center sm:px-6 ${assignmentTaskError
                            ? 'border-rose-100 bg-rose-50/80'
                            : 'border-gray-100 bg-gray-50/80'
                            }`}>
                            <div className={`mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-white nice-shadow ${assignmentTaskError
                                ? 'text-rose-700'
                                : 'text-gray-700'
                                }`}>
                                <AlertCircle size={22} />
                            </div>
                            <div className='mt-4 font-black text-2xl text-gray-950'>
                                {isLoadingSelectedAssignmentTask
                                    ? '正在讀取題目'
                                    : assignmentTaskError
                                        ? '讀取題目失敗'
                                        : '找不到題目'}
                            </div>
                            <p className='mt-2 text-sm font-semibold leading-relaxed text-gray-600'>
                                {isLoadingSelectedAssignmentTask
                                    ? '系統正在載入題目內容和答案設定，請稍候。'
                                    : assignmentTaskError || '這題可能已被刪除或沒有權限查看。請在左側重新選擇題目。'}
                            </p>
                            {assignmentTaskError && !isLoadingSelectedAssignmentTask && (
                                <button
                                    type="button"
                                    onClick={() => assignmentTaskStateHook({ type: 'reload' })}
                                    className="mt-4 inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-white px-3 text-xs font-black text-rose-800 ring-1 ring-inset ring-rose-200 hover:bg-rose-100"
                                >
                                    <RotateCcw size={14} />
                                    重新讀取題目
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}
            {!hasLoadedAssignmentTask && !hasSelectedAssignmentTask && (
                <div className='relative z-10 flex h-full min-w-0 flex-col bg-white px-4 pt-5 text-sm tracking-tight nice-shadow sm:px-6 md:px-10'>
                    <div className='flex h-full items-center justify-center antialiased'>
                        <div className='w-full min-w-0 max-w-lg rounded-2xl border border-dashed border-cyan-200 bg-cyan-50/60 px-4 py-6 text-center sm:px-6'>
                            <div className='mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-white text-cyan-700 nice-shadow'>
                                {isPublishedAssignment && hasNoTasks ? <AlertCircle size={22} /> : <BookOpenCheck size={22} />}
                            </div>
                            <div className='mt-4 font-black text-2xl text-gray-950'>
                                {hasNoTasks
                                    ? isPublishedAssignment
                                        ? '已發布但暫時沒有題目'
                                        : '下一步：建立 3 題簡單題'
                                    : '請在左側選擇題目'}
                            </div>
                            <p className='mt-2 text-sm font-semibold leading-relaxed text-gray-600'>
                                {hasNoTasks
                                    ? isPublishedAssignment
                                        ? '請先取消發布，再新增選擇題、填空題或短問答。避免學生看到空白作業。'
                                        : '請在左側新增簡單題；AI、題庫或手動都可以。學生提交後可自動批改，答錯可以重做。'
                                    : '選擇一題後，可以修改題目內容、答案和提示。'}
                            </p>
                            {!isPublishedAssignment && hasNoTasks && (
                                <div className='mt-4 inline-flex items-center gap-2 rounded-lg bg-white px-3 py-2 text-xs font-black text-cyan-800 ring-1 ring-cyan-100'>
                                    <ArrowLeft size={14} />
                                    <BookOpenCheck size={14} />
                                    左側按新增簡單題
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

        </div>
    )
}



export default AssignmentTaskEditor
