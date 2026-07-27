import { useLHSession } from '@components/Contexts/LHSessionContext';
import UserAvatar from '@components/Objects/UserAvatar';
import Modal from '@components/Objects/StyledElements/Modal/Modal';
import { getAPIUrl } from '@services/config/config';
import { getUserAvatarMediaDirectory } from '@services/media/media';
import { apiFetch } from '@services/utils/ts/requests';
import { useQuery } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import {
    AlertCircle,
    ArrowUpDown,
    Calendar,
    Check,
    CheckCircle2,
    ChevronDown,
    ClipboardCheck,
    Clock,
    Copy,
    Inbox,
    Loader2,
    RotateCcw,
    Search,
    SendHorizonal,
    UserX,
    Users,
    X,
} from 'lucide-react';
import React, { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import EvaluateAssignment from './Modals/EvaluateAssignment';
import { AssignmentProvider } from '@components/Contexts/Assignments/AssignmentContext';
import { AssignmentsTaskProvider } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import AssignmentSubmissionProvider from '@components/Contexts/Assignments/AssignmentSubmissionContext';
import { useTranslation } from 'react-i18next';

type SortField =
    | 'date'             // when the student submitted
    | 'name'             // student display name
    | 'status'           // LATE → SUBMITTED → GRADED (or reverse)
    | 'grade'            // numeric grade value — highest / lowest first
    | 'needs_grading'    // put LATE/SUBMITTED before GRADED so teachers see what to review
    | 'late_first'       // LATE submissions at the top
    | 'recently_graded'; // GRADED first, then sorted by submission date
type SortDirection = 'asc' | 'desc';
type StatusFilter = 'ALL' | 'MISSING' | 'PENDING' | 'LATE' | 'SUBMITTED' | 'GRADED';

const MISSING_STATUSES = new Set(['NOT_SUBMITTED']);
const WAITING_FOR_STUDENT_STATUSES = new Set(['NOT_SUBMITTED', 'PENDING']);
const SCHOOL_DATE_LOCALE = 'zh-HK';

function AssignmentSubmissionsSubPage({ assignment_uuid }: { assignment_uuid: string }) {
    const { t } = useTranslation();
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;

    const [searchQuery, setSearchQuery] = useState('');
    const [statusFilter, setStatusFilter] = useState<StatusFilter>('ALL');
    const [sortField, setSortField] = useState<SortField>('date');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
    const [sortDropdownOpen, setSortDropdownOpen] = useState(false);
    const [missingCopied, setMissingCopied] = useState(false);

    const {
        data: assignmentSubmissions,
        error: submissionsError,
        isFetching: submissionsFetching,
        isLoading: submissionsLoading,
        refetch: refetchSubmissions,
    } = useQuery({
        queryKey: queryKeys.assignments.allSubmissions(assignment_uuid),
        queryFn: () => apiFetch(`${getAPIUrl()}assignments/assignment_${assignment_uuid}/submissions?limit=500`, access_token),
        enabled: !!(assignment_uuid && access_token),
        // Keep the submissions view in near real-time: poll every 10s so
        // auto-graded submissions show up without a manual page refresh.
        staleTime: 5_000,
        refetchInterval: 10_000,
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
    });
    const submissions = useMemo(
        () => Array.isArray(assignmentSubmissions) ? assignmentSubmissions : [],
        [assignmentSubmissions]
    );

    const stats = useMemo(() => {
        return {
            total: submissions.length,
            missing: submissions.filter((s: any) => MISSING_STATUSES.has(s.submission_status)).length,
            inProgress: submissions.filter((s: any) => s.submission_status === 'PENDING').length,
            late: submissions.filter((s: any) => s.submission_status === 'LATE').length,
            submitted: submissions.filter((s: any) => s.submission_status === 'SUBMITTED').length,
            graded: submissions.filter((s: any) => s.submission_status === 'GRADED').length,
        };
    }, [submissions]);

    const statusFilters: { key: StatusFilter; label: string; count: number; icon: React.ReactNode; activeClass: string }[] = [
        { key: 'ALL', label: t('dashboard.assignments.submissions.filters.all'), count: stats.total, icon: <Users size={13} />, activeClass: 'bg-neutral-600/80 text-white' },
        { key: 'MISSING', label: t('dashboard.assignments.submissions.status.not_submitted'), count: stats.missing, icon: <UserX size={13} />, activeClass: 'bg-slate-700 text-white' },
        { key: 'PENDING', label: t('dashboard.assignments.submissions.status.in_progress'), count: stats.inProgress, icon: <RotateCcw size={13} />, activeClass: 'bg-fuchsia-600/80 text-white' },
        { key: 'LATE', label: t('dashboard.assignments.submissions.status.late'), count: stats.late, icon: <Clock size={13} />, activeClass: 'bg-rose-600/80 text-white' },
        { key: 'SUBMITTED', label: t('dashboard.assignments.submissions.status.submitted'), count: stats.submitted, icon: <SendHorizonal size={13} />, activeClass: 'bg-amber-600/80 text-white' },
        { key: 'GRADED', label: t('dashboard.assignments.submissions.status.graded'), count: stats.graded, icon: <CheckCircle2 size={13} />, activeClass: 'bg-emerald-600/80 text-white' },
    ];

    const sortOptions: { field: SortField; label: string }[] = [
        { field: 'date', label: t('dashboard.assignments.submissions.sort.date') },
        { field: 'name', label: t('dashboard.assignments.submissions.sort.name') },
        { field: 'status', label: t('dashboard.assignments.submissions.sort.status') },
        { field: 'grade', label: t('dashboard.assignments.submissions.sort.grade') },
        { field: 'needs_grading', label: t('dashboard.assignments.submissions.sort.needs_grading') },
        { field: 'late_first', label: t('dashboard.assignments.submissions.sort.late_first') },
        { field: 'recently_graded', label: t('dashboard.assignments.submissions.sort.recently_graded') },
    ];

    const missingRows = useMemo(
        () => submissions.filter((s: any) => MISSING_STATUSES.has(s.submission_status)),
        [submissions]
    );
    const reviewFocus = useMemo(() => buildSubmissionReviewFocus(stats), [stats]);

    async function copyMissingList() {
        if (missingRows.length === 0) {
            toast(t('dashboard.assignments.submissions.copy_missing_empty'));
            return;
        }

        const listText = missingRows
            .map((row: any, index: number) => {
                const name = row.student_name || row.student_username || `學生 ${row.user_id}`;
                const email = row.student_email ? ` (${row.student_email})` : '';
                return `${index + 1}. ${name}${email}`;
            })
            .join('\n');

        try {
            await navigator.clipboard.writeText(listText);
            setMissingCopied(true);
            toast.success(t('dashboard.assignments.submissions.copy_missing_success'));
            window.setTimeout(() => setMissingCopied(false), 1800);
        } catch {
            toast.error(t('dashboard.assignments.submissions.copy_missing_error'));
        }
    }

    return (
        <div className="flex flex-col w-full h-full custom-dots-bg">
            <div className="px-10 pt-6 pb-4 flex flex-col space-y-4">
                {/* Stats row */}
                <div className="flex flex-wrap gap-3">
                    <div className="bg-white nice-shadow rounded-xl px-4 py-3 flex items-center space-x-3">
                        <div className="bg-gray-100 rounded-lg p-1.5">
                            <Users size={14} className="text-gray-600" />
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">{t('dashboard.assignments.submissions.stats.total')}</p>
                            <p className="text-lg font-bold text-gray-900 -mt-0.5">{stats.total}</p>
                        </div>
                    </div>
                    <div className="bg-white nice-shadow rounded-xl px-4 py-3 flex items-center space-x-3">
                        <div className="bg-slate-100 rounded-lg p-1.5">
                            <UserX size={14} className="text-slate-600" />
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">{t('dashboard.assignments.submissions.status.not_submitted')}</p>
                            <p className="text-lg font-bold text-gray-900 -mt-0.5">{stats.missing}</p>
                        </div>
                    </div>
                    <div className="bg-white nice-shadow rounded-xl px-4 py-3 flex items-center space-x-3">
                        <div className="bg-fuchsia-50 rounded-lg p-1.5">
                            <RotateCcw size={14} className="text-fuchsia-600" />
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">{t('dashboard.assignments.submissions.status.in_progress')}</p>
                            <p className="text-lg font-bold text-gray-900 -mt-0.5">{stats.inProgress}</p>
                        </div>
                    </div>
                    <div className="bg-white nice-shadow rounded-xl px-4 py-3 flex items-center space-x-3">
                        <div className="bg-rose-50 rounded-lg p-1.5">
                            <Clock size={14} className="text-rose-600" />
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">{t('dashboard.assignments.submissions.status.late')}</p>
                            <p className="text-lg font-bold text-gray-900 -mt-0.5">{stats.late}</p>
                        </div>
                    </div>
                    <div className="bg-white nice-shadow rounded-xl px-4 py-3 flex items-center space-x-3">
                        <div className="bg-amber-50 rounded-lg p-1.5">
                            <SendHorizonal size={14} className="text-amber-600" />
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">{t('dashboard.assignments.submissions.status.submitted')}</p>
                            <p className="text-lg font-bold text-gray-900 -mt-0.5">{stats.submitted}</p>
                        </div>
                    </div>
                    <div className="bg-white nice-shadow rounded-xl px-4 py-3 flex items-center space-x-3">
                        <div className="bg-emerald-50 rounded-lg p-1.5">
                            <CheckCircle2 size={14} className="text-emerald-600" />
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide font-semibold text-gray-400">{t('dashboard.assignments.submissions.status.graded')}</p>
                            <p className="text-lg font-bold text-gray-900 -mt-0.5">{stats.graded}</p>
                        </div>
                    </div>
                </div>

                <SubmissionReviewFocus
                    focus={reviewFocus}
                    stats={stats}
                    missingCopied={missingCopied}
                    onCopyMissingList={copyMissingList}
                    onShowNeedsReview={() => {
                        setStatusFilter('ALL');
                        setSortField('needs_grading');
                        setSortDirection('asc');
                    }}
                />

                {/* Toolbar */}
                <div className="flex flex-wrap items-center gap-3">
                    {/* Search */}
                    <div className="relative flex-1 max-w-sm">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder={t('dashboard.assignments.submissions.search_placeholder')}
                            className="w-full pl-9 pr-8 py-2 text-sm bg-white nice-shadow rounded-lg focus:outline-none focus:ring-2 focus:ring-black/5 placeholder:text-gray-400"
                        />
                        {searchQuery && (
                            <button
                                onClick={() => setSearchQuery('')}
                                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                            >
                                <X size={14} />
                            </button>
                        )}
                    </div>

                    {/* Status filter pills */}
                    <div className="flex flex-wrap gap-1.5">
                        {statusFilters.map((filter) => (
                            <button
                                key={filter.key}
                                onClick={() => setStatusFilter(filter.key)}
                                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
                                    statusFilter === filter.key
                                        ? filter.activeClass
                                        : 'bg-white nice-shadow text-gray-600 hover:bg-gray-50'
                                }`}
                            >
                                {filter.icon}
                                <span>{filter.label}</span>
                                <span className={`${statusFilter === filter.key ? 'bg-white/20' : 'bg-gray-100'} px-1.5 py-0.5 rounded-full text-[10px] font-bold`}>
                                    {filter.count}
                                </span>
                            </button>
                        ))}
                    </div>

                    <button
                        type="button"
                        onClick={copyMissingList}
                        disabled={missingRows.length === 0}
                        className="flex items-center space-x-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 bg-white nice-shadow rounded-full hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {missingCopied ? <Check size={12} /> : <Copy size={12} />}
                        <span>{t('dashboard.assignments.submissions.copy_missing')}</span>
                    </button>

                    {/* Sort */}
                    <div className="relative">
                        <button
                            onClick={() => setSortDropdownOpen(!sortDropdownOpen)}
                            className="flex items-center space-x-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 bg-white nice-shadow rounded-full hover:bg-gray-50 transition-colors"
                        >
                            <ArrowUpDown size={12} />
                            <span>{t('dashboard.assignments.submissions.sort.label')}</span>
                            <ChevronDown size={11} className={`transition-transform ${sortDropdownOpen ? 'rotate-180' : ''}`} />
                        </button>
                        {sortDropdownOpen && (
                            <>
                                <div className="fixed inset-0 z-10" onClick={() => setSortDropdownOpen(false)} />
                                <div className="absolute right-0 top-full mt-2 z-20 bg-white nice-shadow rounded-xl py-1.5 min-w-[150px]">
                                    {sortOptions.map((option) => (
                                        <button
                                            key={option.field}
                                            onClick={() => {
                                                if (sortField === option.field) {
                                                    setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
                                                } else {
                                                    setSortField(option.field);
                                                    setSortDirection(
                                                        ['status', 'needs_grading', 'late_first', 'recently_graded'].includes(option.field)
                                                            ? 'asc'
                                                            : 'desc'
                                                    );
                                                }
                                                setSortDropdownOpen(false);
                                            }}
                                            className={`w-full px-3 py-1.5 text-xs text-left flex items-center justify-between hover:bg-gray-50 ${
                                                sortField === option.field ? 'text-gray-900 font-bold' : 'text-gray-500 font-medium'
                                            }`}
                                        >
                                            <span>{option.label}</span>
                                            {sortField === option.field && (
                                                <span className="text-gray-400 text-[10px]">
                                                    {sortDirection === 'asc' ? '↑' : '↓'}
                                                </span>
                                            )}
                                        </button>
                                    ))}
                                </div>
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* Submissions list */}
            <div className="flex-1 overflow-y-auto px-10 pb-6">
                <SubmissionsList
                    submissions={submissions}
                    assignment_uuid={assignment_uuid}
                    searchQuery={searchQuery}
                    statusFilter={statusFilter}
                    sortField={sortField}
                    sortDirection={sortDirection}
                    isLoading={submissionsLoading}
                    error={submissionsError as any}
                    isRetrying={submissionsFetching}
                    onRetry={() => refetchSubmissions()}
                />
            </div>
        </div>
    );
}

function buildSubmissionReviewFocus(stats: {
    total: number;
    missing: number;
    inProgress: number;
    late: number;
    submitted: number;
    graded: number;
}) {
    const needsReview = stats.late + stats.submitted;
    if (stats.total <= 0) {
        return {
            tone: 'gray' as const,
            title: '暫時沒有學生記錄',
            detail: '發布到班級後，學生提交、自動批改和老師覆核狀態會出現在這裡。',
            primaryLabel: '等待學生提交',
        };
    }
    if (needsReview > 0) {
        return {
            tone: 'amber' as const,
            title: `先批改 ${needsReview} 份已提交作業`,
            detail: '批改完成後，成績表和校長摘要會立即更新，這是最有展示價值的下一步。',
            primaryLabel: '待批改優先',
        };
    }
    if (stats.missing > 0) {
        return {
            tone: 'rose' as const,
            title: `還有 ${stats.missing} 名學生未提交`,
            detail: '先複製未交名單提醒學生。未交清楚列出來，老師和校長都容易跟進。',
            primaryLabel: '複製未交名單',
        };
    }
    if (stats.inProgress > 0) {
        return {
            tone: 'blue' as const,
            title: `${stats.inProgress} 名學生正在重做`,
            detail: '學生答錯後可以修改再提交，等重做完成後再看最高分和改善情況。',
            primaryLabel: '等待重新提交',
        };
    }
    return {
        tone: 'emerald' as const,
        title: '這份作業閉環已跑順',
        detail: `已有 ${stats.graded} 份完成批改。可以到成績表查看全班分數、提交率和待跟進記錄。`,
        primaryLabel: '已完成',
    };
}

function SubmissionReviewFocus({
    focus,
    stats,
    missingCopied,
    onCopyMissingList,
    onShowNeedsReview,
}: {
    focus: ReturnType<typeof buildSubmissionReviewFocus>;
    stats: {
        total: number;
        missing: number;
        inProgress: number;
        late: number;
        submitted: number;
        graded: number;
    };
    missingCopied: boolean;
    onCopyMissingList: () => void;
    onShowNeedsReview: () => void;
}) {
    const toneClass = {
        amber: {
            wrap: 'border-amber-100 bg-amber-50/70',
            icon: 'bg-amber-600 text-white',
            text: 'text-amber-800',
            button: 'border-amber-200 bg-white text-amber-800 hover:bg-amber-50',
        },
        rose: {
            wrap: 'border-rose-100 bg-rose-50/70',
            icon: 'bg-rose-600 text-white',
            text: 'text-rose-800',
            button: 'border-rose-200 bg-white text-rose-800 hover:bg-rose-50',
        },
        blue: {
            wrap: 'border-blue-100 bg-blue-50/70',
            icon: 'bg-blue-600 text-white',
            text: 'text-blue-800',
            button: 'border-blue-200 bg-white text-blue-800 hover:bg-blue-50',
        },
        emerald: {
            wrap: 'border-emerald-100 bg-emerald-50/70',
            icon: 'bg-emerald-600 text-white',
            text: 'text-emerald-800',
            button: 'border-emerald-200 bg-white text-emerald-800 hover:bg-emerald-50',
        },
        gray: {
            wrap: 'border-gray-100 bg-white',
            icon: 'bg-gray-900 text-white',
            text: 'text-gray-700',
            button: 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50',
        },
    }[focus.tone];
    const needsReview = stats.late + stats.submitted;
    const canCopyMissing = stats.missing > 0;

    return (
        <section className={`rounded-xl border px-4 py-3 ${toneClass.wrap}`}>
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex items-start gap-3">
                    <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${toneClass.icon}`}>
                        {focus.tone === 'emerald'
                            ? <CheckCircle2 size={17} />
                            : focus.tone === 'rose'
                                ? <UserX size={17} />
                                : focus.tone === 'blue'
                                    ? <RotateCcw size={17} />
                                    : <ClipboardCheck size={17} />}
                    </span>
                    <div>
                        <p className="text-sm font-black text-gray-950">{focus.title}</p>
                        <p className={`mt-1 text-xs font-semibold leading-relaxed ${toneClass.text}`}>{focus.detail}</p>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2">
                    {needsReview > 0 && (
                        <button
                            type="button"
                            className={`inline-flex h-9 items-center justify-center rounded-lg border px-3 text-xs font-black ${toneClass.button}`}
                            onClick={onShowNeedsReview}
                        >
                            {focus.primaryLabel}
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={onCopyMissingList}
                        disabled={!canCopyMissing}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-xs font-black text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {missingCopied ? <Check size={13} /> : <Copy size={13} />}
                        複製未交名單
                    </button>
                </div>
            </div>
        </section>
    );
}

function SubmissionsList({
    submissions,
    assignment_uuid,
    searchQuery,
    statusFilter,
    sortField,
    sortDirection,
    isLoading,
    error,
    isRetrying,
    onRetry,
}: {
    submissions: any[];
    assignment_uuid: string;
    searchQuery: string;
    statusFilter: StatusFilter;
    sortField: SortField;
    sortDirection: SortDirection;
    isLoading: boolean;
    error?: Error | null;
    isRetrying?: boolean;
    onRetry?: () => void;
}) {
    const { t } = useTranslation();

    if (error) {
        return (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-5 text-sm font-semibold text-amber-800">
                <div className="flex items-start gap-2">
                    <AlertCircle size={18} className="mt-0.5 shrink-0" />
                    <div>
                        <p>提交記錄載入失敗，請稍後再試。</p>
                        {error.message && <p className="mt-1 text-xs font-medium text-amber-700">{error.message}</p>}
                        {onRetry && (
                            <button
                                type="button"
                                onClick={onRetry}
                                disabled={isRetrying}
                                className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-3 text-xs font-black text-amber-800 ring-1 ring-inset ring-amber-200 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                {isRetrying ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                                {isRetrying ? '載入中' : '重新載入提交記錄'}
                            </button>
                        )}
                    </div>
                </div>
            </div>
        );
    }

    if (isLoading) {
        return (
            <div className="bg-white nice-shadow rounded-xl overflow-hidden animate-pulse">
                {[1, 2, 3, 4, 5].map((i) => (
                    <div key={i} className="flex items-center px-5 py-3.5 border-b border-gray-100 last:border-0 gap-3">
                        <div className="w-9 h-9 rounded-full bg-gray-100 flex-shrink-0" />
                        <div className="flex-1 space-y-1.5">
                            <div className="h-3.5 bg-gray-100 rounded w-1/3" />
                            <div className="h-3 bg-gray-100 rounded w-1/4" />
                        </div>
                        <div className="h-6 w-20 bg-gray-100 rounded-full" />
                    </div>
                ))}
            </div>
        );
    }

    // Priority tables used by the status-based sorts. Lower numbers come first
    // when `sortDirection === 'asc'`.
    const statusOrder: Record<string, number> = { NOT_SUBMITTED: 0, PENDING: 0, LATE: 1, SUBMITTED: 2, GRADED: 3 };
    // needsGradingOrder: ungraded (LATE + SUBMITTED) before GRADED
    const needsGradingOrder: Record<string, number> = { LATE: 0, SUBMITTED: 0, NOT_SUBMITTED: 1, PENDING: 1, GRADED: 2 };
    // lateFirstOrder: LATE first, everything else after
    const lateFirstOrder: Record<string, number> = { LATE: 0, SUBMITTED: 1, NOT_SUBMITTED: 2, PENDING: 2, GRADED: 3 };
    // recentlyGradedOrder: GRADED first, then everything else
    const recentlyGradedOrder: Record<string, number> = { GRADED: 0, LATE: 1, SUBMITTED: 1, NOT_SUBMITTED: 2, PENDING: 2 };

    const dateMs = (s: any) => {
        const timestamp = new Date(s.update_date || s.creation_date || 0).getTime();
        return Number.isFinite(timestamp) ? timestamp : 0;
    };

    const filtered = submissions
        .filter((s: any) => {
            if (statusFilter === 'MISSING') return MISSING_STATUSES.has(s.submission_status);
            if (statusFilter !== 'ALL' && s.submission_status !== statusFilter) return false;
            return true;
        })
        .sort((a: any, b: any) => {
            let cmp = 0;
            if (sortField === 'date') {
                cmp = dateMs(a) - dateMs(b);
            } else if (sortField === 'status') {
                cmp = (statusOrder[a.submission_status] ?? 1) - (statusOrder[b.submission_status] ?? 1);
                // Tiebreak by date so stable output in groups
                if (cmp === 0) cmp = dateMs(a) - dateMs(b);
            } else if (sortField === 'grade') {
                // Sort by numeric grade. Ungraded rows (grade === 0 AND status !== GRADED)
                // sink to the bottom regardless of direction so teachers see the
                // actually-graded ones first.
                const aGraded = a.submission_status === 'GRADED';
                const bGraded = b.submission_status === 'GRADED';
                if (aGraded !== bGraded) return aGraded ? -1 : 1;
                cmp = (Number(a.grade) || 0) - (Number(b.grade) || 0);
                if (cmp === 0) cmp = dateMs(a) - dateMs(b);
            } else if (sortField === 'needs_grading') {
                cmp = (needsGradingOrder[a.submission_status] ?? 1) - (needsGradingOrder[b.submission_status] ?? 1);
                // Inside the "needs grading" bucket, show oldest first (they've
                // been waiting longest). Inside the "graded" bucket, newest first.
                if (cmp === 0) {
                    const oldestFirst = a.submission_status !== 'GRADED';
                    cmp = oldestFirst ? dateMs(a) - dateMs(b) : dateMs(b) - dateMs(a);
                }
            } else if (sortField === 'late_first') {
                cmp = (lateFirstOrder[a.submission_status] ?? 1) - (lateFirstOrder[b.submission_status] ?? 1);
                if (cmp === 0) cmp = dateMs(a) - dateMs(b);
            } else if (sortField === 'recently_graded') {
                cmp = (recentlyGradedOrder[a.submission_status] ?? 1) - (recentlyGradedOrder[b.submission_status] ?? 1);
                // Inside the graded bucket, newest updates first
                if (cmp === 0) cmp = dateMs(b) - dateMs(a);
            } else {
                // name and any unknown: delegate to date as a safe fallback
                // (name sort happens inside the row component since user data
                // is fetched there)
                cmp = dateMs(a) - dateMs(b);
            }
            return sortDirection === 'asc' ? cmp : -cmp;
        });

    if (filtered.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center py-16 text-gray-400 gap-3">
                <div className="bg-gray-100 rounded-2xl p-4">
                    <Inbox size={24} />
                </div>
                <p className="text-sm font-semibold">{t('dashboard.assignments.submissions.empty')}</p>
            </div>
        );
    }

    return (
        <div className="bg-white nice-shadow rounded-xl overflow-hidden">
            {filtered.map((submission: any, index: number) => (
                <SubmissionRow
                    key={submission.assignmentusersubmission_uuid || submission.id}
                    submission={submission}
                    assignment_uuid={assignment_uuid}
                    searchQuery={searchQuery}
                    isLast={index === filtered.length - 1}
                />
            ))}
        </div>
    );
}

function SubmissionRow({
    assignment_uuid,
    submission,
    searchQuery,
    isLast,
}: {
    assignment_uuid: string;
    submission: any;
    searchQuery: string;
    isLast: boolean;
}) {
    const { t } = useTranslation();
    const session = useLHSession() as any;
    const access_token = session?.data?.tokens?.access_token;
    const [gradeModalOpen, setGradeModalOpen] = useState(false);

    const { data: user } = useQuery({
        queryKey: ['users', 'id', submission.user_id],
        queryFn: () => apiFetch(`${getAPIUrl()}users/id/${submission.user_id}`, access_token),
        enabled: !!(submission.user_id && access_token && !submission.student_email),
        staleTime: 60_000,
    });

    const displayUser = {
        first_name: user?.first_name,
        last_name: user?.last_name,
        username: submission.student_username || user?.username,
        email: submission.student_email || user?.email,
        user_uuid: submission.student_user_uuid || user?.user_uuid,
        avatar_image: submission.student_avatar_image || user?.avatar_image,
        name: submission.student_name,
    };

    const matchesSearch = useMemo(() => {
        if (!searchQuery) return true;
        if (!displayUser.email && !displayUser.username && !displayUser.name) return true;
        const q = searchQuery.toLowerCase();
        const fullName = (displayUser.name || `${displayUser.first_name || ''} ${displayUser.last_name || ''}`).toLowerCase();
        const username = (displayUser.username || '').toLowerCase();
        const email = (displayUser.email || '').toLowerCase();
        return fullName.includes(q) || username.includes(q) || email.includes(q);
    }, [searchQuery, displayUser.email, displayUser.first_name, displayUser.last_name, displayUser.name, displayUser.username]);

    if (!matchesSearch) return null;

    const statusConfig: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
        LATE: {
            label: t('dashboard.assignments.submissions.status.late'),
            className: 'bg-rose-50 text-rose-700',
            icon: <Clock size={11} />,
        },
        NOT_SUBMITTED: {
            label: t('dashboard.assignments.submissions.status.not_submitted'),
            className: 'bg-slate-100 text-slate-700',
            icon: <UserX size={11} />,
        },
        PENDING: {
            label: t('dashboard.assignments.submissions.status.in_progress'),
            className: 'bg-slate-100 text-slate-700',
            icon: <RotateCcw size={11} />,
        },
        SUBMITTED: {
            label: t('dashboard.assignments.submissions.status.submitted'),
            className: 'bg-amber-50 text-amber-700',
            icon: <SendHorizonal size={11} />,
        },
        GRADED: {
            label: t('dashboard.assignments.submissions.status.graded'),
            className: 'bg-emerald-50 text-emerald-700',
            icon: <CheckCircle2 size={11} />,
        },
    };

    const status = statusConfig[submission.submission_status] || statusConfig['SUBMITTED'];
    const isWaitingForStudent = WAITING_FOR_STUDENT_STATUSES.has(submission.submission_status);
    const bestScoreLabel = assignmentBestScoreLabel(submission);
    const submittedDate = new Date(submission.update_date || submission.creation_date || 0);
    const hasValidDate = !isWaitingForStudent && Number.isFinite(submittedDate.getTime());
    const dateStr = hasValidDate
        ? submittedDate.toLocaleDateString(SCHOOL_DATE_LOCALE, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
        })
        : submission.submission_status === 'PENDING'
            ? t('dashboard.assignments.submissions.status.in_progress')
            : t('dashboard.assignments.submissions.not_submitted_date');
    const timeStr = hasValidDate
        ? submittedDate.toLocaleTimeString(SCHOOL_DATE_LOCALE, {
            hour: '2-digit',
            minute: '2-digit',
        })
        : '';

    return (
        <div className={`flex items-center px-5 py-3.5 hover:bg-gray-50/60 transition-colors group ${!isLast ? 'border-b border-gray-100' : ''}`}>
            {/* User info */}
            <div className="flex items-center space-x-3 flex-1 min-w-0">
                <UserAvatar
                    border="border-2"
                    avatar_url={getUserAvatarMediaDirectory(displayUser.user_uuid, displayUser.avatar_image)}
                    predefined_avatar={displayUser.avatar_image ? undefined : 'empty'}
                    width={36}
                />
                <div className="min-w-0">
                    <p className="text-sm font-semibold text-gray-900 truncate">
                        {displayUser.name
                            ? displayUser.name
                            : displayUser.first_name && displayUser.last_name
                                ? `${displayUser.first_name} ${displayUser.last_name}`
                                : displayUser.username
                                ? `@${displayUser.username}`
                                : '...'}
                    </p>
                    <p className="text-xs text-gray-400 truncate">{displayUser.email}</p>
                </div>
            </div>

            {/* Grade — show the computed display_grade (e.g. "B", "85/100",
                "Pass") so the list matches the evaluate modal and the
                student's own view instead of showing a naked raw sum. */}
            {submission.submission_status === 'GRADED' && (
                <div className="flex items-center space-x-1.5 mr-5">
                    <span className="bg-gray-100 text-gray-500 text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full">{t('dashboard.assignments.submissions.grade_label')}</span>
                    <span className={`text-sm font-bold ${
                        submission.grade_display
                            ? (submission.grade_display.passed ? 'text-emerald-700' : 'text-rose-700')
                            : 'text-gray-900'
                    }`}>
                        {submission.grade_display?.display_grade ?? submission.grade}
                    </span>
                    {submission.grade_display?.points_summary && (
                        <span className="text-[10px] text-gray-400 font-medium">
                            {submission.grade_display.points_summary}
                        </span>
                    )}
                </div>
            )}

            {/* Date */}
            <div className="flex items-center space-x-1.5 mr-5 text-gray-400">
                {hasValidDate
                    ? <Calendar size={12} />
                    : submission.submission_status === 'PENDING'
                        ? <RotateCcw size={12} />
                        : <UserX size={12} />}
                <span className="text-xs font-medium">{dateStr}</span>
                {timeStr && (
                    <>
                        <span className="text-[10px] text-gray-300">|</span>
                        <span className="text-xs text-gray-300">{timeStr}</span>
                    </>
                )}
            </div>

            {/* Status badge */}
            <div className={`flex items-center space-x-1 px-2.5 py-1 rounded-full mr-4 text-xs font-semibold ${status.className}`}>
                {status.icon}
                <span>{status.label}</span>
            </div>

            {/* Attempt indicator — only shown when the student is past the
                first attempt so the row stays uncluttered for the common
                case of a single submission. */}
            {submission.attempt_number && submission.attempt_number > 1 && (
                <div className="flex items-center space-x-1 px-2.5 py-1 rounded-full mr-4 text-xs font-semibold bg-fuchsia-50 text-fuchsia-700">
                    <RotateCcw size={11} />
                    <span>{t('assignments.attempt_label')} {submission.attempt_number}</span>
                </div>
            )}

            {bestScoreLabel && (
                <div className="flex items-center space-x-1 px-2.5 py-1 rounded-full mr-4 text-xs font-semibold bg-emerald-50 text-emerald-700">
                    <CheckCircle2 size={11} />
                    <span>{bestScoreLabel}</span>
                </div>
            )}

            {isWaitingForStudent ? (
                <div className="bg-gray-100 text-gray-500 font-bold py-1.5 px-3.5 rounded-md text-xs">
                    {submission.submission_status === 'PENDING'
                        ? t('dashboard.assignments.submissions.waiting_for_resubmission')
                        : t('dashboard.assignments.submissions.waiting_for_submission')}
                </div>
            ) : (
                <Modal
                    isDialogOpen={gradeModalOpen}
                    onOpenChange={(open: boolean) => setGradeModalOpen(open)}
                    minHeight="lg"
                    minWidth="lg"
                    dialogContent={
                        <AssignmentProvider assignment_uuid={'assignment_' + assignment_uuid}>
                            <AssignmentsTaskProvider>
                                <AssignmentSubmissionProvider assignment_uuid={'assignment_' + assignment_uuid}>
                                    <EvaluateAssignment user_id={submission.user_id} />
                                </AssignmentSubmissionProvider>
                            </AssignmentsTaskProvider>
                        </AssignmentProvider>
                    }
                    dialogTitle={t('dashboard.assignments.submissions.evaluate_modal.title', { username: displayUser.username })}
                    dialogDescription={t('dashboard.assignments.submissions.evaluate_modal.description')}
                    dialogTrigger={
                        <div className="bg-black hover:bg-gray-800 text-white font-bold py-1.5 px-3.5 rounded-md text-xs cursor-pointer nice-shadow transition-colors">
                            {t('dashboard.assignments.submissions.evaluate')}
                        </div>
                    }
                />
            )}
        </div>
    );
}

function numericOrNull(value: any) {
    const numberValue = Number(value);
    return Number.isFinite(numberValue) ? numberValue : null;
}

function assignmentBestScoreLabel(submission: any) {
    const bestGrade = numericOrNull(submission?.best_grade);
    const maxGrade = numericOrNull(submission?.max_grade);
    const bestAttemptNumber = numericOrNull(submission?.best_attempt_number);
    if (
        bestGrade === null
        || maxGrade === null
        || maxGrade <= 0
        || !bestAttemptNumber
        || bestAttemptNumber <= 0
        || submission?.score_policy !== 'highest'
    ) {
        return '';
    }
    return `最高分 ${bestGrade}/${maxGrade}`;
}

export default AssignmentSubmissionsSubPage;
