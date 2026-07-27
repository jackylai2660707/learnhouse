import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import { useAssignmentSubmission, useAssignmentTaskSubmissions } from '@components/Contexts/Assignments/AssignmentSubmissionContext';
import { useAssignmentsTask, useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI';
import { getAssignmentTask, getAssignmentTaskSubmissionsUser, handleAssignmentTaskSubmission, updateAssignmentTask } from '@services/courses/assignments';
import { Check, Info, Minus, Plus, PlusCircle, X } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { v4 as uuidv4 } from 'uuid';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { coerceSimplePilotBoolean } from '@lib/simple-pilot-assignments';

type QuizSchema = {
    questionText: string;
    questionUUID?: string;
    options: {
        optionUUID?: string;
        text: string;
        fileID: string;
        type: 'text' | 'image' | 'audio' | 'video';
        assigned_right_answer: boolean | string | number;
    }[];
};

type QuizSubmitSchema = {
    questions: QuizSchema[];
    submissions: {
        questionUUID: string;
        optionUUID: string;
        answer: boolean | string | number;
    }[];
    assignment_task_submission_uuid?: string;
};

type TaskQuizObjectProps = {
    view: 'teacher' | 'student' | 'grading';
    user_id?: string; // Only for read-only view
    assignmentTaskUUID?: string;
};

type Submission = {
    questionUUID: string;
    optionUUID: string;
    answer: boolean | string | number;
};

function responseErrorMessage(response: any, fallback: string) {
    const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    if (response instanceof Error && response.message) return response.message;
    if (typeof response === 'string' && response.trim()) return response;
    return fallback;
}

function isSimpleBooleanTrue(value: unknown) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value === 1;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        return ['true', '1', 'yes', 'y', 'correct', 'right', '是', '對', '正確'].includes(normalized);
    }
    return false;
}

function isCorrectOption(option: QuizSchema['options'][number]) {
    return isSimpleBooleanTrue(option.assigned_right_answer);
}

function isSelectedSubmission(submission?: Submission) {
    return isSimpleBooleanTrue(submission?.answer);
}

function gradeQuizSubmissions(questions: QuizSchema[], submissions: Submission[], maxPoints: number) {
    let totalUnits = 0;
    let correctUnits = 0;

    questions.forEach((question) => {
        const correctOptionCount = question.options.filter(isCorrectOption).length;

        if (question.questionUUID && question.options.length > 0 && correctOptionCount === 1) {
            totalUnits++;
            let selectedCorrect = false;
            let selectedWrong = false;

            question.options.forEach((option) => {
                const submission = submissions.find(
                    (sub) => sub.questionUUID === question.questionUUID && sub.optionUUID === option.optionUUID
                );
                if (isCorrectOption(option) && isSelectedSubmission(submission)) selectedCorrect = true;
                if (!isCorrectOption(option) && isSelectedSubmission(submission)) selectedWrong = true;
            });

            if (selectedCorrect && !selectedWrong) correctUnits++;
            return;
        }

        question.options.forEach((option) => {
            totalUnits++;
            const submission = submissions.find(
                (sub) => sub.questionUUID === question.questionUUID && sub.optionUUID === option.optionUUID
            );
            if (isSelectedSubmission(submission) === isCorrectOption(option)) {
                correctUnits++;
            }
        });
    });

    return totalUnits > 0 ? Math.round((correctUnits / totalUnits) * maxPoints) : 0;
}

function isSingleAnswerQuestion(question: QuizSchema) {
    return question.options.filter(isCorrectOption).length === 1;
}

