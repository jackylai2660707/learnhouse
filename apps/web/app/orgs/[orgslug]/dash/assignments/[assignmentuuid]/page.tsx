'use client';
import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import {
    AlertCircle,
    BookOpen,
    BookX,
    EllipsisVertical,
    Eye,
    GraduationCap,
    Layers2,
    Pencil,
    Shield,
    UserRoundPen,
    UserPlus,
    Backpack,
    Zap,
    BarChart3,
    CheckCircle2,
} from 'lucide-react'
import React, { useEffect } from 'react'
import { AssignmentProvider, useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import ToolTip from '@components/Objects/StyledElements/Tooltip/Tooltip';
import { updateAssignment } from '@services/courses/assignments';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { useOrg } from '@components/Contexts/OrgContext';
import { getUserGroups } from '@services/usergroups/usergroups';
import { getUriWithOrg } from '@services/config/config';
import toast from 'react-hot-toast';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { updateActivity } from '@services/courses/activities';
import { updateCourse } from '@services/courses/courses';
// Lazy Loading
import dynamic from 'next/dynamic';
import AssignmentEditorSubPage from './subpages/AssignmentEditorSubPage';
import EditAssignmentModal from '@components/Objects/Modals/Activities/Assignments/EditAssignmentModal';
import { useTranslation } from 'react-i18next';
import {
    SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS,
    coerceSimplePilotBoolean,
    countAiFallbackStarterTasks,
    countNonSimplePilotAssignmentTasks,
    getSimplePilotAssignmentMissingSettings,
    getSimplePilotAssignmentTaskSetupIssues,
    getSimplePilotAssignmentTargetUsergroupCount,
    getSimplePilotAssignmentTargetUsergroupIds,
} from '@lib/simple-pilot-assignments';
const AssignmentSubmissionsSubPage = dynamic(() => import('./subpages/AssignmentSubmissionsSubPage'))
const AssignmentAnalyticsSubPage = dynamic(() => import('./subpages/AssignmentAnalyticsSubPage'))

function responseErrorMessage(response: any, fallback: string) {
    const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    if (response instanceof Error && response.message) return response.message;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；');
    return fallback;
}

function dateInputValue(date: Date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function assignmentDueDateInputValue(assignmentObject: any) {
    const dueDate = String(assignmentObject?.due_date || '').slice(0, 10).trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dueDate)) return '';
    const parsedDate = new Date(`${dueDate}T00:00:00`);
    if (Number.isNaN(parsedDate.getTime()) || dateInputValue(parsedDate) !== dueDate) return '';
    return dueDate;
}

function assignmentDueDateIsPast(assignmentObject: any) {
    const dueDate = assignmentDueDateInputValue(assignmentObject);
    if (!dueDate) return false;
    return dueDate < dateInputValue(new Date());
}

function assignmentDueDateIsMissingOrInvalid(assignmentObject: any) {
    return !assignmentDueDateInputValue(assignmentObject);
}

