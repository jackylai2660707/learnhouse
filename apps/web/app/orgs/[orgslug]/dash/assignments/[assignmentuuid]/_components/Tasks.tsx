import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext'
import Modal from '@components/Objects/StyledElements/Modal/Modal';
import { Clock, Code2, FileUp, Hash, ListTodo, Pencil, Plus, Type } from 'lucide-react';
import React from 'react'
import NewTaskModal from './Modals/NewTaskModal';
import { useAssignmentsTask, useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS, isAiFallbackStarterTask } from '@lib/simple-pilot-assignments';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

dayjs.extend(relativeTime)

const TASK_TYPE_META: Record<string, { label: string; Icon: React.ComponentType<{ size?: number; className?: string }> }> = {
    QUIZ: { label: '選擇題', Icon: ListTodo },
    FORM: { label: '填空題', Icon: Type },
    SHORT_ANSWER: { label: '短問答', Icon: Pencil },
    FILE_SUBMISSION: { label: '檔案提交', Icon: FileUp },
    CODE: { label: '程式題', Icon: Code2 },
    NUMBER_ANSWER: { label: '數字題', Icon: Hash },
}
function stripMarkup(text: string): string {
    if (!text) return ''
    return text
        .replace(/<[^>]+>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
}

function AssignmentTasks({ assignment_uuid }: any) {
    const { t } = useTranslation()
    const assignments = useAssignments() as any;
    const assignmentTask = useAssignmentsTask() as any;
    const assignmentTaskHook = useAssignmentsTaskDispatch() as any;
    const [isNewTaskModalOpen, setIsNewTaskModalOpen] = React.useState(false)
    const searchParams = useSearchParams()
    const pathname = usePathname()
    const router = useRouter()
    const autoOpenedNewTaskRef = React.useRef(false)

    async function setSelectTask(task_uuid: string) {
        assignmentTaskHook({ type: 'setSelectedAssignmentTaskUUID', payload: task_uuid })
    }

    const tasks: any[] = assignments?.assignment_tasks ?? []

    React.useEffect(() => {
        if (autoOpenedNewTaskRef.current) return
        if (searchParams.get('newTask') !== '1') return
        if (!assignments || tasks.length >= SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS) return
        autoOpenedNewTaskRef.current = true
        setIsNewTaskModalOpen(true)
        router.replace(pathname, { scroll: false })
    }, [assignments, pathname, router, searchParams, tasks.length])

    return (
        <div className='flex w-full'>
            <div className='flex flex-col gap-2 w-[272px] mx-auto'>
                {assignments && tasks.length < SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS && (
                    <Modal
                        isDialogOpen={isNewTaskModalOpen}
                        onOpenChange={setIsNewTaskModalOpen}
                        minHeight='no-min'
                        minWidth='lg'
                        dialogContent={
                            <NewTaskModal assignment_uuid={assignment_uuid} closeModal={setIsNewTaskModalOpen} />
                        }
                        dialogTitle={t('dashboard.assignments.editor.add_task_modal.title')}
                        dialogDescription={t('dashboard.assignments.editor.add_task_modal.description')}
                        dialogTrigger={
                            <button
                                type='button'
                                className='group flex items-center justify-center gap-1.5 w-full px-3 py-2.5 bg-gray-900 text-white text-xs font-semibold rounded-lg hover:bg-black transition-colors'
                            >
                                <Plus size={14} className='transition-transform group-hover:scale-110' />
                                <span>{t('dashboard.assignments.editor.add_task')}</span>
                            </button>
                        }
                    />
                )}

                {assignments && tasks.length >= SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS && (
                    <div className='rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800'>
                        已有 {tasks.length} 題。校內試行建議每份作業最多 {SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題，請建立另一份簡單作業讓學生分次完成。
                    </div>
                )}

                {tasks.length > 0 && (
                    <div className='px-1 pt-1 pb-0.5 flex items-center justify-between'>
                        <span className='text-[10px] font-semibold text-gray-400 uppercase tracking-wider'>
                            共 {tasks.length} 題
                        </span>
                        <span className='text-[10px] font-medium text-gray-300'>最新在前</span>
                    </div>
                )}

                {tasks.map((task: any, index: number) => {
                    const meta = TASK_TYPE_META[task.assignment_type] ?? { label: task.assignment_type, Icon: Type }
                    const Icon = meta.Icon
                    const isSelected = task.assignment_task_uuid === assignmentTask.selectedAssignmentTaskUUID
                    const descriptionPreview = stripMarkup(task.description || '')
                    const createdAt = task.creation_date ? dayjs(task.creation_date) : null
                    const createdLabel = createdAt?.isValid() ? createdAt.fromNow() : null
                    const position = tasks.length - index
                    const needsTeacherCheck = isAiFallbackStarterTask(task)

                    return (
                        <button
                            type='button'
                            key={task.id}
                            onClick={() => setSelectTask(task.assignment_task_uuid)}
                            className={`group relative text-left rounded-xl border transition-all overflow-hidden
                                ${isSelected
                                    ? 'border-gray-900 bg-white shadow-[0_4px_14px_rgba(15,23,42,0.08)]'
                                    : 'border-gray-100 bg-white hover:border-gray-200 hover:shadow-[0_2px_8px_rgba(15,23,42,0.05)]'}
                            `}
                        >
                            {/* Accent bar when selected */}
                            <span
                                className={`absolute left-0 top-0 bottom-0 w-[3px] transition-colors
                                    ${isSelected ? 'bg-gray-900' : 'bg-transparent'}
                                `}
                                aria-hidden
                            />

                            <div className='p-3 pl-[14px]'>
                                {/* Meta row */}
                                <div className='flex items-center justify-between mb-1.5'>
                                    <div className='flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400'>
                                        <span className='inline-flex items-center justify-center w-4 h-4 rounded-full bg-gray-100 text-gray-500 font-bold'>
                                            {position}
                                        </span>
                                        <span>{meta.label}</span>
                                        {needsTeacherCheck && (
                                            <span className='rounded-full bg-rose-50 px-1.5 py-0.5 text-[9px] font-black text-rose-700'>
                                                需檢查
                                            </span>
                                        )}
                                    </div>
                                    <Icon size={13} className='text-gray-300' />
                                </div>

                                {/* Title */}
                                <div className='text-sm font-semibold text-gray-800 leading-snug break-words'>
                                    {task.title || <span className='text-gray-300 italic font-normal'>未命名題目</span>}
                                </div>

                                {/* Description */}
                                {descriptionPreview && (
                                    <p className='mt-1.5 text-xs text-gray-500 leading-relaxed line-clamp-2'>
                                        {descriptionPreview}
                                    </p>
                                )}

                                {/* Footer */}
                                <div className='mt-2.5 pt-2 border-t border-gray-50 flex items-center justify-between text-[11px] text-gray-400'>
                                    <div className='flex items-center gap-1'>
                                        <Clock size={10} />
                                        <span>{createdLabel ?? '—'}</span>
                                    </div>
                                    {typeof task.max_grade_value === 'number' && task.max_grade_value !== 100 && (
                                        <span className='font-medium text-gray-500'>滿分 {task.max_grade_value}</span>
                                    )}
                                </div>
                            </div>
                        </button>
                    )
                })}

                {tasks.length === 0 && assignments && (
                    <div className='rounded-xl border border-dashed border-cyan-200 bg-cyan-50/70 px-4 py-5 text-center'>
                        <div className='mx-auto flex h-10 w-10 items-center justify-center rounded-lg bg-white text-cyan-700 nice-shadow'>
                            <ListTodo size={18} />
                        </div>
                        <p className='mt-3 text-sm font-black text-gray-950'>先做 3 題簡單作業</p>
                        <p className='mt-1 text-xs leading-relaxed text-gray-500'>
                            可用 AI、題庫或手動建立選擇、填空、短問答；學生提交後可自動批改，答錯可以重做。
                        </p>
                        <button
                            type='button'
                            onClick={() => setIsNewTaskModalOpen(true)}
                            className='mt-4 inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-cyan-700 px-3 text-xs font-black text-white hover:bg-cyan-800'
                        >
                            <Plus size={14} />
                            新增簡單題
                        </button>
                    </div>
                )}
            </div>
        </div>
    )
}

export default AssignmentTasks
