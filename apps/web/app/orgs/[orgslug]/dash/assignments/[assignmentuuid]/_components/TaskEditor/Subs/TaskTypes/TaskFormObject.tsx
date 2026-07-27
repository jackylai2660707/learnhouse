import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import { useAssignmentSubmission, useAssignmentTaskSubmissions } from '@components/Contexts/Assignments/AssignmentSubmissionContext';
import { useAssignmentsTask, useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI';
import { getAssignmentTask, getAssignmentTaskSubmissionsUser, handleAssignmentTaskSubmission, updateAssignmentTask } from '@services/courses/assignments';
import { Check, Info, Minus, Plus, PlusCircle, X, Type } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { v4 as uuidv4 } from 'uuid';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { coerceSimplePilotBoolean } from '@lib/simple-pilot-assignments';

type FormSchema = {
    questionText: string;
    questionUUID?: string;
    blanks: {
        blankUUID?: string;
        placeholder: string;
        correctAnswer: string;
        correct_answer?: string;
        answer?: string;
        hint?: string;
    }[];
};

type FormSubmitSchema = {
    questions: FormSchema[];
    submissions: {
        questionUUID: string;
        blankUUID: string;
        answer: string;
    }[];
    assignment_task_submission_uuid?: string;
};

type TaskFormObjectProps = {
    view: 'teacher' | 'student' | 'grading';
    assignmentTaskUUID: string;
    user_id?: string;
};

function responseErrorMessage(response: any, fallback: string) {
    const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    if (response instanceof Error && response.message) return response.message;
    if (typeof response === 'string' && response.trim()) return response;
    return fallback;
}

const SIMPLE_TEXT_EDGE_PUNCTUATION = "\"'`.,;:!?()[]{}<>，。；：！？、（）【】《》「」『』“”‘’／/｜|～~";
const SIMPLE_TEXT_SIMPLIFIED_TO_TRADITIONAL: Record<string, string> = {
    // Small classroom fallback, not a full OpenCC conversion. It keeps teacher
    // previews aligned with the backend for common Macau school answers.
    门: '門',
    国: '國',
    语: '語',
    学: '學',
    习: '習',
    数: '數',
    课: '課',
    题: '題',
    问: '問',
    认: '認',
    识: '識',
    义: '義',
    词: '詞',
    组: '組',
    级: '級',
    读: '讀',
    写: '寫',
    听: '聽',
    说: '說',
    书: '書',
    简: '簡',
    单: '單',
    对: '對',
    错: '錯',
    体: '體',
    会: '會',
    复: '復',
    线: '線',
    点: '點',
    电: '電',
    话: '話',
    车: '車',
    马: '馬',
    鱼: '魚',
    鸟: '鳥',
    风: '風',
    云: '雲',
    气: '氣',
    节: '節',
    时: '時',
    间: '間',
    长: '長',
    历: '歷',
    汉: '漢',
    热: '熱',
    现: '現',
    这: '這',
    个: '個',
    们: '們',
    为: '為',
    与: '與',
    产: '產',
    业: '業',
    观: '觀',
    实: '實',
    验: '驗',
    试: '試',
    证: '證',
    园: '園',
    区: '區',
    东: '東',
    广: '廣',
    湾: '灣',
    岛: '島',
    桥: '橋',
    际: '際',
    币: '幣',
    纪: '紀',
    录: '錄',
    统: '統',
    计: '計',
    画: '畫',
    图: '圖',
    颜: '顏',
    关: '關',
    键: '鍵',
    练: '練',
    诗: '詩',
    乐: '樂',
    艺: '藝',
    术: '術',
    卫: '衛',
    护: '護',
    强: '強',
    难: '難',
    双: '雙',
    页: '頁',
    类: '類',
    别: '別',
    动: '動',
    静: '靜',
    声: '聲',
    圆: '圓',
    边: '邊',
    积: '積',
    标: '標',
    维: '維',
};

function trimSimpleEdgePunctuation(text: string) {
    let start = 0;
    let end = text.length;
    while (start < end && SIMPLE_TEXT_EDGE_PUNCTUATION.includes(text[start])) start++;
    while (end > start && SIMPLE_TEXT_EDGE_PUNCTUATION.includes(text[end - 1])) end--;
    return text.slice(start, end);
}

function normalizeSimpleTextAnswer(value: unknown) {
    const normalized = String(value ?? '')
        .normalize('NFKC')
        .split('')
        .map((char) => SIMPLE_TEXT_SIMPLIFIED_TO_TRADITIONAL[char] ?? char)
        .join('')
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
    return trimSimpleEdgePunctuation(normalized)
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/([\u3400-\u9fff])\s+(?=[\u3400-\u9fff])/g, '$1')
        .toLocaleLowerCase();
}

