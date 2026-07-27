import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import { useAssignmentSubmission, useAssignmentTaskSubmissions } from '@components/Contexts/Assignments/AssignmentSubmissionContext';
import { useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { useOrg } from '@components/Contexts/OrgContext';
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI'
import { getAssignmentTask, getAssignmentTaskSubmissionsUser, handleAssignmentTaskSubmission, updateSubFile } from '@services/courses/assignments';
import { getTaskFileSubmissionDir } from '@services/media/media';
import { Cloud, Download, File, Info, Loader, UploadCloud } from 'lucide-react'
import Link from 'next/link';
import React, { useEffect, useState } from 'react'
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';

type FileSchema = {
    fileUUID: string;
    assignment_task_submission_uuid?: string;
};

type TaskFileObjectProps = {
    view: 'teacher' | 'student' | 'grading' | 'custom-grading';
    assignmentTaskUUID?: string;
    user_id?: string;
};

function responseErrorMessage(response: any, fallback: string) {
    const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    if (response instanceof Error && response.message) return response.message;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；');
    if (typeof response === 'string' && response.trim()) return response;
    return fallback;
}

export default function TaskFileObject({ view, user_id, assignmentTaskUUID }: TaskFileObjectProps) {
    const { t } = useTranslation()
    const session = useLHSession() as any;
    const org = useOrg() as any;
    const access_token = session?.data?.tokens?.access_token;
    const [isLoading, setIsLoading] = React.useState(false);
    const [localUploadFile, setLocalUploadFile] = React.useState<File | null>(null);
    const [error, setError] = React.useState<string | null>(null);
    const [assignmentTask, setAssignmentTask] = React.useState<any>(null);
    const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any;
    const assignment = useAssignments() as any;
    const assignmentSubmission = useAssignmentSubmission() as any;
    const taskSubmissionsMap = useAssignmentTaskSubmissions();
    const queryClient = useQueryClient();
    const assignmentSubmissionStatus = Array.isArray(assignmentSubmission) && assignmentSubmission.length > 0
        ? assignmentSubmission[0].submission_status
        : null;
    const submissionIsFinal = view === 'student'
        && !!assignmentSubmissionStatus
        && !['PENDING', 'NOT_SUBMITTED'].includes(assignmentSubmissionStatus);

    /* TEACHER VIEW CODE */
    /* TEACHER VIEW CODE */

    /* STUDENT VIEW CODE */
    const [showSavingDisclaimer, setShowSavingDisclaimer] = useState<boolean>(false);
    const [userSubmissions, setUserSubmissions] = useState<FileSchema>({
        fileUUID: '',
    });
    const [initialUserSubmissions, setInitialUserSubmissions] = useState<FileSchema>({
        fileUUID: '',
    });

    const handleFileChange = async (event: any) => {
        // Check if user is authenticated
        if (!access_token) {
            setError(t('dashboard.assignments.editor.task_editor.general.auth_required'));
            return;
        }
        if (submissionIsFinal) {
            setError('這份作業已提交，請按「重做」後再更換檔案。');
            return;
        }

        const file = event.target.files[0]
        if (!file) {
            return;
        }

        setLocalUploadFile(file)
        setIsLoading(true)
        try {
            const res = await updateSubFile(
                file,
                assignmentTask.assignment_task_uuid,
                assignment.assignment_object.assignment_uuid,
                access_token
            )

            // wait for 1 second to show loading animation
            await new Promise((r) => setTimeout(r, 1500))
            if (res?.success) {
                assignmentTaskStateHook({ type: 'reload' })
                setUserSubmissions({
                    fileUUID: res.data.file_uuid,
                    assignment_task_submission_uuid: res.data.assignment_task_submission_uuid
                })
                queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid) });
                setIsLoading(false)
                setError('')
            } else {
                setError(responseErrorMessage(res, '上傳失敗，請稍後再試。'))
                setIsLoading(false)
            }
        } catch (error) {
            setError(responseErrorMessage(error, '上傳失敗，請稍後再試。'))
            setIsLoading(false)
        }
    }

    // Hydrate from the batch task-submissions cache instead of calling
    // /submissions/me per task. Re-runs when the batch payload arrives.
    function hydrateSubmissionFromBatch() {
        if (!assignmentTaskUUID) return;
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
        }
    }

    const submitFC = async () => {
        // Check if user is authenticated
        if (!access_token) {
            toast.error(t('dashboard.assignments.editor.task_editor.general.auth_required_submit'));
            return;
        }
        if (submissionIsFinal) {
            toast.error('這份作業已提交，請按「重做」後再更換檔案。');
            return;
        }

        // Save the file submission to the server
        const values = {
            assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid || null,
            task_submission: userSubmissions,
            grade: 0,
            task_submission_grade_feedback: '',
        };
        if (assignmentTaskUUID) {
            try {
                const res = await handleAssignmentTaskSubmission(values, assignmentTaskUUID, assignment.assignment_object.assignment_uuid, access_token);
                if (res?.success) {
                    assignmentTaskStateHook({
                        type: 'reload',
                    });
                    toast.success(t('assignments.task_answer_saved_not_submitted'));
                    setShowSavingDisclaimer(false);
                    // Update userSubmissions with the returned UUID for future updates
                    const updatedUserSubmissions = {
                        ...userSubmissions,
                        assignment_task_submission_uuid: res.data?.assignment_task_submission_uuid || userSubmissions.assignment_task_submission_uuid
                    };
                    setUserSubmissions(updatedUserSubmissions);
                    setInitialUserSubmissions(updatedUserSubmissions);
                    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid) });
                } else {
                    toast.error(responseErrorMessage(res, t('dashboard.assignments.editor.toasts.task_save_error')));
                }
            } catch (error) {
                toast.error(responseErrorMessage(error, t('dashboard.assignments.editor.toasts.task_save_error')));
            }
        }
    };

    // Used only by grading view — student view hydrates from useAssignments() context
    async function getAssignmentTaskUI() {
        if (!access_token) {
            return;
        }

        if (assignmentTaskUUID) {
            const res = await getAssignmentTask(assignmentTaskUUID, access_token);
            if (res.success) {
                setAssignmentTask(res.data);
                setAssignmentTaskOutsideProvider(res.data);
            }
        }
    }

    function hydrateTaskFromContext() {
        if (!assignmentTaskUUID) return;
        const task = assignment?.assignment_tasks?.find(
            (t: any) => t.assignment_task_uuid === assignmentTaskUUID
        );
        if (task) {
            setAssignmentTask(task);
            setAssignmentTaskOutsideProvider(task);
        }
    }

    // Detect changes between initial and current submissions
    useEffect(() => {
        if (userSubmissions.fileUUID !== initialUserSubmissions.fileUUID) {
            setShowSavingDisclaimer(true);
        } else {
            setShowSavingDisclaimer(false);
        }
    }, [userSubmissions]);

    /* STUDENT VIEW CODE */

    /* GRADING VIEW CODE */
    const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null);
    async function getAssignmentTaskSubmissionFromIdentifiedUserUI() {
        if (!access_token) {
            // Silently fail if not authenticated
            return;
        }
        
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

    async function gradeCustomFC(grade: number) {
        if (assignmentTaskUUID) {
            if (grade > assignmentTaskOutsideProvider.max_grade_value) {
                toast.error(`分數不能超過 ${assignmentTaskOutsideProvider.max_grade_value} 分`);
                return;
            }
            
    
            // Save the grade to the server
            const values = {
                assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid,
                task_submission: userSubmissions,
                grade: grade,
                task_submission_grade_feedback: '老師批改：@' + session.data.user.username,
            };
    
            try {
                const res = await handleAssignmentTaskSubmission(values, assignmentTaskUUID, assignment.assignment_object.assignment_uuid, access_token);
                if (res?.success) {
                    getAssignmentTaskSubmissionFromIdentifiedUserUI();
                    toast.success(`已批改：${grade} 分`);
                } else {
                    toast.error(responseErrorMessage(res, '批改失敗，請稍後再試。'));
                }
            } catch (error) {
                toast.error(responseErrorMessage(error, '批改失敗，請稍後再試。'));
            }
        }
    }

    /* GRADING VIEW CODE */
    const [assignmentTaskOutsideProvider, setAssignmentTaskOutsideProvider] = useState<any>(null);
    useEffect(() => {
        // Student area: hydrate from already-fetched context payloads.
        if (view === 'student') {
            hydrateTaskFromContext()
            hydrateSubmissionFromBatch()
        }

        // Grading area: per-task fetches are fine here (one task at a time).
        else if (view == 'custom-grading') {
            getAssignmentTaskUI();
            getAssignmentTaskSubmissionFromIdentifiedUserUI();
        }
    }, [view, assignmentTaskUUID, assignment?.assignment_tasks, taskSubmissionsMap])

    return (
        <AssignmentBoxUI submitFC={submitFC} showSavingDisclaimer={showSavingDisclaimer} view={view} gradeCustomFC={gradeCustomFC} currentPoints={userSubmissionObject?.grade} maxPoints={assignmentTaskOutsideProvider?.max_grade_value} type="file">
            {view === 'teacher' && (
                <div className='flex flex-col sm:flex-row py-5 sm:py-6 text-xs sm:text-sm justify-center mx-auto space-y-2 sm:space-y-0 sm:space-x-3 text-slate-600 px-4 sm:px-2 text-center sm:text-left bg-slate-50 rounded-lg border border-slate-100'>
                    <Info size={18} className="mx-auto sm:mx-0 text-slate-500" />
                    <p>學生可以為此題提交文件，老師可在提交記錄中查看並評分。</p>
                </div>
            )}
            {view === 'custom-grading' && (
                <div className='flex flex-col space-y-4 w-full px-2 sm:px-0'>
                    <div className='flex flex-col sm:flex-row py-5 sm:py-6 text-xs sm:text-sm justify-center mx-auto space-y-2 sm:space-y-0 sm:space-x-3 text-slate-600 px-4 sm:px-2 text-center sm:text-left bg-slate-50 rounded-lg border border-slate-100'>
                        <Download size={18} className="mx-auto sm:mx-0 text-slate-500" />
                        <p>請下載學生提交的文件，人工查看後在上方輸入分數。</p>
                    </div>
                    {userSubmissions.fileUUID && !isLoading && assignmentTaskUUID && (
                        <Link
                            href={getTaskFileSubmissionDir(org?.org_uuid, assignment.course_object.course_uuid, assignment.activity_object.activity_uuid, assignment.assignment_object.assignment_uuid, assignmentTaskUUID, userSubmissions.fileUUID)}
                            target='_blank'
                            className='flex flex-col rounded-lg bg-white text-gray-500 shadow-xs hover:shadow-md transition-shadow border border-gray-100 px-4 sm:px-5 py-4 space-y-1 items-center relative w-full sm:w-auto mx-auto'>
                            <div className='absolute top-0 right-0 transform translate-x-1/2 -translate-y-1/2 bg-emerald-500 rounded-full p-1.5 text-white flex justify-center items-center shadow-xs'>
                                <Cloud size={14} />
                            </div>

                            <div className='flex space-x-2 mt-2 items-center'>
                                <File size={18} className="text-emerald-500" />
                                <div className='font-medium text-xs sm:text-sm uppercase break-all'>
                                    {`${userSubmissions.fileUUID.slice(0, 8)}...${userSubmissions.fileUUID.slice(-4)}`}
                                </div>
                            </div>
                        </Link>
                    )}
                </div>
            )}
            {view === 'student' && (
                <>
                    <div className="w-full bg-white rounded-lg border border-gray-100 min-h-[200px] shadow-xs px-4 sm:px-6 py-5 sm:py-6">
                        <div className="flex flex-col justify-center items-center h-full w-full">
                            <div className="flex flex-col justify-center items-center w-full max-w-full">
                                <div className="flex flex-col justify-center items-center w-full">
                                    {error && (
                                        <div className="flex justify-center bg-red-50 border border-red-100 rounded-md text-red-600 space-x-2 items-center p-3 transition-all shadow-xs w-full sm:w-auto mb-4">
                                            <div className="text-xs sm:text-sm font-medium">{error}</div>
                                        </div>
                                    )}
                                </div>
                                {localUploadFile && !isLoading && (
                                    <div className='flex flex-col rounded-lg bg-white text-gray-500 shadow-xs border border-gray-100 px-4 sm:px-5 py-4 space-y-1 items-center relative w-full sm:w-auto mt-3'>
                                        <div className='absolute top-0 right-0 transform translate-x-1/2 -translate-y-1/2 bg-emerald-500 rounded-full p-1.5 text-white flex justify-center items-center shadow-xs'>
                                            <Cloud size={14} />
                                        </div>

                                        <div className='flex space-x-2 mt-2 items-center'>
                                            <File size={18} className="text-emerald-500" />
                                            <div className='font-medium text-xs sm:text-sm uppercase break-all'>
                                                {localUploadFile.name.length > 20 
                                                    ? `${localUploadFile.name.slice(0, 10)}...${localUploadFile.name.slice(-10)}`
                                                    : localUploadFile.name}
                                            </div>
                                        </div>
                                    </div>
                                )}
                                {userSubmissions.fileUUID && !isLoading && !localUploadFile && (
                                    <div className='flex flex-col rounded-lg bg-white text-gray-500 shadow-xs border border-gray-100 px-4 sm:px-5 py-4 space-y-1 items-center relative w-full sm:w-auto mt-3'>
                                        <div className='absolute top-0 right-0 transform translate-x-1/2 -translate-y-1/2 bg-emerald-500 rounded-full p-1.5 text-white flex justify-center items-center shadow-xs'>
                                            <Cloud size={14} />
                                        </div>

                                        <div className='flex space-x-2 mt-2 items-center'>
                                            <File size={18} className="text-emerald-500" />
                                            <div className='font-medium text-xs sm:text-sm uppercase break-all'>
                                                {`${userSubmissions.fileUUID.slice(0, 8)}...${userSubmissions.fileUUID.slice(-4)}`}
                                            </div>
                                        </div>
                                    </div>
                                )}
                                <div className='flex flex-col sm:flex-row pt-5 font-medium space-y-1 sm:space-y-0 sm:space-x-2 text-xs items-center text-slate-500 text-center sm:text-left bg-slate-50 rounded-lg px-3 py-2 mt-5 border border-slate-100 w-full sm:w-auto'>
                                    <Info size={15} className="mx-auto sm:mx-0 text-slate-400" />
                                    <p>{t('dashboard.assignments.editor.task_editor.general.allowed_formats')}</p>
                                </div>
                                {!access_token ? (
                                    <div className="flex justify-center items-center w-full mt-5">
                                        <div className="flex justify-center bg-amber-50 border border-amber-100 rounded-md text-amber-600 space-x-2 items-center p-3 transition-all shadow-xs w-full sm:w-auto">
                                            <Info size={15} className="text-amber-500" />
                                            <div className="text-xs sm:text-sm font-medium">{t('dashboard.assignments.editor.task_editor.general.sign_in_required')}</div>
                                        </div>
                                    </div>
                                ) : isLoading ? (
                                    <div className="flex justify-center items-center w-full mt-5">
                                        <input
                                            type="file"
                                            id="fileInput"
                                            style={{ display: 'none' }}
                                            onChange={handleFileChange}
                                        />
                                        <div className="font-medium animate-pulse antialiased items-center bg-slate-100 text-slate-600 text-xs sm:text-sm rounded-md px-4 sm:px-5 py-2.5 flex">
                                            <Loader size={15} className="mr-2" />
                                            <span>上傳中</span>
                                        </div>
                                    </div>
                                ) : submissionIsFinal ? (
                                    <div className="flex justify-center items-center w-full mt-5">
                                        <div className="flex justify-center bg-amber-50 border border-amber-100 rounded-md text-amber-700 space-x-2 items-center p-3 transition-all shadow-xs w-full sm:w-auto">
                                            <Info size={15} className="text-amber-600" />
                                            <div className="text-xs sm:text-sm font-medium">作業已提交，不能更換檔案。請按「重做」後再修改。</div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex justify-center items-center w-full mt-5">
                                        <input
                                            type="file"
                                            id={"fileInput_" + assignmentTaskUUID}
                                            style={{ display: 'none' }}
                                            onChange={handleFileChange}
                                        />
                                        <button
                                            className="font-medium antialiased items-center text-white text-xs sm:text-sm rounded-md px-4 sm:px-5 py-2.5 flex bg-emerald-500 hover:bg-emerald-600 transition-colors shadow-xs"
                                            onClick={() => document.getElementById("fileInput_" + assignmentTaskUUID)?.click()}
                                        >
                                            <UploadCloud size={15} className="mr-2" />
                                            <span>上傳文件</span>
                                        </button>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </>
            )}
        </AssignmentBoxUI>
    )
}
