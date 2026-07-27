import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import { useAssignmentSubmission, useAssignmentTaskSubmissions } from '@components/Contexts/Assignments/AssignmentSubmissionContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { useOrg } from '@components/Contexts/OrgContext';
import { getTaskRefFileDir } from '@services/media/media';
import {
  createMyAssignmentRemediation,
  getMyAssignmentRemediation,
  retryAssignmentSubmission,
  submitMyAssignmentRemediation,
} from '@services/courses/assignments';
import TaskFileObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskFileObject';
import TaskQuizObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskQuizObject'
import TaskFormObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskFormObject'
import TaskCodeObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskCodeObject'
import TaskShortAnswerObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskShortAnswerObject'
import TaskNumberAnswerObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskNumberAnswerObject'
import TaskEssayObject from 'app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskEssayObject'
import toast from 'react-hot-toast';
import { Backpack, BookOpenCheck, Calendar, CheckCircle2, Download, EllipsisVertical, Info, MessageSquare, NotebookPen, RotateCcw, Save, XCircle } from 'lucide-react';
import Link from 'next/link';
import React, { useEffect } from 'react'
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { formatZhHkDate } from '@lib/date-format';
import ConfirmationModal from '@components/Objects/StyledElements/ConfirmationModal/ConfirmationModal';
import { assignmentTaskDisplayName, isAssignmentTaskAnswerComplete } from './assignmentCompletion';
import { coerceSimplePilotBoolean } from '@lib/simple-pilot-assignments';

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  if (response instanceof Error && response.message) return response.message
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；')
  return fallback
}

async function requireSuccess(responsePromise: Promise<any>, fallback: string) {
  const response = await responsePromise
  if (!response?.success) {
    throw new Error(responseErrorMessage(response, fallback))
  }
  return response.data
}

function displayTaskFeedback(feedback: string) {
  if (!feedback) return ''
  try {
    const parsed = JSON.parse(feedback)
    if (parsed?.type === 'ai_essay_grading') {
      const improvements = Array.isArray(parsed.improvements)
        ? parsed.improvements.slice(0, 2).join('；')
        : ''
      return [parsed.overall_feedback || parsed.summary, improvements ? `建議：${improvements}` : '']
        .filter(Boolean)
        .join('\n')
    }
  } catch {
    return feedback
  }
  return feedback
}

function firstRemediationQuestion(question: any) {
  return Array.isArray(question?.contents?.questions) ? question.contents.questions[0] : null
}

function remediationAnswerComplete(question: any, answer: any) {
  if (!question || !answer) return false
  if (question.assignment_type === 'SHORT_ANSWER') {
    return Boolean(String(answer.answer || '').trim())
  }
  const sourceQuestion = firstRemediationQuestion(question)
  if (question.assignment_type === 'QUIZ') {
    const questionUUID = sourceQuestion?.questionUUID
    const options = Array.isArray(sourceQuestion?.options) ? sourceQuestion.options : []
    const submissions = Array.isArray(answer.submissions) ? answer.submissions : []
    return options.some((option: any) =>
      submissions.some((item: any) =>
        item?.questionUUID === questionUUID &&
        item?.optionUUID === option?.optionUUID &&
        Boolean(item?.answer)
      )
    )
  }
  if (question.assignment_type === 'FORM') {
    const questionUUID = sourceQuestion?.questionUUID
    const blank = Array.isArray(sourceQuestion?.blanks) ? sourceQuestion.blanks[0] : null
    const submissions = Array.isArray(answer.submissions) ? answer.submissions : []
    return submissions.some((item: any) =>
      item?.questionUUID === questionUUID &&
      item?.blankUUID === blank?.blankUUID &&
      String(item?.answer || '').trim()
    )
  }
  return false
}

