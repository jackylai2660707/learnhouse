'use client'
import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext'
import { useAssignmentSubmission } from '@components/Contexts/Assignments/AssignmentSubmissionContext'
import {
  useAssignmentsTask,
  useAssignmentsTaskDispatch,
} from '@components/Contexts/Assignments/AssignmentsTaskContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI'
import {
  getAssignmentTask,
  getAssignmentTaskSubmissionsMe,
  getAssignmentTaskSubmissionsUser,
  handleAssignmentTaskSubmission,
  updateAssignmentTask,
} from '@services/courses/assignments'
import { queryKeys } from '@/lib/query/keys'
import { CheckCircle2, XCircle } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import React, { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { useTranslation } from 'react-i18next'
import { coerceSimplePilotBoolean } from '@lib/simple-pilot-assignments'

type NumberAnswerContents = {
  prompt: string
  correct_value: number
  tolerance: number      // absolute ± tolerance in the same units as correct_value
  unit?: string          // optional display unit like "m/s²" or "kg"
  explanation?: string
}

type TaskNumberAnswerObjectProps = {
  view: 'teacher' | 'student' | 'grading'
  assignmentTaskUUID?: string
  user_id?: string
}

const DEFAULT_CONTENTS: NumberAnswerContents = {
  prompt: '',
  correct_value: 0,
  tolerance: 0,
  unit: '',
  explanation: '',
}

// NOTE: numeric grading runs server-side via _check_number_answer in
// assignments.py. The student's answer is stored as-is on save; the backend
// re-parses and compares against correct_value ± tolerance during finalize.

function normalizeContents(raw: any): NumberAnswerContents {
  return {
    prompt: raw?.prompt ?? '',
    correct_value: Number.isFinite(raw?.correct_value) ? raw.correct_value : 0,
    tolerance: Number.isFinite(raw?.tolerance) ? raw.tolerance : 0,
    unit: raw?.unit ?? '',
    explanation: raw?.explanation ?? '',
  }
}

function TaskNumberAnswerObject({
  view,
  assignmentTaskUUID,
  user_id,
}: TaskNumberAnswerObjectProps) {
  const { t } = useTranslation()
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const assignmentTaskState = useAssignmentsTask() as any
  const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
  const assignment = useAssignments() as any
  const queryClient = useQueryClient()
  // Same reveal gate as the other task types: teacher must opt in, and the
  // submission must already be GRADED before any correct-answer hint appears.
  const assignmentSubmission = useAssignmentSubmission() as any
  const assignmentSubmissionStatus = Array.isArray(assignmentSubmission) && assignmentSubmission.length > 0
    ? assignmentSubmission[0].submission_status
    : null
  const assignmentAttemptNumber = Array.isArray(assignmentSubmission) && assignmentSubmission.length > 0
    ? assignmentSubmission[0].attempt_number
    : null
  const submissionIsFinal = view === 'student'
    && !!assignmentSubmissionStatus
    && !['PENDING', 'NOT_SUBMITTED'].includes(assignmentSubmissionStatus)
  const submissionIsGraded = Array.isArray(assignmentSubmission)
    && assignmentSubmission.length > 0
    && assignmentSubmissionStatus === 'GRADED'
  const showCorrectAnswers = view === 'student'
    && submissionIsGraded
    && coerceSimplePilotBoolean(assignment?.assignment_object?.show_correct_answers)

  const [contents, setContents] = useState<NumberAnswerContents>(DEFAULT_CONTENTS)
  const [studentAnswer, setStudentAnswer] = useState<string>('')
  const [initialAnswer, setInitialAnswer] = useState<string>('')

  const [userSubmissions, setUserSubmissions] = useState<any>(null)
  const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null)
  const [assignmentTaskOutsideProvider, setAssignmentTaskOutsideProvider] =
    useState<any>(null)
  const showSavingDisclaimer = view === 'student' && studentAnswer !== initialAnswer

  // --- TEACHER VIEW ---
  useEffect(() => {
    if (view === 'teacher' && assignmentTaskState?.assignmentTask?.contents) {
      const c = assignmentTaskState.assignmentTask.contents
      if (c.prompt !== undefined || c.correct_value !== undefined) {
        // The selected task is external provider state; refresh this local edit buffer when it changes.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setContents(normalizeContents(c))
      }
    }
  }, [view, assignmentTaskState])

  // --- STUDENT / GRADING VIEW ---
  async function loadTaskDefinition() {
    if (!assignmentTaskUUID) return
    const res = await getAssignmentTask(assignmentTaskUUID, access_token)
    if (res.success) {
      setAssignmentTaskOutsideProvider(res.data)
      if (res.data.contents) {
        setContents(normalizeContents(res.data.contents))
      }
    }
  }

  async function loadOwnSubmission() {
    if (!assignmentTaskUUID) return
    const res = await getAssignmentTaskSubmissionsMe(
      assignmentTaskUUID,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success && res.data) {
      setUserSubmissions(res.data)
      const saved = res.data.task_submission?.answer ?? ''
      setStudentAnswer(String(saved))
      setInitialAnswer(String(saved))
    } else {
      setUserSubmissions(null)
      setStudentAnswer('')
      setInitialAnswer('')
    }
  }

  async function loadUserSubmission() {
    if (!assignmentTaskUUID || !user_id) return
    const res = await getAssignmentTaskSubmissionsUser(
      assignmentTaskUUID,
      user_id,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success && res.data) {
      setUserSubmissions(res.data)
      setUserSubmissionObject(res.data)
      const saved = res.data.task_submission?.answer ?? ''
      setStudentAnswer(String(saved))
      setInitialAnswer(String(saved))
    }
  }

  useEffect(() => {
    if (view === 'student') {
      // These async loaders update state only after their API requests resolve.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      loadTaskDefinition()
      loadOwnSubmission()
    } else if (view === 'grading') {
      loadTaskDefinition()
      loadUserSubmission()
    }
  }, [view, assignmentTaskUUID, assignment, access_token, assignmentSubmissionStatus, assignmentAttemptNumber]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- SAVE (teacher) ---
  async function saveFC() {
    if (!assignmentTaskState?.assignmentTask?.assignment_task_uuid) return
    const res = await updateAssignmentTask(
      { contents },
      assignmentTaskState.assignmentTask.assignment_task_uuid,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success) {
      assignmentTaskStateHook({ type: 'reload' })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
      toast.success(t('dashboard.assignments.editor.toasts.task_updated'))
    } else {
      toast.error(t('dashboard.assignments.editor.toasts.task_update_error'))
    }
  }

  // --- SAVE PROGRESS (student) ---
  // Matches the QUIZ / FORM pattern: persist the draft answer only. Grading
  // is done server-side via _server_verified_task_grade when the assignment
  // is finalized — either by the auto-grade path on submission or by the
  // teacher clicking "Set final grade". Keeping the client out of the
  // grading loop also means DevTools tampering can't inflate the score.
  async function submitFC() {
    if (!assignmentTaskUUID) return
    if (submissionIsFinal) {
      toast.error('這份作業已提交，請按「重做」後再修改答案。')
      return
    }
    if (!studentAnswer.trim()) {
      toast.error(t('assignments.save_number_answer_first', {
        defaultValue: '請先輸入答案，再儲存本題。',
      }))
      return
    }
    const values = {
      assignment_task_submission_uuid:
        userSubmissions?.assignment_task_submission_uuid || null,
      task_submission: {
        answer: studentAnswer,
      },
      grade: 0,
      task_submission_grade_feedback: '',
    }
    const res = await handleAssignmentTaskSubmission(
      values,
      assignmentTaskUUID,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success) {
      setUserSubmissions(res.data)
      setInitialAnswer(studentAnswer)
      queryClient.invalidateQueries({
        queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid),
      })
      toast.success(t('assignments.task_answer_saved_not_submitted'))
    } else {
      toast.error(t('dashboard.assignments.editor.toasts.task_save_error'))
    }
  }

  const gradedPassed = userSubmissionObject?.grade > 0

  // For display in grading view: e.g. "9.81 ± 0.05 m/s²"
  const acceptedRange =
    contents.tolerance > 0
      ? `${contents.correct_value} ± ${contents.tolerance}${contents.unit ? ' ' + contents.unit : ''}`
      : `${contents.correct_value}${contents.unit ? ' ' + contents.unit : ''}`

  return (
    <AssignmentBoxUI
      type="number-answer"
      view={view}
      saveFC={saveFC}
      submitFC={submitFC}
      currentPoints={userSubmissionObject?.grade}
      maxPoints={
        assignmentTaskOutsideProvider?.max_grade_value ||
        assignmentTaskState?.assignmentTask?.max_grade_value
      }
      showSavingDisclaimer={showSavingDisclaimer}
    >
      <div className="flex w-full min-w-0 flex-col space-y-4">
        {/* === TEACHER VIEW === */}
        {view === 'teacher' && (
          <>
            <div className="flex min-w-0 flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.number_answer.prompt_label')}
              </label>
              <textarea
                value={contents.prompt}
                onChange={(e) =>
                  setContents((prev) => ({ ...prev, prompt: e.target.value }))
                }
                placeholder={t(
                  'dashboard.assignments.editor.task_editor.number_answer.prompt_placeholder'
                )}
                rows={2}
                className="w-full min-w-0 resize-y rounded-md border border-gray-200 bg-white px-3 py-2 text-sm"
              />
            </div>

            <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex min-w-0 flex-col space-y-1">
                <label className="text-xs font-semibold text-slate-500">
                  {t('dashboard.assignments.editor.task_editor.number_answer.correct_value_label')}
                </label>
                <input
                  type="number"
                  step="any"
                  value={contents.correct_value}
                  onChange={(e) =>
                    setContents((prev) => ({
                      ...prev,
                      correct_value: Number.parseFloat(e.target.value) || 0,
                    }))
                  }
                  className="w-full min-w-0 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm"
                />
              </div>
              <div className="flex min-w-0 flex-col space-y-1">
                <label className="text-xs font-semibold text-slate-500">
                  {t('dashboard.assignments.editor.task_editor.number_answer.tolerance_label')}
                </label>
                <input
                  type="number"
                  step="any"
                  min={0}
                  value={contents.tolerance}
                  onChange={(e) =>
                    setContents((prev) => ({
                      ...prev,
                      tolerance: Math.max(0, Number.parseFloat(e.target.value) || 0),
                    }))
                  }
                  className="w-full min-w-0 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm"
                />
              </div>
            </div>

            <div className="flex min-w-0 flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.number_answer.unit_label')}
              </label>
              <input
                value={contents.unit ?? ''}
                onChange={(e) =>
                  setContents((prev) => ({ ...prev, unit: e.target.value }))
                }
                placeholder={t(
                  'dashboard.assignments.editor.task_editor.number_answer.unit_placeholder'
                )}
                className="w-full min-w-0 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm"
              />
              <p className="text-[10px] text-slate-400">
                {t('dashboard.assignments.editor.task_editor.number_answer.unit_hint')}
              </p>
            </div>

            <div className="flex min-w-0 flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.number_answer.explanation_label')}
              </label>
              <textarea
                value={contents.explanation ?? ''}
                onChange={(e) =>
                  setContents((prev) => ({ ...prev, explanation: e.target.value }))
                }
                placeholder={t(
                  'dashboard.assignments.editor.task_editor.number_answer.explanation_placeholder'
                )}
                rows={2}
                className="w-full min-w-0 resize-y rounded-md border border-gray-200 bg-white px-3 py-2 text-sm"
              />
            </div>

            <div className="flex min-w-0 flex-wrap items-center gap-1.5 rounded-md bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-500">
              <span>{t('dashboard.assignments.editor.task_editor.number_answer.preview_label')}:</span>
              <span className="min-w-0 break-all font-mono font-semibold text-slate-700">
                {acceptedRange}
              </span>
            </div>
          </>
        )}

        {/* === STUDENT VIEW === */}
        {/* Saving is just persisting a draft — no Correct/Incorrect feedback
            here. The student sees their grade after the whole assignment is
            graded (visible in the activity header badge). */}
        {view === 'student' && (
          <>
            {contents.prompt && (
              <p className="text-sm text-slate-700 whitespace-pre-wrap">{contents.prompt}</p>
            )}
            <div className="flex min-w-0 items-center gap-2">
              <input
                type="text"
                inputMode="decimal"
                value={studentAnswer}
                onChange={(e) => !submissionIsFinal && setStudentAnswer(e.target.value)}
                readOnly={submissionIsFinal}
                placeholder={t(
                  'dashboard.assignments.editor.task_editor.number_answer.your_answer_placeholder'
                )}
                className="w-full min-w-0 rounded-md border-2 border-gray-200 bg-white px-3 py-2 font-mono text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-200 sm:max-w-[200px]"
              />
              {contents.unit && (
                <span className="min-w-0 break-words text-sm font-medium text-slate-500">{contents.unit}</span>
              )}
            </div>
            {showCorrectAnswers && (
              <div className="flex flex-col space-y-1.5 p-3 rounded-md bg-emerald-50 border border-emerald-200">
                <div className="flex items-center space-x-1.5 text-xs font-semibold text-emerald-700">
                  <CheckCircle2 size={13} />
                  <span>{t('dashboard.assignments.editor.task_editor.number_answer.accepted_range_label')}</span>
                </div>
                <div className="text-sm font-mono text-emerald-800">{acceptedRange}</div>
                {contents.explanation && (
                  <p className="text-xs text-emerald-800/80 mt-1 whitespace-pre-wrap">{contents.explanation}</p>
                )}
              </div>
            )}
          </>
        )}

        {/* === GRADING VIEW === */}
        {view === 'grading' && (
          <>
            {contents.prompt && (
              <p className="text-sm text-slate-700 whitespace-pre-wrap">{contents.prompt}</p>
            )}
            <div className="flex flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.number_answer.student_answer_label')}
              </label>
              <div className="px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-md font-mono">
                {studentAnswer ? (
                  <>
                    {studentAnswer}
                    {contents.unit && <span className="text-slate-500 ml-1.5 font-sans">{contents.unit}</span>}
                  </>
                ) : (
                  <span className="text-gray-400 italic font-sans">
                    {t('dashboard.assignments.editor.task_editor.number_answer.no_answer')}
                  </span>
                )}
              </div>
            </div>
            <div className="flex flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.number_answer.accepted_range_label')}
              </label>
              <div className="px-3 py-2 text-sm bg-emerald-50 border border-emerald-200 rounded-md font-mono text-emerald-700">
                {acceptedRange}
              </div>
            </div>
            <div
              className={`flex items-center space-x-2 p-2.5 rounded-md text-xs font-semibold ${
                gradedPassed
                  ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                  : 'bg-rose-50 text-rose-700 border border-rose-200'
              }`}
            >
              {gradedPassed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
              <span>
                {gradedPassed
                  ? t('dashboard.assignments.editor.task_editor.number_answer.correct')
                  : t('dashboard.assignments.editor.task_editor.number_answer.incorrect')}
              </span>
            </div>
          </>
        )}
      </div>
    </AssignmentBoxUI>
  )
}

export default TaskNumberAnswerObject