function isCorrectBlankAnswer(studentAnswer: unknown, correctAnswer: unknown) {
    const normalizedStudentAnswer = normalizeSimpleTextAnswer(studentAnswer);
    const normalizedCorrectAnswer = normalizeSimpleTextAnswer(correctAnswer);
    return !!normalizedStudentAnswer && !!normalizedCorrectAnswer && normalizedStudentAnswer === normalizedCorrectAnswer;
}

function getBlankCorrectAnswer(blank: FormSchema['blanks'][number]) {
    return blank.correctAnswer ?? blank.correct_answer ?? blank.answer ?? '';
}

function TaskFormObject({ view, assignmentTaskUUID, user_id }: TaskFormObjectProps) {
    const { t } = useTranslation()
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;
    const assignmentTaskState = useAssignmentsTask() as any;
    const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any;
    const assignment = useAssignments() as any;
    const taskSubmissionsMap = useAssignmentTaskSubmissions();
    const queryClient = useQueryClient();
    // Reveal correct answers only after the submission is GRADED AND the
    // teacher opted in on the assignment. See TaskQuizObject for the same
    // pattern — keep these consistent across task types.
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

    // Anti-paste guard for student inputs. Only active when the teacher
    // enabled anti_copy_paste on the assignment AND we're in the student view.
    const antiPasteEnabled =
        view === 'student' && !!assignment?.assignment_object?.anti_copy_paste;
    const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
        if (!antiPasteEnabled) return;
        e.preventDefault();
        toast.error(t('dashboard.assignments.editor.task_editor.general.paste_blocked'));
    };

    /* TEACHER VIEW CODE */
    const [questions, setQuestions] = useState<FormSchema[]>(
        view === 'teacher' ? [
            { 
                questionText: '', 
                questionUUID: 'question_' + uuidv4(), 
                blanks: [{ 
                    placeholder: '填寫答案',
                    correctAnswer: '', 
                    hint: '',
                    blankUUID: 'blank_' + uuidv4() 
                }] 
            },
        ] : []
    );

    const handleQuestionChange = (index: number, value: string) => {
        const updatedQuestions = [...questions];
        updatedQuestions[index].questionText = value;
        setQuestions(updatedQuestions);
    };

    const handleBlankChange = (qIndex: number, bIndex: number, field: 'placeholder' | 'correctAnswer' | 'hint', value: string) => {
        const updatedQuestions = [...questions];
        updatedQuestions[qIndex].blanks[bIndex][field] = value;
        setQuestions(updatedQuestions);
    };

    const addBlank = (qIndex: number) => {
        const updatedQuestions = [...questions];
        updatedQuestions[qIndex].blanks.push({ 
            placeholder: '填寫答案',
            correctAnswer: '', 
            hint: '',
            blankUUID: 'blank_' + uuidv4() 
        });
        setQuestions(updatedQuestions);
    };

    const removeBlank = (qIndex: number, bIndex: number) => {
        const updatedQuestions = [...questions];
        if (updatedQuestions[qIndex].blanks.length > 1) {
            updatedQuestions[qIndex].blanks.splice(bIndex, 1);
            setQuestions(updatedQuestions);
        } else {
            toast.error('至少需要保留一個填空。');
        }
    };

    const addQuestion = () => {
        setQuestions([...questions, { 
            questionText: '', 
            questionUUID: 'question_' + uuidv4(), 
            blanks: [{ 
                placeholder: '填寫答案',
                correctAnswer: '', 
                hint: '',
                blankUUID: 'blank_' + uuidv4() 
            }] 
        }]);
    };

    const removeQuestion = (qIndex: number) => {
        const updatedQuestions = [...questions];
        updatedQuestions.splice(qIndex, 1);
        setQuestions(updatedQuestions);
    };

    const saveFC = async () => {
        // Save the form to the server
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

    /* STUDENT VIEW CODE */
    const [userSubmissions, setUserSubmissions] = useState<FormSubmitSchema>({
        questions: [],
        submissions: [],
    });
    const [initialUserSubmissions, setInitialUserSubmissions] = useState<FormSubmitSchema>({
        questions: [],
        submissions: [],
    });
    const [showSavingDisclaimer, setShowSavingDisclaimer] = useState<boolean>(false);
    const [assignmentTaskOutsideProvider, setAssignmentTaskOutsideProvider] = useState<any>(null);
    const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null);

    const handleUserAnswerChange = (questionUUID: string, blankUUID: string, answer: string) => {
        const updatedSubmissions = [...userSubmissions.submissions];
        const existingIndex = updatedSubmissions.findIndex(
            (submission) => submission.questionUUID === questionUUID && submission.blankUUID === blankUUID
        );

        if (existingIndex !== -1) {
            updatedSubmissions[existingIndex].answer = answer;
        } else {
            updatedSubmissions.push({
                questionUUID,
                blankUUID,
                answer,
            });
        }

        setUserSubmissions({
            ...userSubmissions,
            submissions: updatedSubmissions,
        });
    };

    const handleUserAnswerBlur = (questionUUID: string, blankUUID: string, answer: string) => {
        // Auto-focus next blank only when user leaves the current input and it has content
        if (answer.trim() && view === 'student') {
            const allBlanks = questions.flatMap(q => q.blanks.map(b => ({ questionUUID: q.questionUUID, blankUUID: b.blankUUID })));
            const currentIndex = allBlanks.findIndex(b => b.questionUUID === questionUUID && b.blankUUID === blankUUID);
            const nextBlank = allBlanks[currentIndex + 1];
            
            if (nextBlank) {
                setTimeout(() => {
                    const nextInput = document.querySelector(`[data-blank-id="${nextBlank.blankUUID}"]`) as HTMLInputElement;
                    if (nextInput && !nextInput.value.trim()) {
                        nextInput.focus();
                    }
                }, 100);
            }
        }
    };

    const submitFC = async () => {
        if (submissionIsFinal) {
            toast.error('這份作業已提交，請按「重做」後再修改答案。');
            return;
        }
        const hasMissingBlankAnswer = questions.some((question) => {
            if (!question.questionUUID || !Array.isArray(question.blanks) || question.blanks.length === 0) return true;
            return question.blanks.some((blank) => {
                const submission = userSubmissions.submissions.find(
                    (item) => item.questionUUID === question.questionUUID && item.blankUUID === blank.blankUUID
                );
                return !String(submission?.answer ?? '').trim();
            });
        });
        if (hasMissingBlankAnswer) {
            toast.error(t('assignments.save_form_fill_blank_first', {
                defaultValue: '請先填寫所有空格，再儲存本題。',
            }));
            return;
        }

        const values = {
            assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid || null,
            task_submission: userSubmissions,
            grade: 0,
            task_submission_grade_feedback: '',
        };

        try {
            const res = await handleAssignmentTaskSubmission(
                values,
                assignmentTaskUUID,
                assignment.assignment_object.assignment_uuid,
                access_token
            );

            if (res.success) {
                toast.success(t('assignments.task_answer_saved_not_submitted'));
                // Update userSubmissions with the returned UUID for future updates
                const updatedUserSubmissions = {
                    ...userSubmissions,
                    assignment_task_submission_uuid: res.data?.assignment_task_submission_uuid || userSubmissions.assignment_task_submission_uuid
                };
                setUserSubmissions(updatedUserSubmissions);
                setInitialUserSubmissions(updatedUserSubmissions);
                setShowSavingDisclaimer(false);
                queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid) });
            } else {
                toast.error(responseErrorMessage(res, '提交答案失敗，請稍後再試。'));
            }
        } catch (error) {
            toast.error(responseErrorMessage(error, '提交答案失敗，請稍後再試。'));
        }
    };

    const gradeFC = async () => {
        if (!user_id) {
            toast.error('找不到學生資料，無法批改。');
            return;
        }

        // Calculate grade based on correct answers
        let correctAnswers = 0;
        let totalBlanks = 0;

        questions.forEach((question) => {
            question.blanks.forEach((blank) => {
                totalBlanks++;
                const userAnswer = userSubmissions.submissions.find(
                    (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                );
                if (userAnswer && isCorrectBlankAnswer(userAnswer.answer, getBlankCorrectAnswer(blank))) {
                    correctAnswers++;
                }
            });
        });

        const maxPoints = assignmentTaskOutsideProvider?.max_grade_value || 100;
        const finalGrade = totalBlanks > 0 ? Math.round((correctAnswers / totalBlanks) * maxPoints) : 0;

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
                toast.success(`已自動批改：${finalGrade} 分（${correctAnswers}/${totalBlanks} 正確）`);
            } else {
                toast.error(responseErrorMessage(res, '批改失敗，請稍後再試。'));
            }
        } catch (error) {
            toast.error(responseErrorMessage(error, '批改失敗，請稍後再試。'));
        }
    };

    async function getAssignmentTaskSubmissionFromIdentifiedUserUI() {
        if (!access_token || !user_id) {
            return;
        }
        
        if (assignmentTaskUUID) {
            const res = await getAssignmentTaskSubmissionsUser(assignmentTaskUUID, user_id, assignment.assignment_object.assignment_uuid, access_token);
            if (res.success) {
                setUserSubmissions({
                    ...res.data.task_submission,
                    assignment_task_submission_uuid: res.data.assignment_task_submission_uuid
                });
                setInitialUserSubmissions({
                    ...res.data.task_submission,
                    assignment_task_submission_uuid: res.data.assignment_task_submission_uuid
                });
                setUserSubmissionObject(res.data);
            }
        }
    }

    useEffect(() => {
        // Used only by grading view — student view hydrates from useAssignments() context
        const loadAssignmentTask = async () => {
            if (assignmentTaskUUID) {
                const res = await getAssignmentTask(assignmentTaskUUID, access_token);
                if (res.success) {
                    setAssignmentTaskOutsideProvider(res.data);
                    if (res.data.contents?.questions && res.data.contents.questions.length > 0) {
                        setQuestions(res.data.contents.questions);
                    } else if (view !== 'teacher') {
                        setQuestions([]);
                    }
                }
            }
        };

        // Hydrate task data + user submission for the student view from the
        // already-fetched AssignmentContext + batch task-submissions map. No
        // per-task network calls.
        const hydrateStudentFromContext = () => {
            if (!assignmentTaskUUID) return;
            const task = assignment?.assignment_tasks?.find(
                (t: any) => t.assignment_task_uuid === assignmentTaskUUID
            );
            if (task) {
                setAssignmentTaskOutsideProvider(task);
                if (task.contents?.questions && task.contents.questions.length > 0) {
                    setQuestions(task.contents.questions);
                } else {
                    setQuestions([]);
                }
            }
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
            } else if (taskSubmissionsMap !== null) {
                const emptySubmission = {
                    questions: [],
                    submissions: [],
                };
                setUserSubmissions(emptySubmission);
                setInitialUserSubmissions(emptySubmission);
            }
        };

        // Set assignment task UUID in context
        assignmentTaskStateHook({
            setSelectedAssignmentTaskUUID: assignmentTaskUUID,
        });

        // Teacher area - Load from context first, then from API if needed
        if (view === 'teacher') {
            if (assignmentTaskState.assignmentTask.contents?.questions) {
                setQuestions(assignmentTaskState.assignmentTask.contents.questions);
            } else {
                loadAssignmentTask();
            }
        }
        // Student area: zero per-task network calls.
        else if (view === 'student') {
            hydrateStudentFromContext();
        }
        // Grading area: per-task fetches are fine here (one task at a time).
        else if (view === 'grading') {
            loadAssignmentTask();
            getAssignmentTaskSubmissionFromIdentifiedUserUI();
        }
    }, [assignmentTaskState, assignment, assignmentTaskStateHook, access_token, assignmentTaskUUID, view, taskSubmissionsMap]);

    useEffect(() => {
        if (JSON.stringify(userSubmissions) !== JSON.stringify(initialUserSubmissions)) {
            setShowSavingDisclaimer(true);
        } else {
            setShowSavingDisclaimer(false);
        }
    }, [userSubmissions, initialUserSubmissions]);

    useEffect(() => {
        if (view !== 'teacher' || (questions && questions.length > 0)) return;
        setQuestions([
            { 
                questionText: '', 
                questionUUID: 'question_' + uuidv4(), 
                blanks: [{ 
                    placeholder: '填寫答案',
                    correctAnswer: '',
                    hint: '',
                    blankUUID: 'blank_' + uuidv4()
                }] 
            },
        ]);
    }, [view, questions]);

    if (view === 'teacher' && (!questions || questions.length === 0)) {
        return null;
    }

    if (view === 'teacher' || (questions && questions.length > 0)) {
        return (
            <AssignmentBoxUI 
                submitFC={submitFC} 
                saveFC={saveFC} 
                gradeFC={gradeFC} 
                view={view} 
                currentPoints={userSubmissionObject?.grade} 
                maxPoints={assignmentTaskOutsideProvider?.max_grade_value} 
                showSavingDisclaimer={showSavingDisclaimer} 
                type="form"
            >
                {view === 'grading' && (
                    <div className="mb-6 p-4 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-lg border border-blue-200">
                        <h3 className="text-sm font-semibold text-gray-800 mb-2">提交摘要</h3>
                        <div className="grid grid-cols-3 gap-4 text-sm">
                            <div className="text-center">
                                <div className="text-lg font-bold text-blue-600">
                                    {questions.flatMap(q => q.blanks).length}
                                </div>
                                <div className="text-gray-600">填空數</div>
                            </div>
                            <div className="text-center">
                                <div className="text-lg font-bold text-green-600">
                                    {questions.flatMap(q => q.blanks).filter(blank => {
                                        const userAnswer = userSubmissions.submissions.find(s => s.blankUUID === blank.blankUUID);
                                        return userAnswer && isCorrectBlankAnswer(userAnswer.answer, getBlankCorrectAnswer(blank));
                                    }).length}
                                </div>
                                <div className="text-gray-600">正確</div>
                            </div>
                            <div className="text-center">
                                <div className="text-lg font-bold text-red-600">
                                    {questions.flatMap(q => q.blanks).length - questions.flatMap(q => q.blanks).filter(blank => {
                                        const userAnswer = userSubmissions.submissions.find(s => s.blankUUID === blank.blankUUID);
                                        return userAnswer && isCorrectBlankAnswer(userAnswer.answer, getBlankCorrectAnswer(blank));
                                    }).length}
                                </div>
                                <div className="text-gray-600">錯誤</div>
                            </div>
                        </div>
                    </div>
                )}
                <div className="flex flex-col space-y-6">
                    {questions && questions.map((question, qIndex) => (
                        <div key={qIndex} className="flex flex-col space-y-1.5">
                            <div className="flex space-x-2 items-center">
                                {view === 'teacher' ? (
                                    <input
                                        value={question.questionText}
                                        onChange={(e) => handleQuestionChange(qIndex, e.target.value)}
                                        placeholder="輸入題目，可用 ___ 表示填空位置"
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

                            {/* Blanks section */}
                            <div className="flex flex-col space-y-2">
                                {question.blanks.map((blank, bIndex) => (
                                    <div key={bIndex} className="flex">
                                        <div className={"blank-item outline-3 outline-white pr-2 shadow-sm w-full flex items-center space-x-2 min-h-[40px] hover:bg-opacity-100 hover:shadow-md rounded-lg bg-white text-sm duration-150 ease-linear nice-shadow " + (view == 'student' ? 'active:scale-105' : '')}>
                                            <div className="font-bold text-base flex items-center justify-center h-full w-[40px] rounded-l-md text-slate-800 bg-slate-100/80">
                                                <Type size={14} />
                                            </div>
                                            {view === 'teacher' ? (
                                                <div className="flex flex-col space-y-1 w-full py-2">
                                                    <input
                                                        value={blank.placeholder}
                                                        onChange={(e) => handleBlankChange(qIndex, bIndex, 'placeholder', e.target.value)}
                                                        placeholder="學生看到的提示文字"
                                                        className="w-full mx-2 px-3 pr-6 text-neutral-600 bg-[#00008b00] border-2 border-gray-200 rounded-md border-dotted text-sm font-bold"
                                                    />
                                                    <input
                                                        value={getBlankCorrectAnswer(blank)}
                                                        onChange={(e) => handleBlankChange(qIndex, bIndex, 'correctAnswer', e.target.value)}
                                                        placeholder="正確答案"
                                                        className="w-full mx-2 px-3 pr-6 text-neutral-600 bg-lime-50 border-2 border-lime-200 rounded-md border-dotted text-sm font-bold"
                                                    />
                                                    <input
                                                        value={blank.hint || ''}
                                                        onChange={(e) => handleBlankChange(qIndex, bIndex, 'hint', e.target.value)}
                                                        placeholder={t('dashboard.assignments.editor.task_editor.general.hint_optional')}
                                                        className="w-full mx-2 px-3 pr-6 text-neutral-600 bg-blue-50 border-2 border-blue-200 rounded-md border-dotted text-xs"
                                                    />
                                                </div>
                                            ) : view === 'grading' ? (
                                                <div className="flex flex-col space-y-1 w-full py-2">
                                                    <div className="flex items-center space-x-2 w-full mx-2">
                                                        <input
                                                            value={userSubmissions.submissions.find(
                                                                (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                            )?.answer || ''}
                                                            readOnly
                                                            className="flex-1 px-3 pr-6 text-neutral-600 bg-gray-50 border-2 border-gray-200 rounded-md text-sm font-bold"
                                                        />
                                                    </div>
                                                    <div className="mx-2 text-xs text-gray-600">
                                                        <span className="font-semibold">正確答案：</span> {getBlankCorrectAnswer(blank)}
                                                    </div>
                                                    {blank.hint && (
                                                        <div className="mx-2 text-xs text-blue-600 italic">💡 {blank.hint}</div>
                                                    )}
                                                </div>
                                            ) : (
                                                <div className="flex flex-col space-y-1 w-full py-2">
                                                    <input
                                                        value={userSubmissions.submissions?.find(
                                                            (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                        )?.answer || ''}
                                                        onChange={(e) => !submissionIsFinal && handleUserAnswerChange(question.questionUUID!, blank.blankUUID!, e.target.value)}
                                                        onBlur={(e) => !submissionIsFinal && handleUserAnswerBlur(question.questionUUID!, blank.blankUUID!, e.target.value)}
                                                        onPaste={handlePaste}
                                                        readOnly={submissionIsFinal}
                                                        placeholder={blank.placeholder}
                                                        data-blank-id={blank.blankUUID}
                                                        className="w-full mx-2 px-3 pr-6 text-neutral-600 bg-[#00008b00] border-2 border-gray-200 rounded-md focus:border-blue-400 focus:ring-2 focus:ring-blue-200 text-sm font-bold transition-all"
                                                    />
                                                    {showCorrectAnswers && (
                                                        <div className="mx-2 text-xs text-emerald-700 bg-emerald-50 px-2 py-1 rounded-md inline-flex items-center space-x-1 w-fit">
                                                            <Check size={11} />
                                                            <span className="font-semibold">{t('assignments.form.expected_answer')}:</span>
                                                            <span>{getBlankCorrectAnswer(blank)}</span>
                                                        </div>
                                                    )}
                                                    {blank.hint && (
                                                        <div className="mx-2 text-xs text-blue-600 italic">💡 {blank.hint}</div>
                                                    )}
                                                </div>
                                            )}
                                            {view === 'teacher' && (
                                                <div
                                                    className="w-[20px] flex-none flex items-center h-[20px] rounded-lg bg-slate-200/60 text-slate-500 hover:bg-slate-300 text-sm transition-all ease-linear cursor-pointer"
                                                    onClick={() => removeBlank(qIndex, bIndex)}
                                                >
                                                    <Minus size={12} className="mx-auto" />
                                                </div>
                                            )}
                                            {view === 'grading' && (
                                                <div className={`w-fit flex-none flex text-xs px-2 py-0.5 space-x-1 items-center h-fit rounded-lg ${
                                                    userSubmissions.submissions.find(
                                                        (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                    ) && isCorrectBlankAnswer(
                                                        userSubmissions.submissions.find(
                                                            (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                        )?.answer,
                                                        getBlankCorrectAnswer(blank)
                                                    )
                                                        ? 'bg-lime-200 text-lime-600'
                                                        : 'bg-rose-200/60 text-rose-500'
                                                } text-sm`}>
                                                    {userSubmissions.submissions.find(
                                                        (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                    ) && isCorrectBlankAnswer(
                                                        userSubmissions.submissions.find(
                                                            (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                        )?.answer,
                                                        getBlankCorrectAnswer(blank)
                                                    ) ? (
                                                        <>
                                                            <Check size={12} className="mx-auto" />
                                                            <p className="mx-auto font-bold text-xs">正確</p>
                                                        </>
                                                    ) : (
                                                        <>
                                                            <X size={12} className="mx-auto" />
                                                            <p className="mx-auto font-bold text-xs">錯誤</p>
                                                        </>
                                                    )}
                                                </div>
                                            )}
                                            {view === 'student' && (
                                                <div className={`w-[20px] flex-none flex items-center h-[20px] rounded-lg ${
                                                    userSubmissions.submissions.find(
                                                        (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                    )?.answer?.trim()
                                                        ? "bg-green-200/60 text-green-500"
                                                        : "bg-slate-200/60 text-slate-500"
                                                } text-sm transition-all ease-linear`}>
                                                    {userSubmissions.submissions.find(
                                                        (submission) => submission.questionUUID === question.questionUUID && submission.blankUUID === blank.blankUUID
                                                    )?.answer?.trim() ? (
                                                        <Check size={12} className="mx-auto" />
                                                    ) : (
                                                        <X size={12} className="mx-auto" />
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                        {view === 'teacher' && bIndex === question.blanks.length - 1 && question.blanks.length <= 4 && (
                                            <div className="flex justify-center mx-auto px-2">
                                                <div
                                                    className="outline-3 outline-white px-2 shadow-sm w-full flex items-center h-[40px] hover:bg-opacity-100 hover:shadow-md rounded-lg bg-white duration-150 cursor-pointer ease-linear nice-shadow"
                                                    onClick={() => addBlank(qIndex)}
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

    return (
        <div className='flex flex-row space-x-2 text-sm items-center'>
            <Info size={12} />
            <p>暫時沒有題目</p>
        </div>
    );
}

export default TaskFormObject;
