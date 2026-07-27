'use client'
import React from 'react'
import Link from 'next/link'
import {
  PlusCircle,
  ChartBar,
  GearSix,
  Users,
  BookOpen,
} from '@phosphor-icons/react'
import {
  AlertCircle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  ClipboardCheck,
  Copy,
  ListChecks,
  NotebookTabs,
  RotateCcw,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { formatZhHkDate } from '@lib/date-format'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrgMembership } from '@components/Contexts/OrgContext'
import { getAPIUrl, getUriWithOrg } from '@services/config/config'
import { OrgUsageResponse, orgUsageFetcher } from '@services/orgs/usage'
import {
  getSchoolOperationsSummary,
  getTeacherAssignmentWorkbench,
} from '@services/courses/assignments'
import AdminAuthorization from '@components/Security/AdminAuthorization'
import { usePlan } from '@components/Hooks/usePlan'
import QuickStats from './QuickStats'
import RecentCourses from './RecentCourses'
import RecentMembers from './RecentMembers'
import ContentOverview from './ContentOverview'
import UsageOverview from './UsageOverview'
import toast from 'react-hot-toast'

const PLAN_COLORS: Record<string, { bg: string; text: string }> = {
  free: { bg: 'bg-gray-100', text: 'text-gray-600' },
  oss: { bg: 'bg-emerald-100', text: 'text-emerald-700' },
  standard: { bg: 'bg-blue-100', text: 'text-blue-700' },
  pro: { bg: 'bg-purple-100', text: 'text-purple-700' },
  enterprise: { bg: 'bg-amber-100', text: 'text-amber-700' },
}

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.detail ?? response?.message
  if (typeof detail === 'string') return detail
  if (detail && typeof detail.message === 'string') return detail.message
  return fallback
}