function buildSimplePilotPublishHint(
    assignmentObject: any,
    tasks: any[],
    isPublished: boolean,
    orgHasUsergroups = false,
    isLoadingUsergroups = false,
    usergroupsLoadFailed = false,
    invalidTargetUsergroupCount = 0,
    emptyTargetUsergroupCount = 0
) {
    const taskCount = tasks.length;
    const targetUsergroupCount = getSimplePilotAssignmentTargetUsergroupCount(assignmentObject);
    const prefix = isPublished ? '已發布：' : '';
    if (taskCount <= 0) {
        return {
            tone: isPublished ? 'rose' : 'cyan',
            message: isPublished
                ? '已發布但暫時沒有題目，請先取消發布並新增選擇、填空或短問答。'
                : '先用 AI、題庫或手動建立 3 題簡單題，再發布給學生。',
        };
    }
    if (taskCount > SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS) {
        return {
            tone: 'amber',
            message: `${prefix}共有 ${taskCount} 題；校內試行建議每份作業最多 ${SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題。請拆成多份簡單作業，學生更容易完成。`,
        };
    }

    const aiFallbackTaskCount = countAiFallbackStarterTasks(tasks);
    if (aiFallbackTaskCount > 0) {
        return {
            tone: 'rose',
            message: `${prefix}包含 ${aiFallbackTaskCount} 題 AI 備用題。請先把題目內容改成正式課堂題目，再發布給學生。`,
        };
    }

    const nonSimpleCount = countNonSimplePilotAssignmentTasks(tasks);
    if (nonSimpleCount > 0) {
        return {
            tone: 'amber',
            message: `${prefix}包含 ${nonSimpleCount} 題進階題型；校內試行發布只支援選擇、填空、短問答和作文，讓學生提交後可以清楚批改或覆核。`,
        };
    }
    const taskSetupIssues = getSimplePilotAssignmentTaskSetupIssues(tasks);
    if (taskSetupIssues.length > 0) {
        return {
            tone: 'rose',
            message: `${prefix}${taskSetupIssues[0]}請先補完整答案，讓系統能自動批改。`,
        };
    }
    if (!isPublished && assignmentDueDateIsPast(assignmentObject)) {
        return {
            tone: 'amber',
            message: '截止日期已經早於今天。發布前請先修改截止日期，避免學生一收到作業就逾期。',
        };
    }
    if (!isPublished && assignmentDueDateIsMissingOrInvalid(assignmentObject)) {
        return {
            tone: 'amber',
            message: '發布前請先設定有效的截止日期，成績表才能準確顯示未交、逾期和快截止學生。',
        };
    }
    if (!isPublished && isLoadingUsergroups) {
        return {
            tone: 'amber',
            message: targetUsergroupCount > 0
                ? '正在確認已指定的班級/群組，請稍後再發布。'
                : '正在讀取班級/群組，請稍後再發布。',
        };
    }
    if (!isPublished && usergroupsLoadFailed) {
        return {
            tone: 'amber',
            message: '班級/群組資料暫時載入失敗。請重新讀取後再發布，避免學生收不到作業或成績表範圍錯誤。',
        };
    }
    if (invalidTargetUsergroupCount > 0) {
        return {
            tone: 'amber',
            message: `${prefix}有 ${invalidTargetUsergroupCount} 個班級/群組已不存在。請重新指定發布對象，避免學生收不到作業或成績表範圍錯誤。`,
        };
    }
    if (emptyTargetUsergroupCount > 0) {
        return {
            tone: 'amber',
            message: `${prefix}有 ${emptyTargetUsergroupCount} 個班級暫時沒有學生。請先把學生加入班級，避免發布後沒有人收到作業。`,
        };
    }

    const missingSettings = getSimplePilotAssignmentMissingSettings(assignmentObject, {
        requireTargets: orgHasUsergroups,
        targetLabel: '指定班級/群組',
        answersLabel: '顯示參考答案',
        booleanMode: 'truthy',
    });
    if (missingSettings.length > 0) {
        return {
            tone: 'amber',
            message: `${prefix}已有 ${taskCount} 題簡單題。按發布時系統會自動套用${missingSettings.join('、')}，讓學生提交後可批改、可重做、可查看答案。`,
        };
    }

    return {
        tone: 'emerald',
        message: `${prefix}已有 ${taskCount} 題簡單題，適合發布給學生：可自動批改、可重做、可看參考答案。`,
    };
}

function publishHintClass(tone: string) {
    const tones: Record<string, string> = {
        amber: 'border-amber-100 bg-amber-50 text-amber-800',
        cyan: 'border-cyan-100 bg-cyan-50 text-cyan-800',
        emerald: 'border-emerald-100 bg-emerald-50 text-emerald-800',
        rose: 'border-rose-100 bg-rose-50 text-rose-800',
    };
    return tones[tone] || tones.cyan;
}

function simplePilotPublishBlockReason(
    assignmentObject: any,
    tasks: any[],
    orgHasUsergroups: boolean,
    isLoadingUsergroups: boolean,
    usergroupsLoadFailed = false,
    invalidTargetUsergroupCount = 0,
    emptyTargetUsergroupCount = 0
) {
    if (tasks.length <= 0) {
        return '發布前請先用 AI、題庫或手動建立選擇、填空或短問答。';
    }
    const aiFallbackTaskCount = countAiFallbackStarterTasks(tasks);
    if (aiFallbackTaskCount > 0) {
        return '這份作業包含 AI 備用題。請先修改成正式題目再發布。';
    }
    const nonSimpleTaskCount = countNonSimplePilotAssignmentTasks(tasks);
    if (nonSimpleTaskCount > 0) {
        return '校內試行發布只支援選擇、填空、短問答和作文。請先改成簡單題型。';
    }
    const taskSetupIssues = getSimplePilotAssignmentTaskSetupIssues(tasks);
    if (taskSetupIssues.length > 0) {
        return `${taskSetupIssues[0]}請先補完整答案，讓系統能自動批改。`;
    }
    if (assignmentDueDateIsPast(assignmentObject)) {
        return '發布前請把截止日期改為今天或之後，避免學生一收到作業就逾期。';
    }
    if (assignmentDueDateIsMissingOrInvalid(assignmentObject)) {
        return '發布前請先設定有效的截止日期。';
    }
    const targetUsergroupCount = getSimplePilotAssignmentTargetUsergroupCount(assignmentObject);
    if (isLoadingUsergroups) {
        return targetUsergroupCount > 0
            ? '正在確認已指定的班級/群組，請稍後再發布。'
            : '正在讀取班級/群組，請稍後再發布。';
    }
    if (usergroupsLoadFailed) {
        return '班級/群組資料載入失敗，請重新讀取後再發布。';
    }
    if (targetUsergroupCount <= 0 && orgHasUsergroups) {
        return '發布前請先指定班級/群組。';
    }
    if (invalidTargetUsergroupCount > 0) {
        return '發布前請重新指定班級/群組，部分班級已不存在。';
    }
    if (emptyTargetUsergroupCount > 0) {
        return '發布前請先把學生加入所選班級/群組。';
    }
    return '';
}