function AssignmentStudentActivity() {
  const { t } = useTranslation()
  const assignments = useAssignments() as any;
  const org = useOrg() as any;
  const session = useLHSession() as any;
  const queryClient = useQueryClient();
  const submission = useAssignmentSubmission() as any;
  const taskSubmissionsMap = useAssignmentTaskSubmissions() as Record<string, any> | null;
  const [isRetrying, setIsRetrying] = React.useState(false);
  const [remediationAnswers, setRemediationAnswers] = React.useState<Record<string, any>>({});
  const retryInFlightRef = React.useRef(false);
  const currentSubmission = Array.isArray(submission) && submission.length > 0 ? submission[0] : null;
  const assignmentUUID = assignments?.assignment_object?.assignment_uuid;
  const accessToken = session?.data?.tokens?.access_token;
  const sortedTasks = React.useMemo(
    () => [...(assignments?.assignment_tasks || [])].sort((a: any, b: any) => a.id - b.id),
    [assignments?.assignment_tasks]
  );
  const hasTasks = sortedTasks.length > 0;

  // Per-task grading is rendered inline only after the whole assignment has
  // been graded — that's when raw task grades are guaranteed to reflect the
  // server-verified value (auto-grade or teacher override). Before that,
  // task.grade is the placeholder 0 from save-progress.
  const isGraded = currentSubmission?.submission_status === 'GRADED';
  const isSubmittedWaiting =
    currentSubmission?.submission_status === 'SUBMITTED' ||
    currentSubmission?.submission_status === 'LATE';
  const isLateSubmittedWaiting = currentSubmission?.submission_status === 'LATE';
  const isAwaitingSubmission =
    !currentSubmission ||
    currentSubmission.submission_status === 'PENDING' ||
    currentSubmission.submission_status === 'NOT_SUBMITTED';

  // Attempt indicator. Only worth showing when the teacher actually enabled
  // retries and the student has burned at least one attempt — otherwise it's
  // noise.
  const allowRetries = coerceSimplePilotBoolean(assignments?.assignment_object?.allow_retries);
  const maxRetries = Number(assignments?.assignment_object?.max_retries || 0);
  const currentAttempt = Number(
    currentSubmission?.attempt_number || 1
  );
  const isAutoGradingAssignment = coerceSimplePilotBoolean(assignments?.assignment_object?.auto_grading);
  const retryLearningNote = allowRetries
    ? t('assignments.score_policy_highest_student_note')
    : '';
  const showAttemptBadge = allowRetries && currentAttempt > 1;
  const canRetry = isGraded && allowRetries && (maxRetries === 0 || currentAttempt < maxRetries);
  const showAnswerReviewHint = isGraded && coerceSimplePilotBoolean(assignments?.assignment_object?.show_correct_answers);
  const totalMaxGrade = sortedTasks.reduce(
    (sum: number, task: any) => sum + Number(task.max_grade_value || 0),
    0
  );
  const totalGrade = Number(currentSubmission?.grade || 0);
  const overallPercentage =
    totalMaxGrade > 0 ? Math.round((totalGrade / totalMaxGrade) * 100) : 0;
  const bestGrade = Number(currentSubmission?.best_grade ?? 0);
  const bestAttemptNumber = Number(currentSubmission?.best_attempt_number ?? 0);
  const showBestScoreBadge =
    currentSubmission?.submission_status === 'PENDING' &&
    bestAttemptNumber > 0 &&
    totalMaxGrade > 0;
  const showResultBestScoreBadge =
    isGraded &&
    currentAttempt > 1 &&
    bestAttemptNumber > 0 &&
    totalMaxGrade > 0;
  const taskCompletionRows = React.useMemo(
    () => sortedTasks.map((task: any, index: number) => ({
      task,
      index,
      complete: taskSubmissionsMap
        ? isAssignmentTaskAnswerComplete(task, taskSubmissionsMap[task.assignment_task_uuid])
        : false,
    })),
    [sortedTasks, taskSubmissionsMap]
  );
  const completedAnswerCount = taskCompletionRows.filter((row) => row.complete).length;
  const incompleteAnswerRows = taskCompletionRows.filter((row) => !row.complete);
  const answerProgressPercent = hasTasks
    ? Math.round((completedAnswerCount / sortedTasks.length) * 100)
    : 0;
  const nextIncompleteAnswer = incompleteAnswerRows[0];
  const canShowAnswerProgress = isAwaitingSubmission && hasTasks && taskSubmissionsMap !== null;

  const passingThreshold = 50;
  const overallPassed = overallPercentage >= passingThreshold;
  const resultTitleKey = overallPassed
    ? 'assignments.result_passed_title'
    : canRetry
      ? 'assignments.result_retry_title'
      : 'assignments.result_review_title';
  const resultDescriptionKey = overallPassed
    ? 'assignments.result_passed_description'
    : canRetry
      ? 'assignments.result_retry_description'
      : 'assignments.result_review_description';
  const gradedTaskSummaries = sortedTasks.map((task: any) => {
    const taskSubmission = taskSubmissionsMap ? taskSubmissionsMap[task.assignment_task_uuid] : null;
    const taskGrade = Number(taskSubmission?.grade ?? 0);
    const taskMax = Number(task.max_grade_value || 0);
    const taskPercentage = taskMax > 0 ? Math.round((taskGrade / taskMax) * 100) : 0;
    return {
      submitted: !!taskSubmission,
      passed: !!taskSubmission && taskPercentage >= passingThreshold,
    };
  });
  const submittedTaskCount = gradedTaskSummaries.filter((task) => task.submitted).length;
  const passedTaskCount = gradedTaskSummaries.filter((task) => task.passed).length;
  const practiceTaskCount = Math.max(0, submittedTaskCount - passedTaskCount);
  const firstPracticeTaskIndex = gradedTaskSummaries.findIndex((task) => task.submitted && !task.passed);
  const firstPracticeTask = firstPracticeTaskIndex >= 0 ? sortedTasks[firstPracticeTaskIndex] : null;
  const remediationQuery = useQuery({
    queryKey: queryKeys.assignments.remediation(assignmentUUID || ''),
    queryFn: async () => requireSuccess(
      getMyAssignmentRemediation(assignmentUUID || '', accessToken || ''),
      '載入補練失敗'
    ),
    enabled: isGraded && Boolean(assignmentUUID && accessToken),
    retry: false,
  });
  const generateRemediationMutation = useMutation({
    mutationFn: async () => requireSuccess(
      createMyAssignmentRemediation(assignmentUUID || '', accessToken || ''),
      '建立補練失敗'
    ),
    onSuccess: (data) => {
      toast.success('補練已準備好');
      queryClient.setQueryData(queryKeys.assignments.remediation(assignmentUUID || ''), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.remediation(assignmentUUID || '') });
    },
    onError: (error: any) => {
      toast.error(responseErrorMessage(error, '建立補練失敗'));
    },
  });
  const submitRemediationMutation = useMutation({
    mutationFn: async () => {
      const questions = Array.isArray(remediationQuery.data?.practice?.questions)
        ? remediationQuery.data.practice.questions
        : [];
      return requireSuccess(
        submitMyAssignmentRemediation(
          assignmentUUID || '',
          questions.map((question: any) => ({
            question_uuid: question.question_uuid,
            answer: remediationAnswers[question.question_uuid] || {},
          })),
          accessToken || ''
        ),
        '提交補練失敗'
      );
    },
    onSuccess: (data) => {
      toast.success('補練已完成');
      queryClient.setQueryData(queryKeys.assignments.remediation(assignmentUUID || ''), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.remediation(assignmentUUID || '') });
    },
    onError: (error: any) => {
      toast.error(responseErrorMessage(error, '提交補練失敗'));
    },
  });
  const remediationStatus = remediationQuery.data?.status;
  const remediationPractice = remediationQuery.data?.practice;
  const remediationQuestions = Array.isArray(remediationPractice?.questions)
    ? remediationPractice.questions
    : [];
  const showRemediationPanel = isGraded && (
    remediationQuery.isLoading ||
    remediationQuery.data?.eligible ||
    remediationStatus === 'available' ||
    remediationStatus === 'generated' ||
    remediationStatus === 'completed'
  );
  const remediationReadyToSubmit =
    remediationQuestions.length > 0 &&
    remediationQuestions.every((question: any) =>
      remediationAnswerComplete(question, remediationAnswers[question.question_uuid])
    );

  function scrollToTask(taskUUID?: string) {
    if (!taskUUID) return;
    document
      .getElementById(`assignment-task-${taskUUID}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function scrollToSubmitTools() {
    document
      .getElementById('assignment-submit-tools')
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  async function retrySubmissionUI() {
    if (!assignmentUUID || !accessToken || isRetrying || retryInFlightRef.current) return;

    retryInFlightRef.current = true;
    setIsRetrying(true);
    try {
      const res = await retryAssignmentSubmission(assignmentUUID, accessToken);
      if (res.success) {
        toast.success(t('assignments.retry_assignment_success'));
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.submission(assignmentUUID) });
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignmentUUID) });
      } else {
        toast.error(responseErrorMessage(res, t('assignments.retry_assignment_failed')));
      }
    } catch (error: any) {
      toast.error(responseErrorMessage(error, t('assignments.retry_assignment_failed')));
    } finally {
      retryInFlightRef.current = false;
      setIsRetrying(false);
    }
  }

  function setRemediationQuizAnswer(question: any, option: any) {
    const sourceQuestion = firstRemediationQuestion(question);
    const options = Array.isArray(sourceQuestion?.options) ? sourceQuestion.options : [];
    setRemediationAnswers((current) => ({
      ...current,
      [question.question_uuid]: {
        submissions: options.map((item: any) => ({
          questionUUID: sourceQuestion?.questionUUID,
          optionUUID: item?.optionUUID,
          answer: item?.optionUUID === option?.optionUUID,
        })),
      },
    }));
  }

  function setRemediationFormAnswer(question: any, value: string) {
    const sourceQuestion = firstRemediationQuestion(question);
    const blank = Array.isArray(sourceQuestion?.blanks) ? sourceQuestion.blanks[0] : null;
    setRemediationAnswers((current) => ({
      ...current,
      [question.question_uuid]: {
        submissions: [
          {
            questionUUID: sourceQuestion?.questionUUID,
            blankUUID: blank?.blankUUID,
            answer: value,
          },
        ],
      },
    }));
  }

  function setRemediationShortAnswer(question: any, value: string) {
    setRemediationAnswers((current) => ({
      ...current,
      [question.question_uuid]: { answer: value },
    }));
  }

  useEffect(() => {
  }, [assignments, org])


  return (
    <div className='flex flex-col space-y-4 md:space-y-6'>
      <div className='flex flex-col md:flex-row justify-center md:space-x-3 space-y-3 md:space-y-0 items-center'>
        <div className='text-xs h-fit flex space-x-3 items-center'>
          <div className='flex gap-2 py-2 px-4 md:px-5 h-fit text-sm text-slate-700 bg-slate-100/5 rounded-full nice-shadow items-center'>
            <Backpack size={14} className="md:size-[14px]" />
            <p className='font-semibold'>{t('activities.assignment')}</p>
          </div>
        </div>
        <div>
          <div className='flex gap-2 items-center flex-wrap justify-center'>
            <EllipsisVertical className='text-slate-400 hidden md:block' size={18} />
            <div className='flex gap-2 items-center'>
              <div className='flex gap-1 md:space-x-2 text-xs items-center text-slate-400'>
                <Calendar size={14} />
                <p className='font-semibold'>{t('assignments.due_date')}</p>
                <p className='font-semibold'>{formatZhHkDate(assignments?.assignment_object?.due_date)}</p>
              </div>
            </div>
            {showAttemptBadge && (
              <div className='flex gap-1.5 items-center text-xs px-2.5 py-1 rounded-full bg-fuchsia-50 text-fuchsia-700 font-semibold nice-shadow'>
                <RotateCcw size={12} />
                <span>
                  {maxRetries
                    ? t('assignments.attempt_count_bounded', {
                        current: currentAttempt,
                        max: maxRetries,
                      })
                    : t('assignments.attempt_count', { current: currentAttempt })}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {isGraded && hasTasks && (
        <div className={`rounded-lg border p-4 md:p-5 nice-shadow ${
          overallPassed
            ? 'border-emerald-200 bg-emerald-50/70'
            : 'border-amber-200 bg-amber-50/70'
        }`}>
          <div className='flex flex-col gap-4 md:flex-row md:items-center md:justify-between'>
            <div className='flex items-start gap-3'>
              <div className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white nice-shadow ${
                overallPassed ? 'text-emerald-600' : 'text-amber-600'
              }`}>
                {overallPassed ? <CheckCircle2 size={20} /> : <RotateCcw size={20} />}
              </div>
              <div className='space-y-1'>
                <p className='text-sm font-black text-slate-900'>
                  {t(resultTitleKey)}
                </p>
                <p className='text-xs leading-relaxed text-slate-600'>
                  {t(resultDescriptionKey)}
                </p>
                {showAnswerReviewHint && (
                  <div className='mt-2 inline-flex max-w-xl items-start gap-2 rounded-lg bg-white/75 px-3 py-2 text-xs font-semibold leading-relaxed text-slate-700 nice-shadow'>
                    <BookOpenCheck size={14} className='mt-0.5 shrink-0 text-cyan-700' />
                    <span>{t('assignments.result_answer_review_hint')}</span>
                  </div>
                )}
                {submittedTaskCount > 0 && (
                  <div className='mt-3 flex flex-wrap gap-2'>
                    <div className='inline-flex items-center gap-1.5 rounded-full bg-white/80 px-3 py-1 text-[11px] font-bold text-slate-600 nice-shadow'>
                      <span>{t('assignments.result_total_tasks')}</span>
                      <span className='text-slate-900 tabular-nums'>{submittedTaskCount}/{sortedTasks.length}</span>
                    </div>
                    <div className='inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-3 py-1 text-[11px] font-bold text-emerald-700'>
                      <CheckCircle2 size={12} />
                      <span>{t('assignments.result_correct_tasks')}</span>
                      <span className='tabular-nums'>{passedTaskCount}</span>
                    </div>
                    <div className='inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-3 py-1 text-[11px] font-bold text-amber-700'>
                      <RotateCcw size={12} />
                      <span>{t('assignments.result_practice_tasks')}</span>
                      <span className='tabular-nums'>{practiceTaskCount}</span>
                    </div>
                  </div>
                )}
                {firstPracticeTask && (
                  <button
                    type='button'
                    onClick={() => scrollToTask(firstPracticeTask.assignment_task_uuid)}
                    className='mt-2 inline-flex h-8 items-center gap-2 rounded-lg bg-white/85 px-3 text-xs font-black text-amber-800 nice-shadow hover:bg-white'
                  >
                    <BookOpenCheck size={13} />
                    <span>
                      {t('assignments.jump_to_first_practice_task', {
                        defaultValue: '查看第一題要練習',
                      })}
                    </span>
                    <span className='rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700'>
                      {t('assignments.task', { defaultValue: '題目' })} {firstPracticeTaskIndex + 1}
                    </span>
                  </button>
                )}
              </div>
            </div>
            <div className='flex flex-col gap-3 md:items-end'>
              <div className='flex items-baseline gap-2 rounded-md bg-white px-3 py-2 nice-shadow'>
                <span className='text-2xl font-black leading-none text-slate-900 tabular-nums'>
                  {overallPercentage}%
                </span>
                <span className='text-xs font-semibold text-slate-500'>
                  {totalGrade}/{totalMaxGrade || 0}
                </span>
              </div>
              {canRetry && (
                <ConfirmationModal
                  confirmationButtonText={t('assignments.retry_assignment')}
                  confirmationMessage={t('assignments.retry_assignment_confirm')}
                  dialogTitle={t('assignments.retry_assignment_title')}
                  dialogTrigger={
                    <button
                      type='button'
                      disabled={isRetrying}
                      className='inline-flex h-9 items-center justify-center gap-2 rounded-md bg-slate-900 px-4 text-sm font-bold text-white transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-50'
                    >
                      <RotateCcw size={14} />
                      {t('assignments.retry_assignment')}
                    </button>
                  }
                  functionToExecute={retrySubmissionUI}
                  status='warning'
                />
              )}
              {showResultBestScoreBadge && (
                <div className='flex max-w-xs items-start gap-2 rounded-lg border border-emerald-100 bg-white/85 px-3 py-2 text-[11px] font-bold leading-relaxed text-emerald-800 nice-shadow'>
                  <CheckCircle2 size={13} className='mt-0.5 shrink-0 text-emerald-600' />
                  <span>
                    {t('assignments.result_best_score_retained', {
                      grade: bestGrade,
                      max: totalMaxGrade,
                      attempt: bestAttemptNumber,
                    })}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {showRemediationPanel && (
        <div className='rounded-xl border border-amber-200 bg-amber-50/70 p-4 md:p-5 nice-shadow'>
          <div className='flex flex-col gap-4 md:flex-row md:items-start md:justify-between'>
            <div className='flex items-start gap-3'>
              <div className='mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white text-amber-700 nice-shadow'>
                <BookOpenCheck size={20} />
              </div>
              <div>
                <p className='text-sm font-black text-slate-900'>錯題補練</p>
                <p className='mt-1 text-xs leading-relaxed text-slate-600'>
                  系統會根據這次錯題整理 2-3 題短練習。補練只幫你打基礎，不會改變原作業分數。
                </p>
                {remediationQuery.data?.message && remediationStatus !== 'completed' && (
                  <p className='mt-2 text-[11px] font-bold text-amber-800'>{remediationQuery.data.message}</p>
                )}
              </div>
            </div>
            {remediationStatus === 'available' && (
              <button
                type='button'
                disabled={generateRemediationMutation.isPending}
                onClick={() => generateRemediationMutation.mutate()}
                className='inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-amber-700 px-4 text-sm font-black text-white hover:bg-amber-800 disabled:cursor-not-allowed disabled:opacity-60'
              >
                <RotateCcw size={14} />
                {generateRemediationMutation.isPending ? '準備中' : '補練一下'}
              </button>
            )}
            {remediationStatus === 'completed' && (
              <div className='rounded-lg bg-white px-3 py-2 text-right nice-shadow'>
                <p className='text-[11px] font-bold text-slate-500'>補練分數</p>
                <p className='text-lg font-black text-slate-950 tabular-nums'>
                  {remediationPractice?.score ?? 0}/{remediationPractice?.max_score ?? 0}
                </p>
              </div>
            )}
          </div>

          {remediationQuery.isLoading && (
            <div className='mt-4 rounded-lg border border-amber-100 bg-white/70 px-3 py-2 text-xs font-semibold text-amber-800'>
              正在檢查是否需要補練...
            </div>
          )}

          {remediationQuestions.length > 0 && remediationStatus !== 'completed' && (
            <div className='mt-4 space-y-3'>
              {remediationQuestions.map((question: any, index: number) => {
                const sourceQuestion = firstRemediationQuestion(question);
                const answer = remediationAnswers[question.question_uuid] || {};
                const options = Array.isArray(sourceQuestion?.options) ? sourceQuestion.options : [];
                const blanks = Array.isArray(sourceQuestion?.blanks) ? sourceQuestion.blanks : [];
                const selectedOptionUUID = Array.isArray(answer.submissions)
                  ? answer.submissions.find((item: any) => item?.answer)?.optionUUID
                  : null;
                const formValue = Array.isArray(answer.submissions)
                  ? String(answer.submissions[0]?.answer || '')
                  : '';

                return (
                  <div key={question.question_uuid} className='rounded-lg border border-amber-100 bg-white p-3 nice-shadow'>
                    <div className='flex items-start justify-between gap-3'>
                      <div>
                        <p className='text-[11px] font-black uppercase text-amber-700'>
                          補練 {index + 1}
                        </p>
                        <p className='mt-1 text-sm font-bold leading-relaxed text-slate-900'>
                          {sourceQuestion?.questionText || question.contents?.prompt || question.description}
                        </p>
                      </div>
                      <span className='shrink-0 rounded-full bg-amber-100 px-2 py-1 text-[10px] font-black text-amber-800'>
                        {question.assignment_type === 'QUIZ'
                          ? '選擇'
                          : question.assignment_type === 'FORM'
                            ? '填空'
                            : '短答'}
                      </span>
                    </div>

                    {question.assignment_type === 'QUIZ' && (
                      <div className='mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2'>
                        {options.map((option: any) => (
                          <button
                            key={option.optionUUID}
                            type='button'
                            onClick={() => setRemediationQuizAnswer(question, option)}
                            className={`min-h-10 rounded-lg border px-3 py-2 text-left text-xs font-bold transition-colors ${
                              selectedOptionUUID === option.optionUUID
                                ? 'border-amber-500 bg-amber-100 text-amber-950'
                                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                            }`}
                          >
                            {option.text}
                          </button>
                        ))}
                      </div>
                    )}

                    {question.assignment_type === 'FORM' && (
                      <div className='mt-3'>
                        <input
                          value={formValue}
                          onChange={(event) => setRemediationFormAnswer(question, event.target.value)}
                          placeholder={blanks[0]?.placeholder || '填寫答案'}
                          className='h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-amber-500'
                        />
                      </div>
                    )}

                    {question.assignment_type === 'SHORT_ANSWER' && (
                      <div className='mt-3'>
                        <input
                          value={String(answer.answer || '')}
                          onChange={(event) => setRemediationShortAnswer(question, event.target.value)}
                          placeholder='輸入短答案'
                          className='h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-amber-500'
                        />
                      </div>
                    )}
                  </div>
                );
              })}

              <button
                type='button'
                disabled={!remediationReadyToSubmit || submitRemediationMutation.isPending}
                onClick={() => submitRemediationMutation.mutate()}
                className='inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-black text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50'
              >
                <CheckCircle2 size={15} />
                {submitRemediationMutation.isPending ? '批改中' : '提交補練'}
              </button>
            </div>
          )}

          {remediationStatus === 'completed' && (
            <div className='mt-4 rounded-lg border border-emerald-100 bg-white px-3 py-2 text-xs font-bold leading-relaxed text-emerald-800 nice-shadow'>
              已完成補練。這是學習輔助記錄，原作業分數仍以老師發布的作業結果為準。
            </div>
          )}
        </div>
      )}
      
      
      
      {assignments?.assignment_object?.description && (
        <div className='flex flex-col space-y-2 p-4 md:p-6 bg-slate-100/30 rounded-md nice-shadow'>
          <div className='flex flex-col space-y-3'>
            <div className='flex items-center gap-2 text-slate-700'>
              <Info size={16} className="text-slate-500" />
              <h3 className='text-sm font-semibold'>{t('assignments.assignment_description')}</h3>
            </div>
            <div className='pl-6'>
              <p className='text-sm leading-relaxed text-slate-600'>{assignments.assignment_object.description}</p>
            </div>
          </div>
        </div>
      )}

      {isAwaitingSubmission && hasTasks && (
        <div className='rounded-xl border border-cyan-100 bg-cyan-50/70 p-4 nice-shadow'>
          <div className='flex flex-col gap-3 md:flex-row md:items-center md:justify-between'>
            <div className='flex items-start gap-3'>
              <div className='mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white text-cyan-700 nice-shadow'>
                <BookOpenCheck size={18} />
              </div>
              <div>
                <p className='text-sm font-black text-slate-900'>
                  {t('assignments.student_flow_title')}
                </p>
                <p className='mt-1 text-xs leading-relaxed text-slate-600'>
                  {t('assignments.student_flow_description')}
                </p>
              </div>
            </div>
            <div className='space-y-2 md:min-w-[420px]'>
              <div className={`grid grid-cols-1 gap-2 text-[11px] font-bold text-slate-700 ${allowRetries ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
                <div className='flex items-center gap-2 rounded-lg bg-white px-3 py-2 nice-shadow'>
                  <Save size={13} className='text-emerald-600' />
                  <span>{t('assignments.student_flow_step_save')}</span>
                </div>
                <div className='flex items-center gap-2 rounded-lg bg-white px-3 py-2 nice-shadow'>
                  <BookOpenCheck size={13} className='text-cyan-700' />
                  <span>{t('assignments.student_flow_step_submit')}</span>
                </div>
                {allowRetries && (
                  <div className='flex items-center gap-2 rounded-lg bg-white px-3 py-2 nice-shadow'>
                    <RotateCcw size={13} className='text-fuchsia-600' />
                    <span>{t('assignments.student_flow_step_retry')}</span>
                  </div>
                )}
              </div>
              {retryLearningNote && (
                <div className='flex items-start gap-2 rounded-lg border border-cyan-100 bg-white/80 px-3 py-2 text-[11px] font-bold leading-relaxed text-cyan-900 nice-shadow'>
                  <RotateCcw size={13} className='mt-0.5 shrink-0 text-fuchsia-600' />
                  <span>{retryLearningNote}</span>
                </div>
              )}
              {showBestScoreBadge && (
                <div className='flex items-start gap-2 rounded-lg border border-emerald-100 bg-white/80 px-3 py-2 text-[11px] font-bold leading-relaxed text-emerald-800 nice-shadow'>
                  <CheckCircle2 size={13} className='mt-0.5 shrink-0 text-emerald-600' />
                  <span>
                    {t('assignments.best_score_retained', {
                      grade: bestGrade,
                      max: totalMaxGrade,
                    })}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {canShowAnswerProgress && (
        <div className='rounded-xl border border-slate-200 bg-white p-4 nice-shadow'>
          <div className='flex flex-col gap-3 md:flex-row md:items-center md:justify-between'>
            <div>
              <p className='text-sm font-black text-slate-900'>
                {t('assignments.student_progress_title', { defaultValue: '作答進度' })}
              </p>
              <p className='mt-1 text-xs font-semibold text-slate-500'>
                {completedAnswerCount === sortedTasks.length
                  ? t('assignments.student_progress_ready', { defaultValue: '全部題目已儲存，可以提交批改。' })
                  : t('assignments.student_progress_next', {
                      defaultValue: '下一題：{{task}}',
                      task: assignmentTaskDisplayName(
                        nextIncompleteAnswer?.task,
                        nextIncompleteAnswer?.index ?? 0,
                        t('assignments.task', { defaultValue: '題目' })
                      ),
                    })}
              </p>
              {completedAnswerCount < sortedTasks.length && nextIncompleteAnswer?.task?.assignment_task_uuid && (
                <button
                  type='button'
                  onClick={() => scrollToTask(nextIncompleteAnswer.task.assignment_task_uuid)}
                  className='mt-3 inline-flex h-8 items-center gap-2 rounded-lg bg-slate-900 px-3 text-xs font-black text-white nice-shadow hover:bg-black'
                >
                  <BookOpenCheck size={13} />
                  <span>
                    {t('assignments.student_progress_jump_next', {
                      defaultValue: '跳到下一題',
                    })}
                  </span>
                </button>
              )}
            </div>
            <div className='md:min-w-[260px]'>
              <div className='mb-2 flex items-center justify-between text-[11px] font-bold text-slate-600'>
                <span>{t('assignments.student_progress_completed', { defaultValue: '已完成' })}</span>
                <span className='tabular-nums'>{completedAnswerCount}/{sortedTasks.length}</span>
              </div>
              <div className='h-2 overflow-hidden rounded-full bg-slate-100'>
                <div
                  className={`h-full rounded-full transition-all ${
                    completedAnswerCount === sortedTasks.length ? 'bg-emerald-500' : 'bg-cyan-600'
                  }`}
                  style={{ width: `${Math.max(0, Math.min(100, answerProgressPercent))}%` }}
                />
              </div>
              {completedAnswerCount === sortedTasks.length && (
                <div className='mt-3 flex items-start gap-2 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] font-bold leading-relaxed text-emerald-800'>
                  <CheckCircle2 size={13} className='mt-0.5 shrink-0 text-emerald-600' />
                  <div className='min-w-0 flex-1'>
                    <span>
                      {t('assignments.student_progress_submit_next', {
                        defaultValue: '下一步：在作業工具列按「提交批改」，系統才會正式計分。',
                      })}
                    </span>
                    <button
                      type='button'
                      onClick={scrollToSubmitTools}
                      className='mt-2 inline-flex h-8 items-center gap-2 rounded-lg bg-white px-3 text-xs font-black text-emerald-800 nice-shadow hover:bg-emerald-100'
                    >
                      <BookOpenCheck size={13} />
                      <span>
                        {t('assignments.student_progress_go_submit', {
                          defaultValue: '前往提交批改',
                        })}
                      </span>
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {isSubmittedWaiting && hasTasks && (
        <div className='rounded-xl border border-cyan-100 bg-cyan-50/70 p-4 nice-shadow' aria-live='polite'>
          <div className='flex items-start gap-3'>
            <div className='mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white text-cyan-700 nice-shadow'>
              <CheckCircle2 size={18} />
            </div>
            <div>
              <p className='text-sm font-black text-slate-900'>
                {isLateSubmittedWaiting
                  ? t('assignments.submitted_late_waiting_title')
                  : t('assignments.submitted_waiting_title')}
              </p>
              <p className='mt-1 text-xs leading-relaxed text-slate-600'>
                {isAutoGradingAssignment
                  ? t('assignments.submitted_waiting_autograde_description')
                  : t('assignments.submitted_waiting_review_description')}
              </p>
            </div>
          </div>
        </div>
      )}
      
      
      {assignments && !hasTasks && (
        <div className='flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/70 px-5 py-10 text-center nice-shadow'>
          <div className='flex h-12 w-12 items-center justify-center rounded-full bg-white text-slate-500 nice-shadow'>
            <NotebookPen size={22} />
          </div>
          <p className='mt-4 text-sm font-black text-slate-800'>老師正在準備題目</p>
          <p className='mt-1 max-w-md text-xs leading-relaxed text-slate-500'>
            這份作業暫時未有題目。請稍後再回來，或向老師確認作業是否已發布完整。
          </p>
        </div>
      )}

      {assignments && sortedTasks.map((task: any, index: number) => {
        const taskSubmission = taskSubmissionsMap ? taskSubmissionsMap[task.assignment_task_uuid] : null;
        const taskGrade = taskSubmission?.grade ?? 0;
        const taskMax = task.max_grade_value || 0;
        const taskFeedback = displayTaskFeedback((taskSubmission?.task_submission_grade_feedback || '').trim());
        const taskPercentage = taskMax > 0 ? Math.round((taskGrade / taskMax) * 100) : 0;
        const taskPassed = taskPercentage >= passingThreshold;
        const hasHint = typeof task.hint === 'string' && task.hint.trim() !== '';
        const hasReferenceFile =
          typeof task.reference_file === 'string' &&
          task.reference_file.trim() !== '' &&
          task.reference_file !== 'null' &&
          task.reference_file !== 'undefined';

        return (
          <div
            id={`assignment-task-${task.assignment_task_uuid}`}
            className='scroll-mt-24 flex flex-col space-y-2'
            key={task.assignment_task_uuid}
          >
            <div className='flex flex-col md:flex-row md:justify-between py-2 space-y-2 md:space-y-0'>
              <div className='flex flex-wrap space-x-2 font-semibold text-slate-800'>
                <p>{t('assignments.task')} {index + 1} : </p>
                <p className='text-slate-500 break-words'>{task.description}</p>
              </div>
              <div className='flex flex-wrap gap-2'>
                {hasHint && (
                  <div
                    onClick={() => toast(task.hint, { icon: 'ℹ️' })}
                    className='px-3 py-1 flex items-center nice-shadow bg-amber-50/40 text-amber-900 rounded-full space-x-2 cursor-pointer'>
                    <Info size={13} />
                    <p className='text-xs font-semibold'>{t('assignments.hint')}</p>
                  </div>
                )}
                {hasReferenceFile && (
                  <Link
                    href={getTaskRefFileDir(
                      org?.org_uuid,
                      assignments?.course_object.course_uuid,
                      assignments?.activity_object.activity_uuid,
                      assignments?.assignment_object.assignment_uuid,
                      task.assignment_task_uuid,
                      task.reference_file
                    )}
                    target='_blank'
                    download={true}
                    className='px-3 py-1 flex items-center nice-shadow bg-cyan-50/40 text-cyan-900 rounded-full space-x-1 md:space-x-2 cursor-pointer'>
                    <Download size={13} />
                    <div className='flex items-center space-x-1 md:space-x-2'>
                      <span className='relative'>
                        <span className='absolute right-0 top-0 block h-2 w-2 rounded-full ring-2 ring-white bg-green-400'></span>
                      </span>
                      <p className='text-xs font-semibold'>{t('assignments.reference_document')}</p>
                    </div>
                  </Link>
                )}
              </div>
            </div>
            {isGraded && taskSubmission && (
              <div className={`relative overflow-hidden rounded-xl nice-shadow border ${
                taskPassed
                  ? 'bg-gradient-to-br from-emerald-50 via-teal-50 to-cyan-50 border-emerald-200/60'
                  : 'bg-gradient-to-br from-rose-50 via-orange-50 to-amber-50 border-rose-200/60'
              }`}>
                <div className={`absolute -top-10 -right-10 w-32 h-32 rounded-full blur-3xl opacity-40 ${
                  taskPassed ? 'bg-emerald-300' : 'bg-rose-300'
                }`} />
                <div className='relative p-4 flex flex-col gap-3'>
                  <div className='flex items-center justify-between gap-3'>
                    <div className='flex items-center gap-2.5'>
                      <div className={`w-9 h-9 rounded-full flex items-center justify-center bg-white nice-shadow ${
                        taskPassed ? 'text-emerald-600' : 'text-rose-600'
                      }`}>
                        {taskPassed ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
                      </div>
                      <div className='flex flex-col leading-tight'>
                        <span className={`text-[10px] font-bold uppercase tracking-[0.15em] ${
                          taskPassed ? 'text-emerald-700' : 'text-rose-700'
                        }`}>
                          {taskPassed ? t('assignments.task_passed') : t('assignments.task_not_passed')}
                        </span>
                        <span className='text-[11px] text-slate-500 font-medium'>
                          {taskPercentage}% {t('assignments.score')}
                        </span>
                      </div>
                    </div>
                    <div className='flex items-baseline gap-1 px-3 py-1.5 rounded-lg bg-white nice-shadow'>
                      <span className='text-xl font-black text-slate-900 leading-none tabular-nums'>{taskGrade}</span>
                      <span className='text-xs font-semibold text-slate-400 leading-none'>/ {taskMax}</span>
                    </div>
                  </div>
                  {/* Progress fill */}
                  <div className='h-1.5 w-full rounded-full bg-white/70 overflow-hidden'>
                    <div
                      className={`h-full rounded-full ${taskPassed ? 'bg-emerald-500' : 'bg-rose-500'}`}
                      style={{ width: `${Math.max(0, Math.min(100, taskPercentage))}%` }}
                    />
                  </div>
                  {taskFeedback && (
                    <div className='flex items-start gap-2 p-3 rounded-lg bg-white/70 border border-white'>
                      <MessageSquare size={13} className='shrink-0 mt-0.5 text-slate-400' />
                      <p className='text-xs text-slate-700 leading-relaxed whitespace-pre-wrap'>{taskFeedback}</p>
                    </div>
                  )}
                </div>
              </div>
            )}
            <div className='w-full'>
              {task.assignment_type === 'QUIZ' && <TaskQuizObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
              {task.assignment_type === 'FILE_SUBMISSION' && <TaskFileObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
              {task.assignment_type === 'FORM' && <TaskFormObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
              {task.assignment_type === 'CODE' && <TaskCodeObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
              {task.assignment_type === 'SHORT_ANSWER' && <TaskShortAnswerObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
              {task.assignment_type === 'NUMBER_ANSWER' && <TaskNumberAnswerObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
              {task.assignment_type === 'ESSAY' && <TaskEssayObject key={task.assignment_task_uuid} view='student' assignmentTaskUUID={task.assignment_task_uuid} />}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default AssignmentStudentActivity