function pilotReadyStatus(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function pilotReadyStatusLabel(value: boolean | null) {
  if (value === true) return '可用'
  if (value === false) return '未配置完整'
  return '未確認'
}

function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([`\uFEFF${text}`], { type: 'text/plain;charset=utf-8' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

async function copyTextOrDownload(text: string, filename: string) {
  try {
    await navigator.clipboard.writeText(text)
    return 'copied'
  } catch {
    downloadTextFile(filename, text)
    return 'downloaded'
  }
}

export default function DashboardHome() {
  const { t } = useTranslation()
  const session = useLHSession() as any
  const { org, orgslug: contextOrgSlug } = useOrgMembership() as any

  const token = session?.data?.tokens?.access_token
  const orgId = org?.id
  const orgslug = org?.slug || contextOrgSlug || ''
  const username = session?.data?.user?.username || ''

  // TanStack Query will dedupe with UsageOverview's identical call via shared queryKey
  const { data: usageData } = useQuery<OrgUsageResponse>({
    queryKey: queryKeys.org.usage(orgId),
    queryFn: () => orgUsageFetcher(`${getAPIUrl()}orgs/${orgId}/usage`, token),
    enabled: !!token && !!orgId,
    staleTime: 60_000,
  })

  const {
    data: workbench,
    error: workbenchError,
    isFetching: workbenchFetching,
    isLoading: workbenchLoading,
    refetch: refetchWorkbench,
  } = useQuery({
    queryKey: orgId ? queryKeys.assignments.workbench(orgId) : ['assignments', 'workbench'],
    queryFn: async () => {
      const response = await getTeacherAssignmentWorkbench(orgId, token)
      if (response?.success === false) {
        throw new Error(responseErrorMessage(response, '作業工作台載入失敗'))
      }
      return response?.data || null
    },
    enabled: !!token && !!orgId,
    staleTime: 45_000,
  })

  const {
    data: operationsSummary,
    error: operationsError,
    isFetching: operationsFetching,
    isLoading: operationsLoading,
    refetch: refetchOperations,
  } = useQuery({
    queryKey: orgId ? queryKeys.assignments.operationsSummary(orgId, 'current-week') : ['assignments', 'operations-summary'],
    queryFn: async () => {
      const response = await getSchoolOperationsSummary(orgId, token)
      if (response?.success === false) {
        throw new Error(responseErrorMessage(response, '本週營運摘要載入失敗'))
      }
      return response?.data || null
    },
    enabled: !!token && !!orgId,
    staleTime: 60_000,
  })

  const plan = usePlan()
  const planStyle = PLAN_COLORS[plan] || PLAN_COLORS.free

  return (
    <div className="h-full w-full bg-[#f8f8f8]">
      <div className="px-4 sm:px-10 pt-8 pb-10">
        <div className="space-y-6 max-w-[1600px] mx-auto w-full">
          {/* Welcome Header */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">
                {t('dashboard.home.welcome_back')}{username ? `, ${username}` : ''}
              </h1>
              <div className="flex items-center gap-2 mt-1.5">
                <span
                  className={`text-[11px] font-semibold px-2.5 py-0.5 rounded-full capitalize ${planStyle.bg} ${planStyle.text}`}
                >
                  {plan === 'oss' ? 'OSS' : `${plan} ${t('dashboard.home.plan')}`}
                </span>
                {org?.name && (
                  <span className="text-xs text-gray-400">{org.name}</span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <Link
                href={getUriWithOrg(orgslug, '/dash/courses?new=true')}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium text-white bg-gray-900 rounded-lg hover:bg-gray-800 transition-colors"
              >
                <PlusCircle size={14} weight="bold" />
                {t('dashboard.home.create_course')}
              </Link>
              <Link
                href={getUriWithOrg(orgslug, '/dash/analytics')}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium text-gray-600 bg-white rounded-lg nice-shadow hover:bg-gray-50 transition-colors"
              >
                <ChartBar size={14} weight="bold" />
                {t('dashboard.home.analytics')}
              </Link>
              <Link
                href={getUriWithOrg(orgslug, '/dash/users/settings/users')}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium text-gray-600 bg-white rounded-lg nice-shadow hover:bg-gray-50 transition-colors"
              >
                <Users size={14} weight="bold" />
                {t('dashboard.home.members')}
              </Link>
              <Link
                href={getUriWithOrg(orgslug, '/dash/org/settings/general')}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium text-gray-600 bg-white rounded-lg nice-shadow hover:bg-gray-50 transition-colors"
              >
                <GearSix size={14} weight="bold" />
                {t('dashboard.home.settings')}
              </Link>
            </div>
          </div>

          <TeacherWorkbench
            workbench={workbench}
            isLoading={workbenchLoading}
            error={workbenchError as Error | null}
            orgslug={orgslug}
            isRetrying={workbenchFetching}
            onRetry={() => refetchWorkbench()}
            operationsSummary={operationsSummary}
            operationsLoading={operationsLoading}
            operationsError={operationsError as Error | null}
            operationsFetching={operationsFetching}
            onRetryOperations={() => refetchOperations()}
          />

          <AdminAuthorization authorizationMode="component">
            <div className="space-y-6">
              {/* Content counts row */}
              <ContentOverview />

              {/* Main grid: courses + members + usage */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                  <RecentCourses />
                  <RecentMembers />
                </div>
                <div className="space-y-6">
                  <UsageOverview />
                  <QuickStats />
                </div>
              </div>
            </div>
          </AdminAuthorization>
        </div>
      </div>
    </div>
  )
}

function TeacherWorkbench({
  workbench,
  isLoading,
  error,
  orgslug,
  isRetrying,
  onRetry,
  operationsSummary,
  operationsLoading,
  operationsError,
  operationsFetching,
  onRetryOperations,
}: {
  workbench: any
  isLoading: boolean
  error?: Error | null
  orgslug: string
  isRetrying?: boolean
  onRetry?: () => void
  operationsSummary?: any
  operationsLoading?: boolean
  operationsError?: Error | null
  operationsFetching?: boolean
  onRetryOperations?: () => void
}) {
  const summary = workbench?.summary || {}
  const dashboardHref = (path: string) => getUriWithOrg(orgslug, path)
  const pendingReview = summary.pending_review || 0
  const essayPendingReview = summary.essay_pending_review || 0
  const nonEssayPendingReview = Math.max(pendingReview - essayPendingReview, 0)
  const missingSubmissions = summary.expected_unsubmitted || 0
  const dueSoon = summary.due_soon || 0
  const retryInProgress = Number(summary.retry_in_progress || 0)
  const retryRecords = Number(summary.retry_records || 0)
  const selfTestRecords = Number(summary.self_test_rows || 0)
  const passingScoreRecords = Number(summary.passing_score_records || 0)
  const needsPracticeRecords = Number(summary.needs_practice_records || 0)
  const learnerCount = Number(summary.learner_count ?? summary.active_student_count ?? summary.student_count ?? 0)
  const selfTestBankStatus = pilotReadyStatus(summary.self_test_bank_ready)
  const hasWorkbenchError = Boolean(error)
  const publishedNeedingSetup = Number(summary.published_needing_setup || 0)
  const publishedWithoutTargets = Number(summary.published_without_targets || 0)
  const publishedEmptyTargetUsergroups = Number(summary.published_empty_target_usergroups || 0)
  const autoGradedAssignments = Number(
    summary.simple_pilot_ready_assignments
      ?? summary.simple_auto_graded_assignments
      ?? summary.auto_graded_assignments
      ?? 0
  )
  const setupAssignmentAttention = publishedNeedingSetup > 0
    ? publishedNeedingSetup
    : publishedWithoutTargets
  const learnerSetupAttention = learnerCount <= 0 ? 1 : 0
  const setupAttention = (
    (hasWorkbenchError ? 1 : 0)
    + learnerSetupAttention
    + setupAssignmentAttention
  )
  const focusCount = pendingReview + missingSubmissions + dueSoon + setupAttention
  const isAllClear = focusCount === 0
  const setupAttentionDetail = hasWorkbenchError
    ? '工作台資料載入失敗，請稍後重試'
    : learnerSetupAttention > 0
      ? '先用 CSV 匯入學生，老師才可以發布作業到班級。'
    : publishedNeedingSetup > 0 && publishedWithoutTargets > 0
      ? `${publishedNeedingSetup} 份作業需整理，其中 ${publishedWithoutTargets} 份需重設班級或加入學生${publishedEmptyTargetUsergroups > 0 ? `，${publishedEmptyTargetUsergroups} 份班級沒有學生` : ''}`
    : publishedNeedingSetup > 0
      ? `${publishedNeedingSetup} 份已發布作業需要整理`
    : publishedWithoutTargets > 0
      ? `${publishedWithoutTargets} 份已發布作業需重設班級或加入學生${publishedEmptyTargetUsergroups > 0 ? `，${publishedEmptyTargetUsergroups} 份班級沒有學生` : ''}`
    : setupAttention > 0
      ? '已發布作業需要整理'
      : '核心作業設定正常'
  const setupAttentionHref =
    learnerSetupAttention > 0
      ? dashboardHref('/dash/users/settings/add')
    : publishedNeedingSetup > 0 || publishedWithoutTargets > 0
      ? dashboardHref('/dash/assignments?status=needs_setup')
      : dashboardHref('/dash/assignments')
  const aiActions = React.useMemo(
    () => [...(workbench?.ai_actions || [])].sort((left: any, right: any) => {
      const leftPriority = left?.priority === 'high' ? 0 : 1
      const rightPriority = right?.priority === 'high' ? 0 : 1
      if (leftPriority !== rightPriority) return leftPriority - rightPriority
      return workbenchActionRank(left?.title) - workbenchActionRank(right?.title)
    }),
    [workbench?.ai_actions]
  )
  const todayActions = React.useMemo(() => {
    const actions: Array<{
      title: string
      description: string
      href: string
      cta: string
      tone: 'amber' | 'rose' | 'blue' | 'emerald'
    }> = []

    if (essayPendingReview > 0) {
      actions.push({
        title: '先覆核作文',
        description: `${essayPendingReview} 份作文已有 AI 初評，老師確認或調整分數即可。`,
        href: dashboardHref('/dash/gradebook?attention=1'),
        cta: '批改作文',
        tone: 'amber',
      })
    }

    if (nonEssayPendingReview > 0) {
      actions.push({
        title: '批改待覆核提交',
        description: `${nonEssayPendingReview} 份提交需要老師看一眼，避免成績表停在待確認。`,
        href: dashboardHref('/dash/gradebook?attention=1'),
        cta: '去批改',
        tone: 'amber',
      })
    }

    if (missingSubmissions > 0) {
      actions.push({
        title: '跟進未交學生',
        description: `${missingSubmissions} 名學生/作業仍未提交，先看名單再提醒。`,
        href: dashboardHref('/dash/gradebook?attention=1'),
        cta: '查看未交',
        tone: 'rose',
      })
    }

    if (dueSoon > 0) {
      actions.push({
        title: '檢查快截止作業',
        description: `${dueSoon} 份作業 7 天內截止，確認學生看得到、班級設定正確。`,
        href: dashboardHref('/dash/assignments'),
        cta: '查看作業',
        tone: 'blue',
      })
    }

    if (setupAttention > 0) {
      actions.push({
        title: learnerSetupAttention > 0 ? '先匯入學生' : '整理試行設定',
        description: setupAttentionDetail,
        href: setupAttentionHref,
        cta: learnerSetupAttention > 0 ? '匯入學生' : '去整理',
        tone: 'emerald',
      })
    }

    if (actions.length === 0) {
      actions.push({
        title: '今天沒有急件',
        description: '可以建立下一份 3 題簡單作業，讓學生保持練習節奏。',
        href: dashboardHref('/dash/assignments'),
        cta: '建立作業',
        tone: 'emerald',
      })
    }

    return actions.slice(0, 3)
  }, [
    dueSoon,
    essayPendingReview,
    learnerSetupAttention,
    missingSubmissions,
    nonEssayPendingReview,
    setupAttention,
    setupAttentionDetail,
    setupAttentionHref,
  ])
  const statCards = [
    {
      label: '待覆核',
      value: pendingReview,
      icon: <ClipboardCheck size={18} />,
      color: 'text-amber-600 bg-amber-50 border-amber-100',
    },
    {
      label: '未提交學生',
      value: missingSubmissions,
      icon: <AlertCircle size={18} />,
      color: 'text-rose-600 bg-rose-50 border-rose-100',
    },
    {
      label: '即將截止',
      value: dueSoon,
      icon: <CalendarClock size={18} />,
      color: 'text-blue-600 bg-blue-50 border-blue-100',
    },
    {
      label: '試行就緒作業',
      value: autoGradedAssignments,
      icon: <ListChecks size={18} />,
      color: 'text-emerald-600 bg-emerald-50 border-emerald-100',
    },
    {
      label: retryInProgress > 0 ? '重做中' : '重做/再練習',
      value: retryInProgress > 0 ? retryInProgress : retryRecords,
      icon: <RotateCcw size={18} />,
      color: 'text-cyan-600 bg-cyan-50 border-cyan-100',
    },
    {
      label: '達標/補強',
      value: `${passingScoreRecords}/${needsPracticeRecords}`,
      icon: <CheckCircle2 size={18} />,
      color: needsPracticeRecords > 0
        ? 'text-orange-600 bg-orange-50 border-orange-100'
        : passingScoreRecords > 0
          ? 'text-emerald-600 bg-emerald-50 border-emerald-100'
          : 'text-gray-500 bg-gray-50 border-gray-100',
    },
  ]

  if (isLoading && !workbench) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((item) => (
          <div key={item} className="h-24 bg-white nice-shadow rounded-lg animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-gray-400 uppercase tracking-wider">
            澳門校內試行
          </p>
          <h2 className="text-xl font-bold text-gray-900 mt-1">今日教學工作台</h2>
          <p className="mt-1 text-sm text-gray-500">
            一進來只看今天最需要處理的事：批改、作文覆核、未交和快截止。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <WorkbenchLink href={dashboardHref('/dash/assignments')} icon={<ListChecks size={14} />} label="建立作業" />
          <WorkbenchLink href={dashboardHref('/dash/gradebook')} icon={<ClipboardCheck size={14} />} label="成績表" />
          <WorkbenchLink href={dashboardHref('/dash/users/settings/add')} icon={<Users size={14} />} label="匯入帳號" />
        </div>
      </div>

      {error && (
        <div className="flex flex-col gap-2 rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-700 sm:flex-row sm:items-center sm:justify-between">
          <span>{error.message || '作業工作台載入失敗，請稍後再試。'}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={isRetrying}
              className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-rose-200 bg-white px-3 text-xs font-black text-rose-800 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isRetrying ? '重新載入中' : '重新載入工作台'}
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="bg-white nice-shadow rounded-lg border border-gray-100 p-4">
          <div className="flex flex-col gap-4">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-gray-400">今日重點</p>
              <h3 className="mt-1 text-lg font-bold text-gray-900">
                {isAllClear ? '今天暫時沒有急件' : `有 ${focusCount} 件教學事項需要留意`}
              </h3>
              <p className="mt-1 text-sm text-gray-500">
                {isAllClear
                  ? '可以先建立下一份 3 題短練習，讓學生有穩定練習節奏。'
                  : '先處理待批改、作文覆核、未交和快截止，其他進階資料先收起。'}
              </p>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
              <PriorityAction
                href={dashboardHref('/dash/gradebook?attention=1')}
                label="待批改"
                value={pendingReview}
                detail="所有需要老師確認的提交"
                tone="amber"
              />
              <PriorityAction
                href={dashboardHref('/dash/gradebook?attention=1')}
                label="作文待覆核"
                value={essayPendingReview}
                detail="AI 初評後仍要老師確認"
                tone="emerald"
              />
              <PriorityAction
                href={dashboardHref('/dash/gradebook?attention=1')}
                label="未交"
                value={missingSubmissions}
                detail="快速知道哪些學生要跟進"
                tone="rose"
              />
              <PriorityAction
                href={dashboardHref('/dash/assignments')}
                label="快截止"
                value={dueSoon}
                detail="7 天內截止的作業"
                tone="blue"
              />
            </div>
          </div>
        </div>

        <div className="bg-white nice-shadow rounded-lg border border-gray-100 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-gray-400">今天建議處理</p>
              <h3 className="mt-1 text-base font-black text-gray-900">最多先做三件事</h3>
            </div>
            <ListChecks size={20} className="text-gray-400" />
          </div>
          <div className="mt-3 space-y-2">
            {todayActions.map((action) => (
              <Link
                key={action.title}
                href={action.href}
                className="group flex items-start justify-between gap-3 rounded-lg border border-gray-100 px-3 py-3 hover:border-gray-200 hover:bg-gray-50"
              >
                <div className="min-w-0">
                  <p className="text-sm font-black text-gray-900">{action.title}</p>
                  <p className="mt-1 text-xs leading-snug text-gray-500">{action.description}</p>
                  <span className={`mt-2 inline-flex items-center rounded-md border px-2 py-1 text-[11px] font-black ${priorityToneClass(action.tone)}`}>
                    {action.cta}
                  </span>
                </div>
                <ArrowRight size={15} className="mt-1 shrink-0 text-gray-300 group-hover:text-gray-600" />
              </Link>
            ))}
          </div>
        </div>
      </div>

      <details className="group rounded-lg border border-gray-100 bg-white p-4 nice-shadow">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
          <div>
            <p className="text-sm font-black text-gray-900">進階試行資料</p>
            <p className="mt-1 text-xs text-gray-500">
              展開後查看題庫、自測、校長摘要和最近個案；日常使用可先不用看。
            </p>
          </div>
          <span className="rounded-lg border border-gray-100 px-3 py-1 text-xs font-bold text-gray-600 group-open:bg-gray-50">
            展開
          </span>
        </summary>
        <div className="mt-4 space-y-4">
          <PilotReadinessCard
            summary={summary}
            orgslug={orgslug}
            operationsSummary={operationsSummary}
            operationsLoading={operationsLoading}
            operationsError={operationsError}
            operationsFetching={operationsFetching}
            onRetryOperations={onRetryOperations}
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-6 gap-3">
            {statCards.map((card) => (
              <div key={card.label} className="rounded-lg border border-gray-100 bg-gray-50/60 p-4">
                <div className="flex items-center justify-between">
                  <span className={`inline-flex h-9 w-9 items-center justify-center rounded-lg border ${card.color}`}>
                    {card.icon}
                  </span>
                  <span className="text-2xl font-bold text-gray-900">{card.value}</span>
                </div>
                <p className="text-xs font-semibold text-gray-500 mt-3">{card.label}</p>
              </div>
            ))}
          </div>
          <PilotFlowCard orgslug={orgslug} />
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-4 gap-4">
            <WorkbenchPanel title="待覆核提交" empty="暫時沒有需要覆核的提交。">
              {(workbench?.pending_review_submissions || []).map((row: any) => (
                <Link
                  key={`${row.assignment_uuid}-${row.student_id}`}
                  href={dashboardHref(`/dash/assignments/${cleanAssignmentUuid(row.assignment_uuid)}?subpage=submissions`)}
                  className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 hover:bg-gray-50"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-gray-900 truncate">{row.assignment_title}</p>
                    <p className="text-xs text-gray-500 truncate">
                      {row.student_name} · {row.has_essay_task ? '作文待覆核' : row.student_email}
                    </p>
                  </div>
                  <ArrowRight size={14} className="text-gray-400 flex-none" />
                </Link>
              ))}
            </WorkbenchPanel>

            <WorkbenchPanel title="未提交學生" empty="暫時沒有未提交記錄。">
              {(workbench?.missing_submissions || []).map((row: any) => (
                <Link
                  key={`${row.assignment_uuid}-${row.student_id}`}
                  href={dashboardHref('/dash/gradebook?attention=1')}
                  className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 hover:bg-gray-50"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-gray-900 truncate">{row.student_name}</p>
                    <p className="text-xs text-gray-500 truncate">{row.assignment_title}</p>
                  </div>
                  <AlertCircle size={14} className="text-rose-500 flex-none" />
                </Link>
              ))}
            </WorkbenchPanel>

            <WorkbenchPanel title="即將截止作業" empty="未來 7 天沒有截止作業。">
              {(workbench?.due_soon_assignments || []).map((assignment: any) => (
                <Link
                  key={assignment.assignment_uuid}
                  href={dashboardHref(`/dash/assignments/${cleanAssignmentUuid(assignment.assignment_uuid)}?subpage=submissions`)}
                  className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 hover:bg-gray-50"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-gray-900 truncate">{assignment.title}</p>
                    <p className="text-xs text-gray-500 truncate">
                      {assignment.course_name || '未命名課程'} · {formatZhHkDate(assignment.due_date)}
                    </p>
                  </div>
                  <ArrowRight size={14} className="text-gray-400 flex-none" />
                </Link>
              ))}
            </WorkbenchPanel>

            <WorkbenchPanel title="系統建議" empty="暫時沒有待處理建議。">
              {aiActions.map((action: any) => {
                const isHighPriority = action?.priority === 'high'
                return (
                  <Link
                    key={action.title}
                    href={orgAwareDashboardHref(orgslug, action.href)}
                    className={`flex items-start gap-3 rounded-lg border px-3 py-2 hover:bg-gray-50 ${
                      isHighPriority ? 'border-amber-200 bg-amber-50/40' : 'border-gray-100'
                    }`}
                  >
                    <span className={`mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-md text-white flex-none ${
                      isHighPriority ? 'bg-amber-600' : 'bg-gray-900'
                    }`}>
                      <ListChecks size={14} />
                    </span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-bold text-gray-900">{action.title}</p>
                        {isHighPriority && (
                          <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-black text-amber-700">
                            優先
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-gray-500 leading-snug">{action.description}</p>
                    </div>
                  </Link>
                )
              })}
            </WorkbenchPanel>
          </div>
        </div>
      </details>
    </section>
  )
}

function PilotFlowCard({ orgslug }: { orgslug: string }) {
  const dashboardHref = (path: string) => getUriWithOrg(orgslug, path)
  const steps = [
    {
      label: '出簡單題',
      detail: 'AI、題庫或手動都可以',
      href: dashboardHref('/dash/assignments'),
      icon: <ListChecks size={16} />,
    },
    {
      label: '發給學生',
      detail: '選班級，設定截止日期',
      href: dashboardHref('/dash/assignments'),
      icon: <BookOpen size={16} />,
    },
    {
      label: '系統自動批改',
      detail: '答錯可以再做，取最高分',
      href: dashboardHref('/dash/gradebook'),
      icon: <ListChecks size={16} />,
    },
    {
      label: '老師看成績',
      detail: '看未交、分數和需要跟進的人',
      href: dashboardHref('/dash/gradebook'),
      icon: <ClipboardCheck size={16} />,
    },
  ]

  return (
    <div className="rounded-lg border border-cyan-100 bg-cyan-50/60 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">校內使用流程</p>
          <h3 className="mt-1 text-base font-black text-gray-950">先把簡單作業跑順</h3>
          <p className="mt-1 text-sm text-gray-600">
            老師不用理解太多功能，先做到出題、提交、批改、看分數。
          </p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4 lg:min-w-[720px]">
          {steps.map((step, index) => (
            <Link
              key={step.label}
              href={step.href}
              className="flex items-start gap-3 rounded-lg border border-white bg-white/80 px-3 py-3 hover:bg-white"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan-700 text-white">
                {step.icon}
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-bold text-cyan-700">第 {index + 1} 步</p>
                <p className="mt-0.5 text-sm font-black text-gray-900">{step.label}</p>
                <p className="mt-1 text-xs leading-snug text-gray-500">{step.detail}</p>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}

function PilotReadinessCard({
  summary,
  orgslug,
  operationsSummary,
  operationsLoading,
  operationsError,
  operationsFetching,
  onRetryOperations,
}: {
  summary: any
  orgslug: string
  operationsSummary?: any
  operationsLoading?: boolean
  operationsError?: Error | null
  operationsFetching?: boolean
  onRetryOperations?: () => void
}) {
  const dashboardHref = (path: string) => getUriWithOrg(orgslug, path)
  const [copied, setCopied] = React.useState(false)
  const activeStudentCount = Number(summary.active_student_count ?? summary.student_count ?? 0)
  const engagedStudentCount = Number(summary.engaged_student_count ?? 0)
  const learnerCount = Number(summary.learner_count ?? activeStudentCount)
  const published = Number(summary.published || 0)
  const autoGraded = Number(
    summary.simple_pilot_ready_assignments
      ?? summary.simple_auto_graded_assignments
      ?? summary.auto_graded_assignments
      ?? 0
  )
  const publishedNeedingSetup = Number(summary.published_needing_setup || 0)
  const publishedWithoutTargets = Number(summary.published_without_targets || 0)
  const publishedEmptyTargetUsergroups = Number(summary.published_empty_target_usergroups || 0)
  const publishedHiddenFromStudents = Number(summary.published_hidden_from_students || 0)
  const submitted = Number(summary.submitted || 0)
  const graded = Number(summary.graded || 0)
  const retryInProgress = Number(summary.retry_in_progress || 0)
  const retryRecords = Number(summary.retry_records || 0)
  const selfTestRecords = Number(summary.self_test_rows || 0)
  const selfTestReadyQuestionCount = Number(summary.self_test_ready_question_count || 0)
  const selfTestMinQuestionCount = Number(summary.self_test_min_question_count || 3)
  const selfTestBankStatus = pilotReadyStatus(summary.self_test_bank_ready)
  const selfTestBankReady = selfTestBankStatus === true
  const pendingReview = Number(summary.pending_review || 0)
  const expectedUnsubmitted = Number(summary.expected_unsubmitted ?? summary.unsubmitted ?? 0)
  const totalRows = Number(summary.actionable_rows ?? summary.total_rows ?? 0)
  const scoredRecords = Number(summary.scored_records || 0)
  const passingScoreRecords = Number(summary.passing_score_records || 0)
  const needsPracticeRecords = Number(summary.needs_practice_records || 0)
  const backendPilotStatus = String(summary.pilot_status || '').trim()
  const backendPilotStatusLabel = String(summary.pilot_status_label || '').trim()
  const backendPilotStatusDetail = String(summary.pilot_status_detail || '').trim()
  const backendPilotNextStep = String(summary.pilot_next_step || '').trim()
  const backendPrincipalEvidenceSummary = String(summary.principal_evidence_summary || '').trim()
  const aiStatus = pilotReadyStatus(summary.ai_assignment_ready)
  const aiReady = aiStatus === true
  const aiMessage = String(summary.ai_assignment_message || 'AI 出題配置尚未確認。')
  const aiSetupDetail = aiReady
    ? aiMessage
    : aiStatus === false
      ? `${aiMessage} 未配置時可先用題庫或手動建立選擇、填空、短問答；AI 出題之後再啟用。`
      : '暫時未取得 AI 出題狀態。可先用題庫或手動出簡單題，之後再確認端點、模型和 API Key。'
  const simpleAssignmentDetail = (() => {
    if (published <= 0) return '先建立一份 3 題簡單作業。'
    if (publishedWithoutTargets > 0) {
      const emptyClassDetail = publishedEmptyTargetUsergroups > 0
        ? `，其中 ${publishedEmptyTargetUsergroups} 份班級沒有學生`
        : ''
      return `已發布 ${published} 份作業，其中 ${publishedWithoutTargets} 份未指定班級/群組、班級已不存在或班級沒有學生${emptyClassDetail}，請先補上發布範圍或把學生加入班級。`
    }
    if (publishedNeedingSetup > 0 && publishedHiddenFromStudents > 0) {
      return `已發布 ${published} 份作業，${autoGraded} 份校內試行就緒；其中 ${publishedHiddenFromStudents} 份的課程或活動未發布，學生暫時看不到。`
    }
    if (publishedNeedingSetup > 0) {
      return `已發布 ${published} 份作業，${autoGraded} 份校內試行就緒；${publishedNeedingSetup} 份需要檢查有效截止日期，改成選擇、填空、短問答，並啟用自動批改、可重做、顯示答案和最高分計分。`
    }
    return `已發布 ${published} 份作業，其中 ${autoGraded} 份校內試行就緒，發布範圍已設定。`
  })()
  const submissionRate = typeof summary.submission_rate === 'number' ? summary.submission_rate : null
  const submissionRateText = submissionRate === null ? '暫無提交率' : `${Math.round(submissionRate)}%`
  const participationRate = typeof summary.participation_rate === 'number' ? summary.participation_rate : null
  const participationRateText = participationRate === null ? '暫無參與率' : `${Math.round(participationRate)}%`
  const simpleAssignmentsReady = autoGraded > 0 && publishedNeedingSetup === 0 && publishedWithoutTargets === 0
  const selfTestBankDetail = selfTestBankReady
    ? selfTestReadyQuestionCount >= selfTestMinQuestionCount
      ? `題庫已有 ${selfTestReadyQuestionCount} 題可用簡單題，學生可以做完整 3 題自測。`
      : `題庫已有 ${selfTestReadyQuestionCount} 題可用簡單題；建議補到至少 ${selfTestMinQuestionCount} 題，學生每次練習更完整。`
    : selfTestBankStatus === false
      ? '目前沒有可用的共享自測題目。這是加分項，可先跑通作業、提交和批改，再補選擇、填空、短問答。'
      : '暫時未取得自測題庫狀態。這是加分項，可之後再確認至少 3 題選擇、填空或短問答。'
  const corePilotReady = learnerCount > 0 && simpleAssignmentsReady && submitted > 0 && scoredRecords > 0
  const principalDemoReady = backendPilotStatus
    ? backendPilotStatus === 'showcase_ready'
    : corePilotReady
  const principalDemoDetail = (() => {
    if (backendPilotStatusDetail) return backendPilotStatusDetail
    if (learnerCount <= 0) return '先批量匯入學生，讓老師可以發布到班級。'
    if (publishedWithoutTargets > 0) {
      const emptyClassDetail = publishedEmptyTargetUsergroups > 0
        ? `，其中 ${publishedEmptyTargetUsergroups} 份要先把學生加入班級`
        : ''
      return `先為 ${publishedWithoutTargets} 份已發布作業設定班級/群組或補學生${emptyClassDetail}，避免校內試行數據範圍不清。`
    }
    if (publishedHiddenFromStudents > 0) {
      return `先發布相關課程和活動；目前有 ${publishedHiddenFromStudents} 份已發布作業學生暫時看不到。`
    }
    if (publishedNeedingSetup > 0) {
      return `先整理 ${publishedNeedingSetup} 份已發布作業，檢查截止日期、題型和自動批改設定。`
    }
    if (autoGraded <= 0) return '先建立一份選擇、填空、短問答的自動批改作業。'
    if (retryInProgress > 0 && graded <= 0) {
      return `有 ${retryInProgress} 條學生正在重做，等學生重新提交後就能展示錯題改善和最高分。`
    }
    if (submitted <= 0) return '請學生完成一次提交，讓成績表有可展示的學習記錄。'
    if (scoredRecords <= 0) return '已有學生使用記錄；下一步讓學生完成一份計入評分的簡單作業，或把合適自測設為計分。'
    if (aiStatus === false) return '核心作業閉環已可展示；AI 出題尚未配置完整，展示時可先主打自動批改、可重做和成績表。'
    if (aiStatus === null) return '核心作業閉環已可展示；AI 出題狀態未確認，之後可再檢查端點、模型和 API Key。'
    if (selfTestBankStatus === false) return '核心作業閉環已可展示；自測題庫可作為下一步補充，不影響先展示學生提交和批改證據。'
    if (selfTestBankStatus === null) return '核心作業閉環已可展示；自測題庫狀態可之後再確認。'
    return `已有 ${graded} 條評分記錄，達標 ${passingScoreRecords} 條、需補強 ${needsPracticeRecords} 條，學習參與率 ${participationRateText}，提交率 ${submissionRateText}。`
  })()
  const pilotNextStep = backendPilotNextStep || principalDemoDetail
  const pilotStatusLabel = backendPilotStatusLabel || (principalDemoReady ? '可展示' : '仍需整理')
  const averageScoreValue = Number(summary.average_score)
  const averageScoreText = Number.isFinite(averageScoreValue)
    ? `${Math.round(averageScoreValue)}%`
    : '未統計'
  const operationsScope = operationsSummary?.scope || {}
  const operationsMetrics = operationsSummary?.metrics || {}
  const operationsWorkload = operationsSummary?.workload || {}
  const operationsPrivacy = operationsSummary?.privacy || {}
  const operationsNarrative = operationsSummary?.summary || {}
  const operationsPeriod = operationsScope.start_date && operationsScope.end_date
    ? `${operationsScope.start_date} 至 ${operationsScope.end_date}`
    : '本週'
  const operationsAvailable = Boolean(operationsSummary?.scope)
  const operationsLearnerCount = Number(operationsMetrics.learner_count || 0)
  const operationsEngagedCount = Number(operationsMetrics.engaged_student_count || 0)
  const operationsRecordCount = Number(operationsMetrics.record_count || 0)
  const operationsAutoGraded = Number(operationsMetrics.auto_graded_records || 0)
  const operationsMinutesSaved = Number(operationsWorkload.estimated_teacher_minutes_saved || 0)
  const operationsSubmissionRate = operationsMetrics.submission_rate == null
    ? operationsPrivacy.suppressed ? '已按私隱規則隱藏' : '暫無資料'
    : `${Math.round(Number(operationsMetrics.submission_rate))}%`
  const operationsAverageScore = operationsMetrics.average_score == null
    ? operationsPrivacy.suppressed ? '已按私隱規則隱藏' : '暫無評分'
    : `${Math.round(Number(operationsMetrics.average_score))}%`
  const leadershipSummary = String(operationsNarrative.principal_brief || '').trim()
  const principalEvidenceSummary = leadershipSummary || backendPrincipalEvidenceSummary || principalDemoDetail
  const principalQuestions = [
    {
      label: '學生有用嗎',
      value: operationsAvailable
        ? `${operationsEngagedCount}/${operationsLearnerCount} 名有記錄`
        : submitted > 0
          ? learnerCount > 0
            ? `${engagedStudentCount}/${learnerCount} 名有記錄`
            : `${engagedStudentCount} 名有記錄`
          : '未有記錄',
      detail: operationsAvailable
        ? `${operationsPeriod} 共 ${operationsRecordCount} 條學習記錄，提交率 ${operationsSubmissionRate}。`
        : submitted > 0
          ? `提交/自測記錄 ${submitted} 條，提交率 ${submissionRateText}。`
          : '先讓學生完成一份 3 題簡單作業。',
    },
    {
      label: '老師省力嗎',
      value: operationsAvailable
        ? `${operationsAutoGraded} 條自動批改`
        : autoGraded > 0 ? `${autoGraded} 份可自動批改` : '待建立',
      detail: operationsAvailable
        ? `透明估算約節省 ${operationsMinutesSaved} 分鐘人工批改時間。`
        : aiReady
          ? 'AI 可幫老師出選擇、填空、短問答。'
          : '未配置 AI 也可用題庫或手動出簡單題。',
    },
    {
      label: '成績有證據嗎',
      value: operationsAvailable
        ? operationsPrivacy.suppressed ? '小群組已保護' : `平均分 ${operationsAverageScore}`
        : scoredRecords > 0 ? `${scoredRecords} 條計分` : '未有計分',
      detail: operationsAvailable
        ? operationsPrivacy.suppressed
          ? `群組少於 ${operationsPrivacy.minimum_cohort_size || 5} 人，已隱藏可推斷個別表現的比率和分數。`
          : `待覆核 ${Number(operationsMetrics.pending_review || 0)} 條，未提交 ${Number(operationsMetrics.unsubmitted || 0)} 條。`
        : scoredRecords > 0
          ? `平均分 ${averageScoreText}，待跟進 ${expectedUnsubmitted + pendingReview} 條。`
          : '學生提交並完成批改後，成績表會留下記錄。',
    },
  ]

  const readinessItems = [
    {
      label: '已匯入學生',
      detail: learnerCount > 0
        ? engagedStudentCount > 0
          ? `已匯入 ${learnerCount} 名學生，其中 ${engagedStudentCount} 名已有學習記錄。`
          : `已匯入 ${learnerCount} 名學生，下一步讓學生完成第一次作業或自測。`
        : '先批量匯入學生，或把學生加入班級/群組。',
      done: learnerCount > 0,
      href: dashboardHref(learnerCount > 0 ? '/dash/users/settings/users' : '/dash/users/settings/add'),
    },
    {
      label: '作業已整理好',
      detail: simpleAssignmentDetail,
      done: simpleAssignmentsReady,
      href: dashboardHref('/dash/assignments'),
    },
    {
      label: '學生已開始提交',
      detail: retryInProgress > 0
        ? `有 ${retryInProgress} 條正在重做，學生已開始改錯再提交。`
        : submitted > 0
        ? `已有 ${submitted}/${totalRows || submitted} 條提交或自測記錄。`
        : '請學生完成第一次作業或自測。',
      done: submitted > 0 || retryInProgress > 0,
      href: dashboardHref('/dash/gradebook'),
    },
    {
      label: '答錯可以再做',
      detail: retryRecords > 0
        ? `已有 ${retryRecords} 條重做/再練習記錄，現在 ${retryInProgress} 條重做中。`
        : '簡單作業預設允許重做，錯了可以改完再提交。',
      done: simpleAssignmentsReady,
      href: dashboardHref('/dash/gradebook'),
    },
    {
      label: '可向校長展示',
      detail: principalEvidenceSummary,
      done: principalDemoReady,
      href: dashboardHref('/dash/gradebook'),
    },
    {
      label: 'AI 省力出題',
      detail: aiSetupDetail,
      done: aiReady,
      href: dashboardHref('/dash/assignments'),
    },
    {
      label: '自測加分項',
      detail: selfTestBankDetail,
      done: selfTestBankReady,
      href: dashboardHref('/dash/question-bank'),
    },
  ]
  const coreReadinessItems = readinessItems.slice(0, 5)
  const addOnReadinessItems = readinessItems.slice(5)
  const coreCompletedCount = coreReadinessItems.filter((item) => item.done).length
  const addOnCompletedCount = addOnReadinessItems.filter((item) => item.done).length
  const principalItem = readinessItems.find((item) => item.label === '可向校長展示') || readinessItems[readinessItems.length - 1]
  const nextItem = principalDemoReady
    ? principalItem
    : readinessItems.find((item) => !item.done) || readinessItems[readinessItems.length - 1]
  const nextActionLabel = pilotNextActionLabel(nextItem.label, principalDemoReady)

  async function copyPrincipalSummary() {
    const operationsText = operationsAvailable ? [
      'LearnHouse 澳門校內營運摘要',
      `摘要範圍：${operationsPeriod}（${operationsScope.timezone || 'Asia/Macau'}）`,
      `狀態：${operationsNarrative.status_label || '本期摘要'}`,
      `校長/主任摘要：${leadershipSummary || '目前沒有可展示的學習記錄。'}`,
      `學生人數：${operationsLearnerCount} 名`,
      `有學習記錄學生：${operationsEngagedCount} 名`,
      `學習記錄：${operationsRecordCount} 條`,
      `提交：${Number(operationsMetrics.submitted || 0)} 條`,
      `提交率：${operationsSubmissionRate}`,
      `已批改：${Number(operationsMetrics.graded || 0)} 條`,
      `待覆核：${Number(operationsMetrics.pending_review || 0)} 條`,
      `未提交：${Number(operationsMetrics.unsubmitted || 0)} 條`,
      `平均分：${operationsAverageScore}`,
      `自動批改：${operationsAutoGraded} 條`,
      `估算節省時間：約 ${operationsMinutesSaved} 分鐘`,
      `估算方法：${operationsWorkload.methodology || '按自動批改記錄作透明估算。'}`,
      `私隱保護：${operationsPrivacy.suppressed ? '小群組敏感指標已隱藏' : '只包含聚合資料'}`,
      `下一步：${operationsNarrative.next_step || pilotNextStep}`,
    ].join('\n') : null
    const fallbackText = [
      'LearnHouse 澳門校內試行摘要',
      `校內試行狀態：${pilotStatusLabel}`,
      `核心作業閉環：${principalDemoReady ? '可展示' : `仍需整理（${coreCompletedCount}/${coreReadinessItems.length} 步）`}`,
      `校長展示摘要：${principalEvidenceSummary}`,
      '核心證據：',
      `已匯入學生：${learnerCount} 名`,
      `已有學習記錄學生：${engagedStudentCount} 名`,
      `已發布作業：${published} 份`,
      `校內試行就緒作業：${autoGraded} 份`,
      `學生暫時看不到的已發布作業：${publishedHiddenFromStudents} 份`,
      `班級需重設或需加學生作業：${publishedWithoutTargets} 份`,
      `班級沒有學生作業：${publishedEmptyTargetUsergroups} 份`,
      `學生提交/自測記錄：${submitted}/${totalRows || submitted} 條`,
      `學習參與率：${participationRateText}`,
      `已批改/已覆核：${graded} 條`,
      `計入評分記錄：${scoredRecords} 條`,
      `達標記錄（60% 以上）：${passingScoreRecords} 條`,
      `需補強記錄（低於 60%）：${needsPracticeRecords} 條`,
      `答錯再練習：${retryRecords} 條`,
      `重做中：${retryInProgress} 條`,
      `提交率：${submissionRateText}`,
      `待老師覆核：${pendingReview} 條`,
      `未提交學生：${expectedUnsubmitted} 條`,
      `校長展示狀態：${pilotStatusLabel}`,
      '簡化原則：作業只用選擇、填空、短問答；可用 AI、題庫或手動出題；學生提交後自動批改，答錯可重做，顯示答案，成績取最高分。',
      'Pilot 主線：先證明老師能發簡單作業、學生能提交、系統能批改、老師能看成績；AI 出題和自測題庫是省力加分項，不是先試行的阻塞條件。',
      `下一步：${pilotNextStep}`,
      '加分項：',
      `AI 省力出題：${pilotReadyStatusLabel(aiStatus)}`,
      `自測題庫：${pilotReadyStatusLabel(selfTestBankStatus)}，可用自測題 ${selfTestReadyQuestionCount} 題，自測記錄 ${selfTestRecords} 條`,
    ].join('\n')
    const text = operationsText || fallbackText

    try {
      const result = await copyTextOrDownload(text, 'learnhouse-校長摘要.txt')
      setCopied(true)
      toast.success(result === 'copied' ? '已複製校長摘要' : '剪貼簿不可用，已下載校長摘要')
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      toast.error('無法複製或下載校長摘要，請手動複製成績表摘要。')
    }
  }

  return (
    <div className="rounded-lg border border-emerald-100 bg-white p-4 nice-shadow">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div className="max-w-xl">
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">
            {operationsAvailable ? `本週營運證據 · ${operationsPeriod}` : '校內試行狀態'}
          </p>
          <h3 className="mt-1 text-base font-black text-gray-950">
            {operationsNarrative.status_label || pilotStatusLabel}
          </h3>
          <p className="mt-1 text-sm text-gray-600">
            {principalEvidenceSummary}
          </p>
          {operationsLoading && (
            <p className="mt-2 text-xs font-semibold text-emerald-700">正在整理本週聚合資料...</p>
          )}
          {operationsError && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-900">
              <span>本週摘要暫時載入失敗，原有老師工作台仍可正常使用。</span>
              <button
                type="button"
                onClick={onRetryOperations}
                disabled={operationsFetching}
                className="rounded-md border border-amber-300 bg-white px-2 py-1 font-black disabled:opacity-60"
              >
                {operationsFetching ? '重試中' : '重新載入'}
              </button>
            </div>
          )}
          <p className="mt-1 text-sm font-semibold text-gray-700">
            下一步：{pilotNextStep}
          </p>
          <p className="mt-1 text-xs font-semibold text-gray-500">
            加分項：AI 省力出題、自測題庫已完成 {addOnCompletedCount}/{addOnReadinessItems.length}。
          </p>
          <p className="mt-1 text-xs font-semibold text-emerald-800">
            簡化原則：選擇、填空、短問答；自動批改；答錯可重做；最高分計分。
          </p>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {principalQuestions.map((item) => (
              <div key={item.label} className="rounded-lg border border-emerald-100 bg-emerald-50/70 px-3 py-2">
                <p className="text-[11px] font-black text-emerald-800">{item.label}</p>
                <p className="mt-0.5 text-sm font-black text-gray-950">{item.value}</p>
                <p className="mt-1 text-[11px] font-semibold leading-snug text-gray-600">{item.detail}</p>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Link
              href={nextItem.href}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-gray-950 px-3 text-xs font-bold text-white hover:bg-black"
            >
              {nextActionLabel}
              <ArrowRight size={14} />
            </Link>
            <button
              type="button"
              onClick={copyPrincipalSummary}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 text-xs font-bold text-emerald-700 hover:bg-emerald-50"
            >
              {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
              複製校長摘要
            </button>
          </div>
        </div>
        <div className="space-y-3 xl:flex-1">
          <div>
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-[11px] font-black uppercase tracking-wider text-gray-500">
                必做核心 {coreCompletedCount}/{coreReadinessItems.length}
              </p>
              <span className="text-[11px] font-semibold text-gray-500">
                作業閉環跑順就可以先試行
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
              {coreReadinessItems.map((item) => (
                <ReadinessItemLink key={item.label} item={item} />
              ))}
            </div>
          </div>
          <div>
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-[11px] font-black uppercase tracking-wider text-emerald-700">
                可選加分 {addOnCompletedCount}/{addOnReadinessItems.length}
              </p>
              <span className="text-[11px] font-semibold text-gray-500">
                AI 和自測用來省老師時間，不阻塞 pilot
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {addOnReadinessItems.map((item) => (
                <ReadinessItemLink key={item.label} item={item} compact />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function ReadinessItemLink({
  item,
  compact = false,
}: {
  item: { label: string; detail: string; done: boolean; href: string }
  compact?: boolean
}) {
  return (
    <Link
      href={item.href}
      className={`group flex items-start gap-3 rounded-lg border border-gray-100 px-3 hover:border-emerald-200 hover:bg-emerald-50/40 ${
        compact ? 'py-2.5' : 'py-3'
      }`}
    >
      <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
        item.done ? 'bg-emerald-600 text-white' : 'bg-gray-100 text-gray-400'
      }`}>
        {item.done ? <CheckCircle2 size={15} /> : <CircleDashed size={15} />}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-black text-gray-900">{item.label}</p>
        <p className="mt-1 text-xs leading-snug text-gray-500">{item.detail}</p>
      </div>
    </Link>
  )
}

function pilotNextActionLabel(nextLabel: string, principalDemoReady: boolean) {
  if (principalDemoReady) return '查看成績表'
  if (nextLabel === '已匯入學生') return '去匯入學生'
  if (nextLabel === '作業已整理好') return '建立簡單作業'
  if (nextLabel === '自測加分項') return '補充自測題庫'
  if (nextLabel === '學生已開始提交') return '查看成績表'
  if (nextLabel === '答錯可以再做') return '查看重做記錄'
  if (nextLabel === 'AI 省力出題') return '檢查 AI 出題'
  return '查看成績表'
}

function workbenchActionRank(title: string | undefined) {
  const value = String(title || '')
  if (value.includes('匯入學生')) return 0
  if (value.includes('設定發佈班級')) return 1
  if (value.includes('整理已發布作業')) return 2
  if (value.includes('批改')) return 3
  if (value.includes('簡單出題') || value.includes('AI 出題') || value.includes('配置 AI')) return 4
  if (value.includes('自測題庫')) return 5
  if (value.includes('自測')) return 6
  return 9
}

function WorkbenchLink({
  href,
  icon,
  label,
}: {
  href: string
  icon: React.ReactNode
  label: string
}) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-bold text-gray-700 bg-white rounded-lg nice-shadow hover:bg-gray-50"
    >
      {icon}
      {label}
    </Link>
  )
}

function priorityToneClass(tone: 'amber' | 'rose' | 'blue' | 'emerald') {
  return {
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    rose: 'border-rose-200 bg-rose-50 text-rose-700',
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  }[tone]
}

function PriorityAction({
  href,
  label,
  value,
  detail,
  tone,
}: {
  href: string
  label: string
  value: number
  detail: string
  tone: 'amber' | 'rose' | 'blue' | 'emerald'
}) {
  const toneClass = {
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    rose: 'border-rose-200 bg-rose-50 text-rose-700',
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  }[tone]

  return (
    <Link
      href={href}
      className="group flex items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 hover:border-gray-200 hover:bg-gray-50"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className={`inline-flex min-w-8 justify-center rounded-md border px-2 py-0.5 text-sm font-black ${toneClass}`}>
            {value}
          </span>
          <p className="text-sm font-bold text-gray-900">{label}</p>
        </div>
        <p className="mt-1 truncate text-xs text-gray-500">{detail}</p>
      </div>
      <ArrowRight size={14} className="shrink-0 text-gray-300 group-hover:text-gray-600" />
    </Link>
  )
}

function WorkbenchPanel({
  title,
  empty,
  children,
}: {
  title: string
  empty: string
  children: React.ReactNode
}) {
  const hasChildren = React.Children.count(children) > 0
  return (
    <div className="bg-white nice-shadow rounded-lg border border-gray-100 p-4">
      <h3 className="text-sm font-bold text-gray-900 mb-3">{title}</h3>
      <div className="space-y-2">
        {hasChildren ? children : <p className="text-sm text-gray-400">{empty}</p>}
      </div>
    </div>
  )
}

function cleanAssignmentUuid(uuid: string) {
  return (uuid || '').replace(/^assignment_/, '')
}

function orgAwareDashboardHref(orgslug: string, href?: string) {
  if (!href) return getUriWithOrg(orgslug, '/dash/assignments')
  if (href.startsWith('/')) return getUriWithOrg(orgslug, href)
  return href
}