function countInvalidTargetUsergroups(assignmentObject: any, usergroups: any[] | undefined) {
    if (!Array.isArray(usergroups)) return 0;
    const validIds = new Set(
        usergroups
            .map((usergroup) => Number(usergroup?.id))
            .filter((id) => Number.isFinite(id))
    );
    return getSimplePilotAssignmentTargetUsergroupIds(assignmentObject)
        .filter((id: any) => !validIds.has(Number(id)))
        .length;
}

function countEmptyTargetUsergroups(assignmentObject: any, usergroups: any[] | undefined) {
    if (!Array.isArray(usergroups)) return 0;
    const usergroupsById = new Map<number, any>();
    usergroups.forEach((usergroup) => {
        const id = Number(usergroup?.id);
        if (Number.isFinite(id)) {
            usergroupsById.set(id, usergroup);
        }
    });
    return getSimplePilotAssignmentTargetUsergroupIds(assignmentObject)
        .filter((id: any) => {
            const usergroup = usergroupsById.get(Number(id));
            if (!usergroup || !Object.prototype.hasOwnProperty.call(usergroup, 'member_count')) return false;
            return Number(usergroup.member_count) <= 0;
        })
        .length;
}

function shouldOpenAssignmentSettingsFromPublishError(message: string) {
    return [
        '班級',
        '群組',
        '學生帳號',
        '沒有學生',
        '發布對象',
        '加入所選',
        '收到這份作業',
    ].some((keyword) => message.includes(keyword));
}

function cleanCourseUuid(value: unknown) {
    return String(value || '').replace(/^course_/, '');
}

function publishRecoveryAction(message: string, orgslug: string) {
    if (message.includes('學生帳號') || message.includes('沒有學生')) {
        return {
            href: getUriWithOrg(orgslug, '/dash/users/settings/add'),
            label: '匯入學生',
            icon: <UserPlus size={14} />,
            detail: '先批量新增學生帳號，再把學生加入班級。',
        };
    }
    if (message.includes('班級') || message.includes('群組') || message.includes('加入所選')) {
        return {
            href: getUriWithOrg(orgslug, '/dash/users/settings/usergroups'),
            label: '管理班級',
            icon: <GraduationCap size={14} />,
            detail: '檢查班級是否存在，並確認學生已加入所選班級。',
        };
    }
    return null;
}

const ASSIGNMENT_SUBPAGES = [
    {
        id: 'editor',
        labelKey: 'dashboard.assignments.detail.tabs.editor',
        Icon: Layers2,
    },
    {
        id: 'submissions',
        labelKey: 'dashboard.assignments.detail.tabs.submissions',
        Icon: UserRoundPen,
    },
    {
        id: 'analytics',
        labelKey: 'dashboard.assignments.detail.tabs.analytics',
        Icon: BarChart3,
    },
] as const;

type AssignmentSubPage = (typeof ASSIGNMENT_SUBPAGES)[number]['id'];

function isAssignmentSubPage(value: string | null): value is AssignmentSubPage {
    return ASSIGNMENT_SUBPAGES.some((subPage) => subPage.id === value);
}

