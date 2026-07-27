import { useAssignmentSubmission } from '@components/Contexts/Assignments/AssignmentSubmissionContext'
import { BookPlus, BookUser, Code2, EllipsisVertical, FilePenLine, FileUp, Forward, Hash, InfoIcon, ListTodo, Loader2, Pencil, Save, Type } from 'lucide-react'
import React from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useTranslation } from 'react-i18next'

type AssignmentBoxProps = {
    type: 'quiz' | 'file' | 'form' | 'code' | 'short-answer' | 'number-answer' | 'essay'
    view?: 'teacher' | 'student' | 'grading' | 'custom-grading'
    maxPoints?: number
    currentPoints?: number
    saveFC?: () => void | Promise<void>
    submitFC?: () => void | Promise<void>
    gradeFC?: () => void | Promise<void>
    // eslint-disable-next-line no-unused-vars
    gradeCustomFC?: (grade: number) => void | Promise<void>
    showSavingDisclaimer?: boolean
    autoGradable?: boolean
    studentActionDisabled?: boolean
    studentActionDisabledDescriptionId?: string
    children: React.ReactNode
}

function AssignmentBoxUI({ type, view, currentPoints, maxPoints, saveFC, submitFC, gradeFC, gradeCustomFC, showSavingDisclaimer, autoGradable, studentActionDisabled = false, studentActionDisabledDescriptionId, children }: AssignmentBoxProps) {
    const { t } = useTranslation()
    const [customGrade, setCustomGrade] = React.useState<number>(0)
    const [pendingAction, setPendingAction] = React.useState<string | null>(null)
    const actionInFlightRef = React.useRef(false)
    const submission = useAssignmentSubmission() as any
    const session = useLHSession() as any

    // Check if user is authenticated
    const isAuthenticated = session?.status === 'authenticated'
    const submissionStatus = Array.isArray(submission) && submission.length > 0
        ? submission[0]?.submission_status
        : null
    const canSaveStudentProgress =
        view === 'student' &&
        isAuthenticated &&
        Array.isArray(submission) &&
        (
            submission.length === 0 ||
            submissionStatus === 'PENDING' ||
            submissionStatus === 'NOT_SUBMITTED'
        )
    const actionIsDisabled = pendingAction !== null
    const isActionPending = (action: string) => pendingAction === action
    const actionClassName = (baseClassName: string) =>
        `${baseClassName} ${actionIsDisabled ? 'pointer-events-none opacity-60' : ''}`

    async function runAction(action: string, callback?: () => void | Promise<void>) {
        if (!callback || actionIsDisabled || actionInFlightRef.current) return
        actionInFlightRef.current = true
        setPendingAction(action)
        try {
            await callback()
        } finally {
            actionInFlightRef.current = false
            setPendingAction(null)
        }
    }

    return (
        <div className='flex flex-col px-3 sm:px-6 py-4 nice-shadow rounded-md bg-slate-100/30'>
            <div className='flex flex-col sm:flex-row sm:justify-between sm:space-x-2 pb-2 text-slate-400 sm:items-center'>
                {/* Left side with type and badges */}
                <div className='flex flex-wrap gap-2 items-center mb-2 sm:mb-0'>
                    <div className='text-lg font-semibold'>
                        {type === 'quiz' &&
                            <div className='flex space-x-1.5 items-center'>
                                <ListTodo size={17} />
                                <p>{t('activities.quiz')}</p>
                            </div>}
                        {type === 'file' &&
                            <div className='flex space-x-1.5 items-center'>
                                <FileUp size={17} />
                                <p>{t('activities.file_submission')}</p>
                            </div>}
                        {type === 'form' &&
                            <div className='flex space-x-1.5 items-center'>
                                <Type size={17} />
                                <p>{t('activities.form')}</p>
                            </div>}
                        {type === 'code' &&
                            <div className='flex space-x-1.5 items-center'>
                                <Code2 size={17} />
                                <p>{t('activities.code')}</p>
                            </div>}
                        {type === 'short-answer' &&
                            <div className='flex space-x-1.5 items-center'>
                                <Pencil size={17} />
                                <p>{t('dashboard.assignments.editor.task_types.short_answer.title')}</p>
                            </div>}
                        {type === 'number-answer' &&
                            <div className='flex space-x-1.5 items-center'>
                                <Hash size={17} />
                                <p>{t('dashboard.assignments.editor.task_types.number_answer.title')}</p>
                            </div>}
                        {type === 'essay' &&
                            <div className='flex space-x-1.5 items-center'>
                                <FilePenLine size={17} />
                                <p>作文</p>
                            </div>}
                    </div>

                    <div className='flex items-center space-x-1'>
                        <EllipsisVertical size={15} />
                    </div>
                    {view === 'teacher' &&
                        <div className='flex bg-amber-200/20 text-xs rounded-full space-x-1 px-2 py-0.5 font-bold outline items-center text-amber-600 outline-1 outline-amber-300/40'>
                            <BookUser size={12} />
                            <p>{t('activities.teacher_view')}</p>
                        </div>
                    }
                    {maxPoints &&
                        <div className='flex bg-emerald-200/20 text-xs rounded-full space-x-1 px-2 py-0.5 font-bold outline items-center text-emerald-600 outline-1 outline-emerald-300/40'>
                            <BookPlus size={12} />
                            <p>{maxPoints} {t('assignments.points')}</p>
                        </div>
                    }
                </div>

                {/* Right side with buttons and actions */}
                <div className='flex flex-wrap gap-2 items-center'>
                    {showSavingDisclaimer &&
                        <div className='flex space-x-2 items-center font-semibold px-3 py-1 outline-dashed outline-red-200 text-red-400 sm:mr-5 rounded-full w-full sm:w-auto mb-2 sm:mb-0'>
                            <InfoIcon size={14} />
                            <p className='text-xs'>{t('activities.dont_forget_to_save')}</p>
                        </div>
                    }

                    {/* Teacher button */}
                    {view === 'teacher' &&
                        <button
                            type="button"
                            onClick={() => runAction('save', saveFC)}
                            disabled={actionIsDisabled}
                            aria-busy={isActionPending('save')}
                            className={actionClassName('flex cursor-pointer items-center space-x-2 rounded-md bg-emerald-300/20 bg-linear-to-bl px-2 py-1 text-emerald-700 outline-dashed outline-offset-2 outline-emerald-500/60 transition-all hover:bg-emerald-300/10 hover:outline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-2 active:outline-offset-1 disabled:cursor-not-allowed')}>
                            {isActionPending('save') ? <Loader2 size={14} className='animate-spin' /> : <Save size={14} />}
                            <p className='text-xs font-semibold'>
                                {isActionPending('save')
                                    ? t('common.saving', { defaultValue: '儲存中...' })
                                    : t('common.save')}
                            </p>
                        </button>
                    }

                    {/* Student button - only show if authenticated */}
                    {canSaveStudentProgress &&
                        <div className='flex w-full flex-col items-stretch gap-1 sm:w-auto sm:items-end'>
                            <button
                                type="button"
                                onClick={() => runAction('submit', submitFC)}
                                disabled={actionIsDisabled || studentActionDisabled}
                                aria-disabled={actionIsDisabled || studentActionDisabled}
                                aria-busy={isActionPending('submit')}
                                aria-describedby={studentActionDisabled ? studentActionDisabledDescriptionId : undefined}
                                className={`${actionClassName('flex px-2 py-1 cursor-pointer rounded-md space-x-2 items-center justify-center mx-auto w-full sm:w-auto bg-linear-to-bl text-emerald-700 bg-emerald-300/20 hover:bg-emerald-300/10 hover:outline-offset-4 active:outline-offset-1 linear transition-all outline-offset-2 outline-dashed outline-emerald-500/60')} ${studentActionDisabled ? 'cursor-not-allowed opacity-60' : ''}`}>
                                {isActionPending('submit') ? <Loader2 size={14} className='animate-spin' /> : <Forward size={14} />}
                                <p className='text-xs font-semibold'>
                                    {isActionPending('submit')
                                        ? t('assignments.saving_task_answer', { defaultValue: '儲存中...' })
                                        : t('assignments.save_task_answer', { defaultValue: '儲存本題答案' })}
                                </p>
                            </button>
                            <p className='text-center text-[10px] font-semibold leading-snug text-slate-400 sm:max-w-[170px] sm:text-right'>
                                {t('assignments.save_task_answer_note', {
                                    defaultValue: '這只是儲存本題，完成全部題目後再提交批改。',
                                })}
                            </p>
                        </div>
                    }

                    {/* Grading button */}
                    {view === 'grading' && gradeFC &&
                        <div
                            className='flex flex-wrap sm:flex-nowrap w-full sm:w-auto px-0.5 py-0.5 rounded-md gap-2 sm:space-x-2 items-center'>
                            {currentPoints !== undefined && currentPoints > 0 && (
                                <p className='font-semibold px-2 text-xs text-emerald-700 bg-emerald-50 rounded-full py-0.5'>{currentPoints}/{maxPoints} {t('assignments.points')}</p>
                            )}
                            <button
                                type="button"
                                onClick={() => runAction('grade', gradeFC)}
                                disabled={actionIsDisabled}
                                aria-busy={isActionPending('grade')}
                                className={actionClassName('flex cursor-pointer items-center space-x-1.5 rounded-md bg-orange-50 px-2 py-1 text-orange-700 transition-colors hover:bg-orange-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed')}>
                                {isActionPending('grade') ? <Loader2 size={14} className='animate-spin' /> : <BookPlus size={14} />}
                                <p className='text-xs font-semibold'>
                                    {isActionPending('grade')
                                        ? t('assignments.grading', { defaultValue: '批改中...' })
                                        : autoGradable ? t('assignments.run_autograde') : t('assignments.grade')}
                                </p>
                            </button>
                        </div>
                    }

                    {/* CustomGrading button */}
                    {view === 'custom-grading' && maxPoints &&
                        <div
                            className='flex w-full flex-wrap items-center gap-2 rounded-md bg-linear-to-bl px-0.5 py-0.5 sm:w-auto sm:flex-nowrap sm:space-x-2'>
                            <p className='font-semibold px-2 text-xs text-orange-700 w-full sm:w-auto'>{t('assignments.current_points', { points: currentPoints })}</p>
                            <div className='flex items-center gap-2 w-full sm:w-auto'>
                                <input
                                    onChange={(e) => setCustomGrade(parseInt(e.target.value))}
                                    placeholder={maxPoints.toString()}
                                    className='w-full sm:w-[100px] light-shadow text-sm py-0.5 outline outline-gray-200 rounded-lg px-2'
                                    type="number"
                                />
                                <button
                                    type="button"
                                    onClick={() => runAction('custom-grade', () => gradeCustomFC?.(customGrade))}
                                    disabled={actionIsDisabled}
                                    aria-busy={isActionPending('custom-grade')}
                                    className={actionClassName('flex items-center space-x-2 whitespace-nowrap rounded-md bg-orange-300/20 bg-linear-to-bl px-2 py-1 text-orange-700 hover:bg-orange-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed')}>
                                    {isActionPending('custom-grade') ? <Loader2 size={14} className='animate-spin' /> : <BookPlus size={14} />}
                                    <p className='text-xs font-semibold'>
                                        {isActionPending('custom-grade')
                                            ? t('assignments.grading', { defaultValue: '批改中...' })
                                            : t('assignments.grade')}
                                    </p>
                                </button>
                            </div>
                        </div>
                    }
                </div>
            </div>
            {children}
        </div>
    )
}

export default AssignmentBoxUI