function TaskQuizObject({ view, assignmentTaskUUID, user_id }: TaskQuizObjectProps) {
    const { t } = useTranslation()
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;
    const assignmentTaskState = useAssignmentsTask() as any;
    const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any;
    const assignment = useAssignments() as any;
    const taskSubmissionsMap = useAssignmentTaskSubmissions();
    const queryClient = useQueryClient();
    // Reveal correct answers to the student only after the submission is
    // GRADED AND the teacher opted into it on the assignment. Before grading
    // we still hide the answer key (the assignment hasn't been evaluated yet)
    // and on opt-out we never reveal, so the student sees only their own
    // choices + score.
    const assignmentSubmission = useAssignmentSubmission() as any;
    const assignmentSubmissionStatus = Array.isArray(assignmentSubmission) && assignmentSubmission.length > 0
        ? assignmentSubmission[0].submission_status
        : null;
    const submissionIsFinal = view === 'student'
        && !!assignmentSubmissionStatus
        && !['PENDING', 'NOT_SUBMITTED'].includes(assignmentSubmissionStatus);
    const submissionIsGraded = Array.isArray(assignmentSubmission)
        && assignmentSubmission.length > 0
        && assignmentSubmissionStatus === 'GRADED';
    const showCorrectAnswers = view === 'student'
        && submissionIsGraded
        && coerceSimplePilotBoolean(assignment?.assignment_object?.show_correct_answers);


    /* TEACHER VIEW CODE */
    const [questions, setQuestions] = useState<QuizSchema[]>([
        { questionText: '', questionUUID: 'question_' + uuidv4(), options: [{ text: '', fileID: '', type: 'text', assigned_right_answer: false, optionUUID: 'option_' + uuidv4() }] },
    ]);

    const handleQuestionChange = (index: number, value: string) => {
        const updatedQuestions = [...questions];
        updatedQuestions[index].questionText = value;
        setQuestions(updatedQuestions);
    };

    const handleOptionChange = (qIndex: number, oIndex: number, value: string) => {
        const updatedQuestions = [...questions];
        updatedQuestions[qIndex].options[oIndex].text = value;
        setQuestions(updatedQuestions);
    };

    const addOption = (qIndex: number) => {
        const updatedQuestions = [...questions];
        updatedQuestions[qIndex].options.push({ text: '', fileID: '', type: 'text', assigned_right_answer: false, optionUUID: 'option_' + uuidv4() });
        setQuestions(updatedQuestions);
    };

    const removeOption = (qIndex: number, oIndex: number) => {
        const updatedQuestions = [...questions];
        if (updatedQuestions[qIndex].options.length > 1) {
            updatedQuestions[qIndex].options.splice(oIndex, 1);
            setQuestions(updatedQuestions);
        } else {
            toast.error('至少需要保留一個選項。');
        }
    };

    const addQuestion = () => {
        setQuestions([...questions, { questionText: '', questionUUID: 'question_' + uuidv4(), options: [{ text: '', fileID: '', type: 'text', assigned_right_answer: false, optionUUID: 'option_' + uuidv4() }] }]);
    };

    const removeQuestion = (qIndex: number) => {
        const updatedQuestions = [...questions];
        updatedQuestions.splice(qIndex, 1);
        setQuestions(updatedQuestions);
    };

    const toggleOption = (qIndex: number, oIndex: number) => {
        const updatedQuestions = [...questions];
        // Find the option to toggle
        const optionToToggle = updatedQuestions[qIndex].options[oIndex];
        // Toggle the 'correct' property of the option
        optionToToggle.assigned_right_answer = !isCorrectOption(optionToToggle);
        setQuestions(updatedQuestions);
    };

    const saveFC = async () => {
        // Save the quiz to the server
        const values = {
            contents: {
                questions,
            },
        };
        try {
            const res = await updateAssignmentTask(values, assignmentTaskState.assignmentTask.assignment_task_uuid, assignment.assignment_object.assignment_uuid, access_token);
            if (res.success) {
                assignmentTaskStateHook({
                    type: 'reload',
                });
                queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() });
                toast.success(t('dashboard.assignments.editor.toasts.task_saved'));
            } else {
                toast.error(responseErrorMessage(res, t('dashboard.assignments.editor.toasts.task_save_error')));
            }
        } catch (error) {
            toast.error(responseErrorMessage(error, t('dashboard.assignments.editor.toasts.task_save_error')));
        }
    };
    /* TEACHER VIEW CODE */

    /* STUDENT VIEW CODE */
    const [userSubmissions, setUserSubmissions] = useState<QuizSubmitSchema>({
        questions: [],
        submissions: [],
    });
    const [initialUserSubmissions, setInitialUserSubmissions] = useState<QuizSubmitSchema>({
        questions: [],
        submissions: [],
    });
    const [showSavingDisclaimer, setShowSavingDisclaimer] = useState<boolean>(false);
    const [assignmentTaskOutsideProvider, setAssignmentTaskOutsideProvider] = useState<any>(null);

    async function chooseOption(qIndex: number, oIndex: number) {
        const updatedSubmissions = [...userSubmissions.submissions];
        const question = questions[qIndex];
        const option = question?.options[oIndex];

        if (!question || !option) return;

        const questionUUID = question.questionUUID;
        const optionUUID = option.optionUUID;

        if (!questionUUID || !optionUUID) return;

        if (isSingleAnswerQuestion(question)) {
            const otherQuestionSubmissions = updatedSubmissions.filter(
                (submission) => submission.questionUUID !== questionUUID
            );
            const selectedQuestionSubmissions = question.options
                .filter((questionOption) => !!questionOption.optionUUID)
                .map((questionOption) => ({
                    questionUUID,
                    optionUUID: questionOption.optionUUID as string,
                    answer: questionOption.optionUUID === optionUUID,
                }));

            setUserSubmissions({
                ...userSubmissions,
                submissions: [...otherQuestionSubmissions, ...selectedQuestionSubmissions],
            });
            return;
        }

        const submissionIndex = updatedSubmissions.findIndex(
            (submission) => submission.questionUUID === questionUUID && submission.optionUUID === optionUUID
        );

        if (submissionIndex === -1) {
            updatedSubmissions.push({ questionUUID, optionUUID, answer: true });
        } else {
            updatedSubmissions[submissionIndex].answer = !updatedSubmissions[submissionIndex].answer;
        }

        setUserSubmissions({
            ...userSubmissions,
            submissions: updatedSubmissions,
        });
    }

    // Used only by grading view — student view hydrates from useAssignments() context
    async function getAssignmentTaskUI() {
        if (assignmentTaskUUID) {
            const res = await getAssignmentTask(assignmentTaskUUID, access_token);
            if (res.success) {
                setAssignmentTaskOutsideProvider(res.data);
                setQuestions(res.data.contents.questions);
            }

        }
    }

    function hydrateTaskFromContext() {
        if (!assignmentTaskUUID) return;
        const task = assignment?.assignment_tasks?.find(
            (t: any) => t.assignment_task_uuid === assignmentTaskUUID
        );
        if (task) {
            setAssignmentTaskOutsideProvider(task);
            if (task.contents?.questions) {
                setQuestions(task.contents.questions);
            }
        }
    }

    function hydrateSubmissionFromBatch() {
        if (!assignmentTaskUUID) return;
        if (taskSubmissionsMap === null) return;
        const sub = taskSubmissionsMap?.[assignmentTaskUUID] ?? null;
        if (sub) {
            setUserSubmissions({
                ...sub.task_submission,
                assignment_task_submission_uuid: sub.assignment_task_submission_uuid,
            });
            setInitialUserSubmissions({
                ...sub.task_submission,
                assignment_task_submission_uuid: sub.assignment_task_submission_uuid,
            });
        } else {
            const emptySubmission = {
                questions: [],
                submissions: [],
            };
            setUserSubmissions(emptySubmission);
            setInitialUserSubmissions(emptySubmission);
        }
    }

    // Detect changes between initial and current submissions
    useEffect(() => {
        const hasChanges = JSON.stringify(initialUserSubmissions.submissions) !== JSON.stringify(userSubmissions.submissions);
        setShowSavingDisclaimer(hasChanges);
    }, [userSubmissions, initialUserSubmissions.submissions]);



    const submitFC = async () => {
        if (submissionIsFinal) {
            toast.error('這份作業已提交，請按「重做」後再修改答案。');
            return;
        }
        const hasMissingSelection = questions.some((question) => {
            if (!question.questionUUID || !Array.isArray(question.options) || question.options.length === 0) return true;
            return !question.options.some((option) => {
                const submission = userSubmissions.submissions.find(
                    (item) => item.questionUUID === question.questionUUID && item.optionUUID === option.optionUUID
                );
                return isSelectedSubmission(submission);
            });
        });
        if (hasMissingSelection) {
            toast.error(t('assignments.save_quiz_select_option_first', {
                defaultValue: '請先選擇答案，再儲存本題。',
            }));
            return;
        }
        // Ensure all questions and options have submissions
        const updatedSubmissions: Submission[] = questions.flatMap(question => {
            return question.options.map(option => {
                const existingSubmission = userSubmissions.submissions.find(
                    submission => submission.questionUUID === question.questionUUID && submission.optionUUID === option.optionUUID
                );
                
                return existingSubmission || {
                    questionUUID: question.questionUUID || '',
                    optionUUID: option.optionUUID || '',
                    answer: false // Mark unsubmitted options as false
                };
            });
        });

        // Update userSubmissions with the complete set of submissions
        const updatedUserSubmissions: QuizSubmitSchema = {
            ...userSubmissions,
            submissions: updatedSubmissions
        };

        // Save the quiz to the server
        const values = {
            assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid || null,
            task_submission: updatedUserSubmissions,
            grade: 0,
            task_submission_grade_feedback: '',
        };

        if (assignmentTaskUUID) {
            try {
                const res = await handleAssignmentTaskSubmission(values, assignmentTaskUUID, assignment.assignment_object.assignment_uuid, access_token);
                if (res.success) {
                    assignmentTaskStateHook({
                        type: 'reload',
                    });
                    toast.success(t('assignments.task_answer_saved_not_submitted'));
                    setShowSavingDisclaimer(false);
                    // Update userSubmissions with the returned UUID for future updates
                    const updatedUserSubmissionsWithUUID = {
                        ...updatedUserSubmissions,
                        assignment_task_submission_uuid: res.data?.assignment_task_submission_uuid || userSubmissions.assignment_task_submission_uuid
                    };
                    setUserSubmissions(updatedUserSubmissionsWithUUID);
                    setInitialUserSubmissions(updatedUserSubmissionsWithUUID);
                    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid) });
                } else {
                    toast.error(responseErrorMessage(res, t('dashboard.assignments.editor.toasts.task_save_error')));
                }
            } catch (error) {
                toast.error(responseErrorMessage(error, t('dashboard.assignments.editor.toasts.task_save_error')));
            }
        }
    };

    /* STUDENT VIEW CODE */

    /* GRADING VIEW CODE */
    const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null);
    async function getAssignmentTaskSubmissionFromIdentifiedUserUI() {
        if (assignmentTaskUUID && user_id) {
            const res = await getAssignmentTaskSubmissionsUser(assignmentTaskUUID, user_id, assignment.assignment_object.assignment_uuid, access_token);
            if (res.success) {
                setUserSubmissions({
                    ...res.data.task_submission,
                    assignment_task_submission_uuid: res.data.assignment_task_submission_uuid
                });
                setUserSubmissionObject(res.data);
                setInitialUserSubmissions({
                    ...res.data.task_submission,
                    assignment_task_submission_uuid: res.data.assignment_task_submission_uuid
                });
            }

        }
    }

    async function gradeFC() {
        if (assignmentTaskUUID) {
            const maxPoints = assignmentTaskOutsideProvider?.max_grade_value || 100;
            const finalGrade = gradeQuizSubmissions(questions, userSubmissions.submissions, maxPoints);

            // Save the grade to the server
            const values = {
                assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid,
                task_submission: userSubmissions,
                grade: finalGrade,
                task_submission_grade_feedback: '系統自動批改',
            };

            try {
                const res = await handleAssignmentTaskSubmission(values, assignmentTaskUUID, assignment.assignment_object.assignment_uuid, access_token);
                if (res.success) {
                    getAssignmentTaskSubmissionFromIdentifiedUserUI();
                    toast.success(`已自動批改：${finalGrade} 分`);
                } else {
                    toast.error(responseErrorMessage(res, '批改失敗，請稍後再試。'));
                }
            } catch (error) {
                toast.error(responseErrorMessage(error, '批改失敗，請稍後再試。'));
            }
        }
    }



    /* GRADING VIEW CODE */

    useEffect(() => {
        assignmentTaskStateHook({
            setSelectedAssignmentTaskUUID: assignmentTaskUUID,
        });
        // Teacher area
        if (view == 'teacher' && assignmentTaskState.assignmentTask.contents?.questions) {
            setQuestions(assignmentTaskState.assignmentTask.contents.questions);
        }
        // Student area: hydrate from already-fetched context payloads.
        else if (view == 'student') {
            hydrateTaskFromContext();
            hydrateSubmissionFromBatch();
        }

        // Grading area: per-task fetches are fine here (one task at a time).
        else if (view == 'grading') {
            getAssignmentTaskUI();
            getAssignmentTaskSubmissionFromIdentifiedUserUI();

        }
    }, [assignmentTaskState, assignment, assignmentTaskStateHook, access_token, taskSubmissionsMap]);

    if (questions && questions.length >= 0) {
        return (
            <AssignmentBoxUI submitFC={submitFC} saveFC={saveFC} gradeFC={gradeFC} view={view} currentPoints={userSubmissionObject?.grade} maxPoints={assignmentTaskOutsideProvider?.max_grade_value} showSavingDisclaimer={showSavingDisclaimer} type="quiz" autoGradable={true}>
                <div className="flex flex-col space-y-6">
                    {questions && questions.map((question, qIndex) => (
                        <div key={qIndex} className="flex flex-col space-y-1.5">
                            <div className="flex space-x-2 items-center">
                                {view === 'teacher' ? (
                                    <input
                                        value={question.questionText}
                                        onChange={(e) => handleQuestionChange(qIndex, e.target.value)}
                                        placeholder="輸入題目"
                                        className="w-full px-3 text-neutral-600 bg-[#00008b00] border-2 border-gray-200 rounded-md border-dotted text-sm font-bold"
                                    />
                                ) : (
                                    <p className="w-full px-3 text-neutral-600 bg-[#00008b00] border-2 border-gray-200 rounded-md border-dotted text-sm font-bold">
                                        {question.questionText}
                                    </p>
                                )}
                                {view === 'teacher' && (
                                    <div
                                        className="w-[20px] flex-none flex items-center h-[20px] rounded-lg bg-slate-200/60 text-slate-500 hover:bg-slate-300 text-sm transition-all ease-linear cursor-pointer"
                                        onClick={() => removeQuestion(qIndex)}
                                    >
                                        <Minus size={12} className="mx-auto" />
                                    </div>
                                )}
                            </div>
                            <div className="flex flex-col space-y-2">
                                {question.options.map((option, oIndex) => (
                                    <div className="flex" key={oIndex}>
                                        <div
                                            onClick={() => view === 'student' && !submissionIsFinal && chooseOption(qIndex, oIndex)}
                                            onKeyDown={(event) => {
                                                if (
                                                    view === 'student'
                                                    && !submissionIsFinal
                                                    && (event.key === 'Enter' || event.key === ' ')
                                                ) {
                                                    event.preventDefault();
                                                    chooseOption(qIndex, oIndex);
                                                }
                                            }}
                                            role={view === 'student' ? 'button' : undefined}
                                            tabIndex={view === 'student' && !submissionIsFinal ? 0 : undefined}
                                            aria-disabled={view === 'student' ? submissionIsFinal : undefined}
                                            aria-label={view === 'student' ? `${String.fromCharCode(65 + oIndex)}：${option.text}` : undefined}
                                            aria-pressed={view === 'student' ? userSubmissions.submissions.some(
                                                (submission) =>
                                                    submission.questionUUID === question.questionUUID
                                                    && submission.optionUUID === option.optionUUID
                                                    && isSelectedSubmission(submission)
                                            ) : undefined}
                                            className={"answer outline outline-3 outline-white pr-2 shadow-sm w-full flex items-center space-x-2 h-[30px] hover:bg-opacity-100 hover:shadow-md rounded-lg bg-white text-sm duration-150 ease-linear nice-shadow " + (view == 'student' && !submissionIsFinal ? 'cursor-pointer active:scale-110' : '')}
                                        >
                                            <div className="font-bold text-base flex items-center h-full w-[40px] rounded-l-md text-slate-800 bg-slate-100/80">
                                                <p className="mx-auto font-bold text-sm">{String.fromCharCode(65 + oIndex)}</p>
                                            </div>
                                            {view === 'teacher' ? (
                                                <input
                                                    type="text"
                                                    value={option.text}
                                                    onChange={(e) => handleOptionChange(qIndex, oIndex, e.target.value)}
                                                    placeholder="選項"
                                                    className="w-full mx-2 px-3 pr-6 text-neutral-600 bg-[#00008b00] border-2 border-gray-200 rounded-md border-dotted text-sm font-bold"
                                                />
                                            ) : (
                                                <p className="w-full mx-2 px-3 pr-6 text-neutral-600 bg-[#00008b00] text-sm font-bold">
                                                    {option.text}
                                                </p>
                                            )}
                                            {view === 'teacher' && (
                                                <>
                                                    <div
                                                        className={`w-fit flex-none flex text-xs px-2 py-0.5 space-x-1 items-center h-fit rounded-lg ${isCorrectOption(option) ? 'bg-lime-200 text-lime-600' : 'bg-rose-200/60 text-rose-500'
                                                            } hover:bg-lime-300 text-sm transition-all ease-linear cursor-pointer`}
                                                        onClick={() => toggleOption(qIndex, oIndex)}
                                                    >
                                                        {isCorrectOption(option) ? <Check size={12} className="mx-auto" /> : <X size={12} className="mx-auto" />}
                                                        {isCorrectOption(option) ? (
                                                            <p className="mx-auto font-bold text-xs">正確</p>
                                                        ) : (
                                                            <p className="mx-auto font-bold text-xs">錯誤</p>
                                                        )}
                                                    </div>
                                                    <div
                                                        className="w-[20px] flex-none flex items-center h-[20px] rounded-lg bg-slate-200/60 text-slate-500 hover:bg-slate-300 text-sm transition-all ease-linear cursor-pointer"
                                                        onClick={() => removeOption(qIndex, oIndex)}
                                                    >
                                                        <Minus size={12} className="mx-auto" />
                                                    </div>
                                                </>
                                            )}
                                            {view === 'grading' && (
                                                <>
                                                    <div
                                                        className={`w-fit flex-none flex text-xs px-2 py-0.5 space-x-1 items-center h-fit rounded-lg ${isCorrectOption(option) ? 'bg-lime-200 text-lime-600' : 'bg-rose-200/60 text-rose-500'
                                                            } hover:bg-lime-300 text-sm transition-all ease-linear cursor-pointer`}
                                                    >
                                                        {isCorrectOption(option) ? <Check size={12} className="mx-auto" /> : <X size={12} className="mx-auto" />}
                                                        {isCorrectOption(option) ? (
                                                            <p className="mx-auto font-bold text-xs">標記為正確</p>
                                                        ) : (
                                                            <p className="mx-auto font-bold text-xs">標記為錯誤</p>
                                                        )}
                                                    </div>

                                                </>
                                            )}
                                            {view === 'student' && showCorrectAnswers && (
                                                <div className={`w-fit flex-none flex text-[10px] px-2 py-0.5 space-x-1 items-center h-fit rounded-lg ${
                                                    isCorrectOption(option)
                                                        ? 'bg-emerald-50 text-emerald-700'
                                                        : 'bg-rose-50 text-rose-600'
                                                }`}>
                                                    {isCorrectOption(option) ? <Check size={10} /> : <X size={10} />}
                                                    <p className='font-bold'>
                                                        {isCorrectOption(option)
                                                            ? t('assignments.quiz.correct_answer')
                                                            : t('assignments.quiz.incorrect_answer')}
                                                    </p>
                                                </div>
                                            )}
                                            {view === 'student' && (
                                                <div
                                                    className={`w-[20px] flex-none flex items-center h-[20px] rounded-lg ${
                                                        userSubmissions.submissions.find(
                                                            (submission) =>
                                                                submission.questionUUID === question.questionUUID &&
                                                                submission.optionUUID === option.optionUUID &&
                                                                isSelectedSubmission(submission)
                                                        )
                                                            ? "bg-green-200/60 text-green-500 hover:bg-green-300"
                                                            : "bg-slate-200/60 text-slate-500 hover:bg-slate-300"
                                                    } text-sm transition-all ease-linear ${submissionIsFinal ? '' : 'cursor-pointer'}`}
                                                >
                                                    {userSubmissions.submissions.find(
                                                        (submission) =>
                                                            submission.questionUUID === question.questionUUID &&
                                                            submission.optionUUID === option.optionUUID &&
                                                            isSelectedSubmission(submission)
                                                    ) ? (
                                                        <Check size={12} className="mx-auto" />
                                                    ) : (
                                                        <X size={12} className="mx-auto" />
                                                    )}
                                                </div>
                                            )}
                                            {view === 'grading' && (
                                                <>
                                                   
                                                    <div className={`w-[20px] flex-none flex items-center h-[20px] rounded-lg ${
                                                        userSubmissions.submissions.find(
                                                            (submission) =>
                                                                submission.questionUUID === question.questionUUID &&
                                                                submission.optionUUID === option.optionUUID &&
                                                                isSelectedSubmission(submission)
                                                        )
                                                            ? "bg-green-200/60 text-green-500"
                                                            : "bg-slate-200/60 text-slate-500"
                                                    } text-sm`}>
                                                        {userSubmissions.submissions.find(
                                                        (submission) =>
                                                            submission.questionUUID === question.questionUUID &&
                                                            submission.optionUUID === option.optionUUID &&
                                                            isSelectedSubmission(submission)
                                                    ) ? (
                                                        <Check size={12} className="mx-auto" />
                                                    ) : (
                                                            <X size={12} className="mx-auto" />
                                                        )}
                                                    </div>
                                                </>
                                            )}

                                        </div>
                                        {view === 'teacher' && oIndex === question.options.length - 1 && questions[qIndex].options.length <= 4 && (
                                            <div className="flex justify-center mx-auto px-2">
                                                <div
                                                    className="outline text-xs outline-3 outline-white px-2 shadow-sm w-full flex items-center h-[30px] hover:bg-opacity-100 hover:shadow-md rounded-lg bg-white duration-150 cursor-pointer ease-linear nice-shadow"
                                                    onClick={() => addOption(qIndex)}
                                                >
                                                    <Plus size={14} className="inline-block" />
                                                    <span></span>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
                {view === 'teacher' && questions.length <= 5 && (
                    <div className="flex justify-center mx-auto px-2">
                        <div
                            className="flex w-full my-2 py-2 px-4 bg-white text-slate text-xs rounded-md nice-shadow hover:shadow-xs cursor-pointer space-x-3 items-center transition duration-150 ease-linear"
                            onClick={addQuestion}
                        >
                            <PlusCircle size={14} className="inline-block" />
                            <span>新增題目</span>
                        </div>
                    </div>
                )}
            </AssignmentBoxUI>
        );
    }
    else {
        return <div className='flex flex-row space-x-2 text-sm items-center'>
            <Info size={12} />
            <p>暫時沒有題目</p>
        </div>;
    }
}

export default TaskQuizObject;