function AssignmentEdit() {
    const { t } = useTranslation()
    const params = useParams<{ assignmentuuid: string; }>()
    const searchParams = useSearchParams()
    const [selectedSubPage, setSelectedSubPage] = React.useState<AssignmentSubPage>(() => {
        const requestedSubPage = searchParams.get('subpage');
        return isAssignmentSubPage(requestedSubPage) ? requestedSubPage : 'editor';
    })
    const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([])

    function selectTabAt(index: number) {
        const nextTab = ASSIGNMENT_SUBPAGES[index]
        if (!nextTab) return
        setSelectedSubPage(nextTab.id)
        tabRefs.current[index]?.focus()
    }

    function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
        let nextIndex: number | null = null

        if (event.key === 'ArrowRight') {
            nextIndex = (currentIndex + 1) % ASSIGNMENT_SUBPAGES.length
        } else if (event.key === 'ArrowLeft') {
            nextIndex = (currentIndex - 1 + ASSIGNMENT_SUBPAGES.length) % ASSIGNMENT_SUBPAGES.length
        } else if (event.key === 'Home') {
            nextIndex = 0
        } else if (event.key === 'End') {
            nextIndex = ASSIGNMENT_SUBPAGES.length - 1
        }

        if (nextIndex === null) return
        event.preventDefault()
        selectTabAt(nextIndex)
    }

    return (
        <div className='flex min-h-dvh w-full min-w-0 flex-col md:h-screen md:min-h-0'>
            <AssignmentProvider assignment_uuid={'assignment_' + params.assignmentuuid}>
                <div className='flex flex-col bg-white z-10 nice-shadow relative'>
                    <div className='flex h-full min-w-0 flex-col gap-3 px-4 sm:px-6 md:mr-10 md:flex-row md:justify-between md:px-0'>
                        <div className="min-w-0 tracking-tighter md:mr-10 md:pl-10">
                            <BrdCmpx />
                            <div className="flex min-w-0 justify-between">
                                <div className="flex min-w-0 flex-col space-y-2">
                                    <AssignmentTitle />
                                    <AssignmentInfoBadges />
                                </div>
                            </div>
                        </div>
                        <div className='flex min-w-0 flex-col justify-center pb-3 antialiased md:pb-0'>
                            <PublishingState />
                        </div>
                    </div>
                    <div
                        role="tablist"
                        aria-label={t('common.assignments')}
                        className='flex w-full min-w-0 gap-1 overflow-x-auto px-4 pt-2 text-sm font-semibold tracking-tight sm:px-6 md:mr-10 md:px-0 md:pl-10'
                    >
                        {ASSIGNMENT_SUBPAGES.map((subPage, index) => {
                            const selected = selectedSubPage === subPage.id
                            const Icon = subPage.Icon
                            return (
                                <button
                                    key={subPage.id}
                                    ref={(element) => { tabRefs.current[index] = element }}
                                    id={`assignment-${subPage.id}-tab`}
                                    type="button"
                                    role="tab"
                                    aria-selected={selected}
                                    aria-controls={`assignment-${subPage.id}-panel`}
                                    tabIndex={selected ? 0 : -1}
                                    onClick={() => setSelectedSubPage(subPage.id)}
                                    onKeyDown={(event) => handleTabKeyDown(event, index)}
                                    className={`flex shrink-0 items-center gap-2 border-black px-2.5 py-2 text-center transition-all ease-linear focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600 focus-visible:ring-offset-2 ${selected
                                        ? 'border-b-4 text-gray-950'
                                        : 'border-b-4 border-transparent text-gray-500 hover:text-gray-800'
                                        }`}
                                >
                                    <Icon size={16} aria-hidden="true" />
                                    <span>{t(subPage.labelKey)}</span>
                                </button>
                            )
                        })}
                    </div>
                </div>
                <div
                    id={`assignment-${selectedSubPage}-panel`}
                    role="tabpanel"
                    aria-labelledby={`assignment-${selectedSubPage}-tab`}
                    tabIndex={0}
                    className={`flex w-full min-w-0 flex-1 focus-visible:outline-none ${selectedSubPage === 'editor'
                        ? 'min-h-0 flex-col max-md:[&>*]:!h-auto max-md:[&>*]:!min-w-0 max-md:[&>*]:!w-full md:flex-row'
                        : `min-h-0 max-md:flex-none max-md:[&>*]:!h-auto max-md:[&>*]:!min-w-0 max-md:[&_.px-10]:!px-4 ${selectedSubPage === 'analytics'
                            ? 'max-md:[&_.grid.grid-cols-4]:!grid-cols-2 max-md:[&_.grid.grid-cols-2]:!grid-cols-1'
                            : ''}`
                        }`}
                >
                    {selectedSubPage === 'editor' && <AssignmentEditorSubPage assignmentuuid={params.assignmentuuid} />}
                    {selectedSubPage === 'submissions' && <AssignmentSubmissionsSubPage assignment_uuid={params.assignmentuuid} />}
                    {selectedSubPage === 'analytics' && <AssignmentAnalyticsSubPage assignment_uuid={params.assignmentuuid} />}
                </div>
            </AssignmentProvider>
        </div>
    )
}

export default AssignmentEdit

function BrdCmpx() {
    const { t } = useTranslation()
    const assignment = useAssignments() as any

    useEffect(() => {
    }, [assignment])

    return (
        <div className="pt-6 pb-4">
            <Breadcrumbs items={[
                { label: t('common.assignments'), href: '/dash/assignments', icon: <Backpack size={14} /> },
                ...(assignment?.assignment_object?.title ? [{ label: assignment.assignment_object.title }] : [])
            ]} />
        </div>
    )
}

function PublishingState() {
    const { t } = useTranslation()
    const assignment = useAssignments() as any;
    const org = useOrg() as any;
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;
    const queryClient = useQueryClient();
    const [isEditModalOpen, setIsEditModalOpen] = React.useState(false);
    const [isPublishing, setIsPublishing] = React.useState(false);
    const [publishRecoveryMessage, setPublishRecoveryMessage] = React.useState('');
    const isPublished = Boolean(assignment?.assignment_object?.published);
    const tasks = Array.isArray(assignment?.assignment_tasks) ? assignment.assignment_tasks : [];
    const targetUsergroupCount = getSimplePilotAssignmentTargetUsergroupCount(assignment?.assignment_object);
    const taskSetupReady =
        tasks.length > 0 &&
        tasks.length <= SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS &&
        countAiFallbackStarterTasks(tasks) === 0 &&
        countNonSimplePilotAssignmentTasks(tasks) === 0 &&
        getSimplePilotAssignmentTaskSetupIssues(tasks).length === 0 &&
        !assignmentDueDateIsMissingOrInvalid(assignment?.assignment_object) &&
        !assignmentDueDateIsPast(assignment?.assignment_object);
    const usergroupsQuery = useQuery({
        queryKey: queryKeys.usergroups.list(org?.id),
        queryFn: async () => {
            const res = await getUserGroups(org.id, access_token)
            if (res.success === false) {
                throw new Error(res?.data?.detail || '讀取班級/群組失敗')
            }
            return Array.isArray(res.data) ? res.data : []
        },
        enabled: !!org?.id && !!access_token,
        staleTime: 60_000,
    })
    const orgHasUsergroups = !usergroupsQuery.isError && Array.isArray(usergroupsQuery.data) && usergroupsQuery.data.length > 0;
    const invalidTargetUsergroupCount = countInvalidTargetUsergroups(assignment?.assignment_object, usergroupsQuery.data);
    const emptyTargetUsergroupCount = countEmptyTargetUsergroups(assignment?.assignment_object, usergroupsQuery.data);
    const targetSetupReady = !usergroupsQuery.isLoading && (
        !orgHasUsergroups || (
            targetUsergroupCount > 0 &&
            invalidTargetUsergroupCount <= 0 &&
            emptyTargetUsergroupCount <= 0
        )
    );
    const publishHint = buildSimplePilotPublishHint(
        assignment?.assignment_object,
        tasks,
        isPublished,
        orgHasUsergroups,
        usergroupsQuery.isLoading,
        usergroupsQuery.isError,
        invalidTargetUsergroupCount,
        emptyTargetUsergroupCount
    );
    const publishBlockReason = !isPublished
        ? simplePilotPublishBlockReason(
            assignment?.assignment_object,
            tasks,
            orgHasUsergroups,
            usergroupsQuery.isLoading,
            usergroupsQuery.isError,
            invalidTargetUsergroupCount,
            emptyTargetUsergroupCount
        )
        : '';
    const publishDisabled = isPublishing || Boolean(publishBlockReason);
    const recoveryAction = publishRecoveryMessage
        ? publishRecoveryAction(publishRecoveryMessage, org?.slug || '')
        : null;

    function refreshPublishEvidence(assignmentUUID: string) {
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.detail(assignmentUUID) })
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
        if (org?.id) {
            queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org.id) })
            queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(org.id) })
            queryClient.invalidateQueries({ queryKey: queryKeys.assignments.studentQueue(org.id) })
        }
    }

    async function updateAssignmentPublishState(assignmentUUID: string) {
        if (isPublishing || !assignmentUUID || !access_token) return
        setPublishRecoveryMessage('')
        const previousPublished = isPublished
        const published = !previousPublished
        const blockReason = published
            ? simplePilotPublishBlockReason(
                assignment?.assignment_object,
                tasks,
                orgHasUsergroups,
                usergroupsQuery.isLoading,
                usergroupsQuery.isError,
                invalidTargetUsergroupCount,
                emptyTargetUsergroupCount
            )
            : '';
        if (blockReason) {
            toast.error(blockReason)
            if (
                !usergroupsQuery.isLoading &&
                orgHasUsergroups &&
                (targetUsergroupCount <= 0 || invalidTargetUsergroupCount > 0 || emptyTargetUsergroupCount > 0)
            ) {
                setIsEditModalOpen(true)
            }
            return
        }
        const toast_loading = toast.loading(t('dashboard.assignments.detail.publishing.toasts.updating'))
        setIsPublishing(true)
        try {
            const res = await updateAssignment({ published }, assignmentUUID, access_token)
            if (res.success === false) {
                const message = responseErrorMessage(res, t('dashboard.assignments.detail.publishing.toasts.update_error'));
                toast.error(message)
                setPublishRecoveryMessage(message)
                if (published && shouldOpenAssignmentSettingsFromPublishError(message)) {
                    setIsEditModalOpen(true)
                }
                return
            }
            const res2 = await updateActivity({ published }, assignment?.activity_object?.activity_uuid, access_token)
            if (!res2 || res2?.success === false) {
                await updateAssignment({ published: previousPublished }, assignmentUUID, access_token)
                refreshPublishEvidence(assignmentUUID)
                const message = responseErrorMessage(res2, t('dashboard.assignments.detail.publishing.toasts.update_error'));
                toast.error(message)
                return
            }
            if (published && assignment?.course_object?.course_uuid) {
                try {
                    await updateCourse(assignment.course_object.course_uuid, { published: true }, access_token)
                    const courseUuid = cleanCourseUuid(assignment.course_object.course_uuid)
                    queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(courseUuid) })
                    queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(assignment.course_object.course_uuid) })
                } catch (courseError) {
                    await updateActivity({ published: previousPublished }, assignment?.activity_object?.activity_uuid, access_token)
                    await updateAssignment({ published: previousPublished }, assignmentUUID, access_token)
                    refreshPublishEvidence(assignmentUUID)
                    const message = responseErrorMessage(courseError, '發布課程失敗，作業已回復為草稿。請稍後再試。');
                    toast.error(message)
                    return
                }
            }
            refreshPublishEvidence(assignmentUUID)
            setPublishRecoveryMessage('')
            toast.success(t('dashboard.assignments.detail.publishing.toasts.update_success'))
        } catch (error) {
            const message = responseErrorMessage(error, t('dashboard.assignments.detail.publishing.toasts.update_error'));
            toast.error(message)
            setPublishRecoveryMessage(message)
            if (published && shouldOpenAssignmentSettingsFromPublishError(message)) {
                setIsEditModalOpen(true)
            }
        } finally {
            setIsPublishing(false)
            toast.dismiss(toast_loading)
        }
    }

    useEffect(() => {
    }, [assignment])

    return (
        <>
            <div className='mx-4 mt-4 flex flex-wrap items-center justify-center gap-2 md:mx-auto md:mt-5 md:gap-4'>
                <div className={`flex text-xs rounded-full px-3.5 py-2 mx-auto font-bold outline outline-1 ${!isPublished ? 'outline-gray-300 bg-gray-200/60' : 'outline-green-300 bg-green-200/60'}`}>
                    {isPublished ? t('dashboard.assignments.detail.publishing.published') : t('dashboard.assignments.detail.publishing.unpublished')}
                </div>
                <div className="hidden md:block"><EllipsisVertical className='text-gray-500' size={13} /></div>

                <ToolTip
                    side='left'
                    slateBlack
                    sideOffset={10}
                    content={t('dashboard.assignments.detail.publishing.edit_tooltip')}>
                    <button
                        type="button"
                        onClick={() => !isPublishing && setIsEditModalOpen(true)}
                        disabled={isPublishing}
                        className={`flex px-3 py-2 rounded-md space-x-2 items-center bg-linear-to-bl text-blue-800 font-medium from-blue-400/50 to-blue-200/80 border border-blue-600/10 shadow-blue-900/10 shadow-lg ${isPublishing ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
                        <Pencil size={18} />
                        <p className='text-sm font-bold'>{t('dashboard.assignments.detail.publishing.edit')}</p>
                    </button>
                </ToolTip>

                <ToolTip
                    side='left'
                    slateBlack
                    sideOffset={10}
                    content={t('dashboard.assignments.detail.publishing.preview_tooltip')} >
                    <Link
                        target='_blank'
                        href={`/course/${assignment?.course_object?.course_uuid.replace('course_', '')}/activity/${assignment?.activity_object?.activity_uuid.replace('activity_', '')}`}
                        className='flex px-3 py-2 cursor-pointer rounded-md space-x-2 items-center bg-linear-to-bl text-cyan-800 font-medium from-sky-400/50 to-cyan-200/80  border border-cyan-600/10 shadow-cyan-900/10 shadow-lg'>
                        <Eye size={18} />
                        <p className=' text-sm font-bold'>{t('dashboard.assignments.detail.publishing.preview')}</p>
                    </Link>
                </ToolTip>
                {isPublished && <ToolTip
                    side='left'
                    slateBlack
                    sideOffset={10}
                    content={t('dashboard.assignments.detail.publishing.unpublish_tooltip')} >
                    <button
                        type="button"
                        onClick={() => updateAssignmentPublishState(assignment?.assignment_object?.assignment_uuid)}
                        disabled={isPublishing}
                        className={`flex px-3 py-2 rounded-md space-x-2 items-center bg-linear-to-bl text-gray-800 font-medium from-gray-400/50 to-gray-200/80 border border-gray-600/10 shadow-gray-900/10 shadow-lg ${isPublishing ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
                        <BookX size={18} />
                        <p className='text-sm font-bold'>{t('dashboard.assignments.detail.publishing.unpublish')}</p>
                    </button>
                </ToolTip>}
                {!isPublished &&
                    <ToolTip
                        side='left'
                        slateBlack
                        sideOffset={10}
                        content={publishBlockReason || t('dashboard.assignments.detail.publishing.publish_tooltip')} >
                        <button
                            type="button"
                            onClick={() => updateAssignmentPublishState(assignment?.assignment_object?.assignment_uuid)}
                            disabled={publishDisabled}
                            className={`flex px-3 py-2 rounded-md space-x-2 items-center bg-linear-to-bl text-green-800 font-medium from-green-400/50 to-lime-200/80 border border-green-600/10 shadow-green-900/10 shadow-lg ${publishDisabled ? 'cursor-not-allowed opacity-60 grayscale' : 'cursor-pointer'}`}>
                            <BookOpen size={18} />
                            <p className=' text-sm font-bold'>{publishBlockReason ? '還不能發布' : '發布給學生'}</p>
                        </button>
                    </ToolTip>}
            </div>
            {assignment?.assignment_object && (
                <div className={`mx-4 mt-2 max-w-[560px] rounded-lg border px-3 py-2 text-center text-xs font-semibold md:mx-auto ${publishHintClass(publishHint.tone)}`}>
                    <span>{publishHint.message}</span>
                    {usergroupsQuery.isError && !isPublished && (
                        <button
                            type="button"
                            onClick={() => usergroupsQuery.refetch()}
                            className="ml-2 inline-flex h-7 items-center rounded-lg border border-amber-300 bg-white px-2.5 text-[11px] font-black text-amber-900 hover:border-amber-700"
                        >
                            重新讀取班級
                        </button>
                    )}
                </div>
            )}
            {publishRecoveryMessage && (
                <div className="mx-4 mt-2 max-w-[560px] rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-rose-800 md:mx-auto">
                    <div className="flex items-start gap-2">
                        <AlertCircle size={15} className="mt-0.5 flex-none" />
                        <div className="min-w-0 flex-1">
                            <p className="font-black">發布未完成</p>
                            <p className="mt-0.5 font-semibold leading-relaxed">{publishRecoveryMessage}</p>
                            {recoveryAction && (
                                <div className="mt-2 flex flex-wrap items-center gap-2">
                                    <Link
                                        href={recoveryAction.href}
                                        className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-3 text-xs font-bold text-rose-800 ring-1 ring-rose-200 hover:bg-rose-100"
                                    >
                                        {recoveryAction.icon}
                                        {recoveryAction.label}
                                    </Link>
                                    <span className="font-medium text-rose-700">{recoveryAction.detail}</span>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
            {assignment?.assignment_object && (
                <div className="mx-4 mt-2 grid max-w-[560px] grid-cols-3 gap-2 md:mx-auto">
                    <PublishStepChip
                        ready={taskSetupReady}
                        label="題目"
                        detail={taskSetupReady ? `${tasks.length} 題簡單題` : '先出 3 題'}
                    />
                    <PublishStepChip
                        ready={targetSetupReady}
                        label="班級"
                        detail={usergroupsQuery.isLoading
                            ? '讀取中'
                            : orgHasUsergroups
                                ? invalidTargetUsergroupCount > 0
                                ? '需重設'
                                : emptyTargetUsergroupCount > 0
                                ? '空班級'
                                : targetUsergroupCount > 0
                                ? `${targetUsergroupCount} 個班級`
                                : '請先指定'
                            : '不需指定'}
                    />
                    <PublishStepChip
                        ready={isPublished || (!publishBlockReason && taskSetupReady && targetSetupReady)}
                        label="發布"
                        detail={isPublished ? '學生可見' : publishBlockReason ? '還不能發布' : '可以發布'}
                    />
                </div>
            )}
            {isEditModalOpen && (
                <EditAssignmentModal
                    isOpen={isEditModalOpen}
                    onClose={() => setIsEditModalOpen(false)}
                    assignment={{
                        ...assignment?.assignment_object,
                        assignment_tasks: assignment?.assignment_tasks,
                    }}
                    accessToken={access_token}
                />
            )}
        </>
    )
}

function PublishStepChip({
    ready,
    label,
    detail,
}: {
    ready: boolean
    label: string
    detail: string
}) {
    return (
        <div className={`rounded-lg border px-3 py-2 ${ready ? 'border-emerald-100 bg-emerald-50 text-emerald-800' : 'border-amber-100 bg-amber-50 text-amber-800'}`}>
            <div className="flex items-center justify-center gap-1.5">
                {ready ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                <span className="text-[11px] font-black">{label}</span>
            </div>
            <p className="mt-0.5 truncate text-center text-[10px] font-semibold opacity-80">
                {detail}
            </p>
        </div>
    )
}

function AssignmentTitle() {
    const { t } = useTranslation()
    const assignment = useAssignments() as any;
    const name = assignment?.assignment_object?.title;

    return (
        <div className="flex min-w-0 items-baseline gap-2 text-xl font-bold sm:text-2xl">
            <span className="shrink-0 text-gray-400">{t('dashboard.assignments.detail.title_prefix')}</span>
            <span className="max-w-[500px] min-w-0 truncate text-gray-900">{name || '...'}</span>
        </div>
    );
}

// Skeuomorphic badge tokens — vertical gradient + colored ring + colored
// drop shadow + inset white highlight for a soft "raised pill" look. Same
// values used in the assignments dashboard (page.tsx in /dash/assignments)
// so the design language matches across both views.
const BADGE_BASE =
    'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold ring-1 ring-inset whitespace-nowrap';

const BADGE_AMBER =
    'bg-gradient-to-b from-amber-50 to-amber-100 text-amber-700 ring-amber-300/40 shadow-[0_1px_2px_rgba(245,158,11,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]';
const BADGE_CYAN =
    'bg-gradient-to-b from-cyan-50 to-cyan-100 text-cyan-700 ring-cyan-300/40 shadow-[0_1px_2px_rgba(6,182,212,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]';

function AssignmentInfoBadges() {
    const { t } = useTranslation();
    const assignment = useAssignments() as any;
    const obj = assignment?.assignment_object;
    if (!obj) return null;

    const targetUsergroupCount = Array.isArray(obj.target_usergroup_ids)
        ? obj.target_usergroup_ids.filter((id: any) => Number.isFinite(Number(id))).length
        : 0;

    return (
        <div className="flex items-center gap-1.5 flex-wrap">
            {coerceSimplePilotBoolean(obj.auto_grading) && (
                <div className={`${BADGE_BASE} ${BADGE_AMBER}`}>
                    <Zap size={13} />
                    <span>{t('dashboard.assignments.detail.header_badges.auto_grading')}</span>
                </div>
            )}
            {obj.anti_copy_paste && (
                <div className={`${BADGE_BASE} ${BADGE_CYAN}`}>
                    <Shield size={13} />
                    <span>{t('dashboard.assignments.detail.header_badges.anti_copy_paste')}</span>
                </div>
            )}
            {obj.subject && (
                <div className={`${BADGE_BASE} bg-gray-100 text-gray-700 ring-gray-200`}>
                    <span>{obj.subject}</span>
                </div>
            )}
            {obj.grade_level && (
                <div className={`${BADGE_BASE} bg-gray-100 text-gray-700 ring-gray-200`}>
                    <span>{obj.grade_level}</span>
                </div>
            )}
            {obj.unit && (
                <div className={`${BADGE_BASE} bg-gray-100 text-gray-700 ring-gray-200`}>
                    <span>{obj.unit}</span>
                </div>
            )}
            <div className={`${BADGE_BASE} ${targetUsergroupCount > 0 ? 'bg-blue-50 text-blue-700 ring-blue-200' : 'bg-rose-50 text-rose-700 ring-rose-200'}`}>
                <span>{targetUsergroupCount > 0 ? `已指定 ${targetUsergroupCount} 個班級` : '未指定班級'}</span>
            </div>
            {obj.score_policy === 'highest' && (
                <div className={`${BADGE_BASE} bg-emerald-50 text-emerald-700 ring-emerald-200`}>
                    <span>最高分計算</span>
                </div>
            )}
            {obj.teacher_review_required && (
                <div className={`${BADGE_BASE} bg-amber-50 text-amber-700 ring-amber-200`}>
                    <span>老師覆核</span>
                </div>
            )}
        </div>
    );
}
