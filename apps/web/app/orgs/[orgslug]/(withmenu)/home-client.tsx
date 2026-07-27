'use client'
import React from 'react'
import { useOrg } from '@components/Contexts/OrgContext'
import { useCourses } from '@/hooks/queries/useCourses'
import { useCollections } from '@/hooks/queries/useCollections'
import LandingClassic from '@components/Landings/LandingClassic'
import LandingCustom from '@components/Landings/LandingCustom'
import { JsonLd } from '@components/SEO/JsonLd'
import { getUriWithOrg } from '@services/config/config'
import { getOrgLogoMediaDirectory } from '@services/media/media'
import GeneralWrapperStyled from '@components/Objects/StyledElements/Wrappers/GeneralWrapper'
import AuthenticatedClientElement from '@components/Security/AuthenticatedClientElement'
import ProductHubGrid from '@components/Hub/ProductHubGrid'
import Link from 'next/link'
import { ArrowRight, CheckCircle2, ClipboardList, Clock3, FileSpreadsheet, LayoutDashboard, ListChecks, Loader2, NotebookPen, RotateCcw } from 'lucide-react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useQuery } from '@tanstack/react-query'
import { getMySelfTestAttempts } from '@services/self-tests/self-tests'
import { getMyAssignmentQueue } from '@services/courses/assignments'
import { formatZhHkDate } from '@/lib/date-format'
import { queryKeys } from '@/lib/query/keys'

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || item?.message || String(item)).join('；')
  }
  return fallback
}

function cleanCourseActivityUuid(value: unknown, prefix: 'course_' | 'activity_') {
  return String(value || '').replace(prefix, '')
}

export default function HomeClient({ orgslug }: { orgslug: string }) {
  const org = useOrg() as any
  const orgId = org?.id as number | undefined
  const { data: courses, isLoading: coursesLoading } = useCourses(orgslug)
  const { data: collections, isLoading: collectionsLoading } = useCollections(orgId)

  const landingConfig = org?.config?.config?.customization?.landing || org?.config?.config?.landing
  const hasCustomLanding = landingConfig?.enabled

  const orgJsonLd = org
    ? {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        name: org.name,
        description: org.description,
        url: getUriWithOrg(orgslug, '/'),
        ...(org.logo_image && {
          logo: getOrgLogoMediaDirectory(org.org_uuid, org.logo_image),
        }),
      }
    : null

  if (!org || (!hasCustomLanding && (coursesLoading || collectionsLoading))) {
    return (
      <GeneralWrapperStyled>
        <div className="animate-pulse space-y-6 pt-6">
          <div className="h-6 bg-gray-200 rounded w-40" />
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="bg-white rounded-xl overflow-hidden shadow-sm border border-gray-100">
                <div className="h-[131px] bg-gray-200" />
                <div className="p-3 space-y-2">
                  <div className="h-4 bg-gray-200 rounded w-3/4" />
                  <div className="h-3 bg-gray-200 rounded w-1/2" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </GeneralWrapperStyled>
    )
  }

  return (
    <div className="w-full">
      {orgJsonLd && <JsonLd data={orgJsonLd} />}
      <AuthenticatedClientElement checkMethod="authentication">
        <GeneralWrapperStyled>
          <StudentQuickStart orgslug={orgslug} orgId={orgId} coursesCount={(courses || []).length} />
          <ProductHubGrid />
        </GeneralWrapperStyled>
      </AuthenticatedClientElement>
      {hasCustomLanding ? (
        <LandingCustom landing={landingConfig} orgslug={orgslug} />
      ) : (
        <LandingClassic
          courses={courses || []}
          collections={collections || []}
          orgslug={orgslug}
          org_id={org.id}
        />
      )}
    </div>
  )
}

function StudentQuickStart({
  orgslug,
  orgId,
  coursesCount,
}: {
  orgslug: string
  orgId?: number
  coursesCount: number
}) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const isStaffViewer = isStaffUserForOrg(session, orgId)
  if (isStaffViewer) return <StaffHomeShortcut orgslug={orgslug} />

  return (
    <StudentLearnerQuickStart
      orgslug={orgslug}
      orgId={orgId}
      coursesCount={coursesCount}
      accessToken={accessToken}
    />
  )
}

function StudentLearnerQuickStart({
  orgslug,
  orgId,
  coursesCount,
  accessToken,
}: {
  orgslug: string
  orgId?: number
  coursesCount: number
  accessToken?: string
}) {
  const { data: selfTestAttempts } = useQuery({
    queryKey: queryKeys.selfTests.studentHome(orgId ?? 0),
    queryFn: async () => {
      const response = await getMySelfTestAttempts(Number(orgId), accessToken || '')
      if (response?.success === false) return []
      return Array.isArray(response?.data) ? response.data : []
    },
    enabled: !!orgId && !!accessToken,
    staleTime: 60_000,
  })
  const assignmentQueueQuery = useQuery({
    queryKey: queryKeys.assignments.studentQueue(orgId ?? 0),
    queryFn: async () => {
      const response = await getMyAssignmentQueue(Number(orgId), accessToken || '', 5)
      if (response?.success === false) {
        throw new Error(responseErrorMessage(response, '載入作業待辦失敗'))
      }
      return response?.data || null
    },
    enabled: !!orgId && !!accessToken,
    staleTime: 30_000,
  })
  if (assignmentQueueQuery.isLoading && !assignmentQueueQuery.data) {
    return <StudentLearningLoadingState />
  }

  const assignmentQueue = assignmentQueueQuery.data
  const queueSummary = assignmentQueue?.summary || {}
  const queuedAssignments = Array.isArray(assignmentQueue?.assignments)
    ? assignmentQueue.assignments
    : []
  const todoCount = Number(queueSummary.todo_count || 0)
  const overdueCount = Number(queueSummary.overdue_count || 0)
  const canRetryCount = Number(queueSummary.can_retry_count || 0)
  const retryInProgressCount = Number(queueSummary.retry_count || 0)
  const waitingCount = Number(queueSummary.waiting_count || 0)
  const gradedCount = Number(queueSummary.graded_count || 0)
  const activeAssignments = queuedAssignments.filter((assignment: any) => {
    const state = studentAssignmentState(assignment)
    return state.kind !== 'done'
  })
  const visibleAssignments = activeAssignments.length > 0
    ? activeAssignments
    : queuedAssignments.slice(0, 3)
  const primaryAssignment = visibleAssignments[0]
  const selfTestReadyQuestionCount = Number(queueSummary.self_test_ready_question_count || 0)
  const selfTestMinQuestionCount = Number(queueSummary.self_test_min_question_count || 3)
  const selfTestBankReady = queueSummary.self_test_bank_ready === true
  const selfTestAttemptRows = Array.isArray(selfTestAttempts) ? selfTestAttempts : []
  const latestStartedSelfTest = [...selfTestAttemptRows]
    .filter((attempt: any) => attempt?.status === 'STARTED')
    .sort((left: any, right: any) => Date.parse(right?.creation_date || '') - Date.parse(left?.creation_date || ''))[0]
  const completedSelfTestCount = selfTestAttemptRows.filter((attempt: any) => attempt?.status !== 'STARTED').length
  const statusCards = [
    {
      label: '待做',
      value: overdueCount > 0 ? `${overdueCount} 逾期` : String(Math.max(0, todoCount - retryInProgressCount)),
      detail: overdueCount > 0 ? '先補交逾期作業' : '老師新發布的作業',
      icon: <NotebookPen size={16} />,
      tone: 'amber' as const,
    },
    {
      label: '可重做',
      value: String(canRetryCount + retryInProgressCount),
      detail: retryInProgressCount > 0 ? '已有訂正中的作業' : '做錯可以再改再交',
      icon: <RotateCcw size={16} />,
      tone: 'cyan' as const,
    },
    {
      label: '等批改',
      value: String(waitingCount),
      detail: '已提交，等結果',
      icon: <Clock3 size={16} />,
      tone: 'blue' as const,
    },
    {
      label: '已完成',
      value: String(gradedCount),
      detail: '可查看分數和建議',
      icon: <CheckCircle2 size={16} />,
      tone: 'emerald' as const,
    },
  ]

  return (
    <section className="mb-6 rounded-xl border border-gray-100 bg-white px-4 py-4 nice-shadow">
      {assignmentQueueQuery.isError && (
        <div className="mb-3 flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800 sm:flex-row sm:items-center sm:justify-between">
          <span>
            作業待辦暫時載入失敗。請先打開課程查看老師安排的作業，或稍後再重試。
          </span>
          <button
            type="button"
            onClick={() => assignmentQueueQuery.refetch()}
            className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700"
          >
            重新載入
          </button>
        </div>
      )}
      <div className="space-y-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-wider text-gray-400">今日任務</p>
            <h2 className="mt-1 text-xl font-black text-gray-950">
              {primaryAssignment ? '先完成老師安排的作業' : '今天暫時沒有待做作業'}
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              {primaryAssignment
                ? '只要按第一個任務開始；做錯可以再改再提交，分數和建議會留在作業裡。'
                : coursesCount > 0
                  ? '可以先複習課程；老師發布新作業後，會出現在這裡。'
                  : '老師發布課程或作業後，會出現在這裡。'}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:min-w-[560px]">
            {statusCards.map((card) => (
              <StudentStatusCard key={card.label} {...card} />
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-2">
            {visibleAssignments.length > 0 ? (
              visibleAssignments.map((assignment: any, index: number) => (
                <StudentAssignmentTaskCard
                  key={`${assignment.assignment_uuid}-${assignment.submission_status}`}
                  assignment={assignment}
                  href={studentAssignmentHref(orgslug, assignment)}
                  primary={index === 0}
                />
              ))
            ) : (
              <Link
                href={getUriWithOrg(orgslug, '/courses')}
                className="group flex min-h-20 items-center justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-3 text-gray-800 hover:border-gray-200 hover:bg-white"
              >
                <div className="flex min-w-0 items-start gap-3">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-gray-500">
                    <NotebookPen size={18} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-black">查看課程</p>
                    <p className="mt-1 text-xs leading-snug text-gray-500">
                      暫時沒有待做作業，可以先複習老師發布的課程。
                    </p>
                  </div>
                </div>
                <ArrowRight size={15} className="text-gray-300 group-hover:text-gray-600" />
              </Link>
            )}
          </div>

          <Link
            href={getUriWithOrg(orgslug, '/self-test')}
            className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 hover:border-gray-200 hover:bg-white"
          >
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-gray-600">
                <ClipboardList size={17} />
              </span>
              <div>
                <p className="text-sm font-black text-gray-900">
                  {latestStartedSelfTest ? '繼續自測' : '自我練習'}
                </p>
                <p className="text-[11px] font-semibold text-gray-500">次要練習入口</p>
              </div>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-gray-500">
              {latestStartedSelfTest
                ? '你有一份未完成自測，可以完成後留下分數記錄。'
                : selfTestBankReady
                  ? `題庫已有 ${selfTestReadyQuestionCount} 題，可做 ${Math.min(selfTestReadyQuestionCount, selfTestMinQuestionCount)} 題短練習。`
                  : '老師準備好題庫後，可以在這裡做自我練習。'}
            </p>
            <p className="mt-2 text-[11px] font-bold text-gray-400">
              已完成自測：{completedSelfTestCount} 次
            </p>
          </Link>
        </div>
      </div>
    </section>
  )
}

function studentAssignmentHref(orgslug: string, assignment: any) {
  if (!assignment?.course_uuid || !assignment?.activity_uuid) {
    return getUriWithOrg(orgslug, '/courses')
  }
  return getUriWithOrg(
    orgslug,
    `/course/${cleanCourseActivityUuid(assignment.course_uuid, 'course_')}/activity/${cleanCourseActivityUuid(assignment.activity_uuid, 'activity_')}`
  )
}

function studentAssignmentState(assignment: any) {
  const status = String(assignment?.submission_status || '')
  if (status === 'PENDING') {
    return {
      kind: 'retry',
      label: '繼續訂正',
      cta: '繼續作答',
      tone: 'cyan' as const,
    }
  }
  if (assignment?.overdue || status === 'NOT_SUBMITTED') {
    return {
      kind: 'todo',
      label: assignment?.overdue ? '逾期未交' : '待做',
      cta: assignment?.overdue ? '補交作業' : '開始作業',
      tone: assignment?.overdue ? 'rose' as const : 'amber' as const,
    }
  }
  if (assignment?.can_retry) {
    return {
      kind: 'retry',
      label: '可重做',
      cta: '查看分數/重做',
      tone: 'cyan' as const,
    }
  }
  if (assignment?.waiting_for_grade || status === 'SUBMITTED' || status === 'LATE') {
    return {
      kind: 'waiting',
      label: '等批改',
      cta: '查看提交',
      tone: 'blue' as const,
    }
  }
  return {
    kind: 'done',
    label: '已完成',
    cta: '查看分數',
    tone: 'emerald' as const,
  }
}

function studentAssignmentScoreText(assignment: any) {
  const maxGrade = Number(assignment?.max_grade)
  if (!Number.isFinite(maxGrade) || maxGrade <= 0) return ''
  const scorePolicy = String(assignment?.score_policy || 'highest')
  const grade = scorePolicy === 'highest'
    ? Number(assignment?.best_grade)
    : Number(assignment?.grade)
  if (!Number.isFinite(grade)) return ''
  const label = scorePolicy === 'highest' ? '最高分' : '本次分數'
  return `${label} ${grade}/${maxGrade}`
}

function studentAssignmentRetryText(assignment: any) {
  if (assignment?.submission_status === 'PENDING') return '正在訂正中，改好後再提交。'
  if (!assignment?.can_retry) return ''
  const maxRetries = Number(assignment?.max_retries || 0)
  const attempt = Number(assignment?.attempt_number || 1)
  if (maxRetries > 0) {
    return `還可重做 ${Math.max(0, maxRetries - attempt)} 次。`
  }
  return '做錯可以再改再提交。'
}

function studentToneClass(tone: 'amber' | 'cyan' | 'blue' | 'emerald' | 'rose') {
  return {
    amber: 'border-amber-200 bg-amber-50 text-amber-800',
    cyan: 'border-cyan-200 bg-cyan-50 text-cyan-800',
    blue: 'border-blue-200 bg-blue-50 text-blue-800',
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    rose: 'border-rose-200 bg-rose-50 text-rose-800',
  }[tone]
}

function StudentStatusCard({
  label,
  value,
  detail,
  icon,
  tone,
}: {
  label: string
  value: string
  detail: string
  icon: React.ReactNode
  tone: 'amber' | 'cyan' | 'blue' | 'emerald'
}) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${studentToneClass(tone)}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-black">{label}</span>
        {icon}
      </div>
      <p className="mt-1 text-lg font-black text-gray-950">{value}</p>
      <p className="mt-0.5 text-[10px] font-semibold leading-snug">{detail}</p>
    </div>
  )
}

function StudentAssignmentTaskCard({
  assignment,
  href,
  primary,
}: {
  assignment: any
  href: string
  primary: boolean
}) {
  const state = studentAssignmentState(assignment)
  const scoreText = studentAssignmentScoreText(assignment)
  const retryText = studentAssignmentRetryText(assignment)
  const detail = [
    assignment?.course_name,
    assignment?.due_date ? `截止 ${formatZhHkDate(assignment.due_date, '-')}` : '',
    scoreText,
    retryText,
  ].filter(Boolean).join('｜')

  return (
    <Link
      href={href}
      className={`group flex min-h-20 items-center justify-between gap-3 rounded-lg border px-3 py-3 transition-colors ${
        primary
          ? 'border-gray-950 bg-gray-950 text-white hover:bg-black'
          : 'border-gray-100 bg-gray-50 text-gray-800 hover:border-gray-200 hover:bg-white'
      }`}
    >
      <div className="flex min-w-0 items-start gap-3">
        <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
          primary ? 'bg-white/15 text-white' : 'bg-white text-gray-500'
        }`}>
          {state.kind === 'retry'
            ? <RotateCcw size={18} />
            : state.kind === 'waiting'
              ? <Clock3 size={18} />
              : state.kind === 'done'
                ? <CheckCircle2 size={18} />
                : <NotebookPen size={18} />}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-black">{assignment?.assignment_title || '未命名作業'}</p>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-black ${primary ? 'border-white/20 bg-white/15 text-white' : studentToneClass(state.tone)}`}>
              {state.label}
            </span>
          </div>
          <p className={`mt-1 text-xs leading-snug ${primary ? 'text-white/70' : 'text-gray-500'}`}>
            {detail || '點入作業查看題目和提交狀態。'}
          </p>
        </div>
      </div>
      <span className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-black ${
        primary ? 'bg-white text-gray-950' : 'bg-gray-950 text-white'
      }`}>
        {state.cta}
        <ArrowRight size={13} />
      </span>
    </Link>
  )
}

function StudentLearningLoadingState() {
  return (
    <section className="mb-6 rounded-xl border border-gray-100 bg-white px-4 py-4 nice-shadow">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-950 text-white">
            <Loader2 size={18} className="animate-spin" />
          </span>
          <div>
            <p className="text-xs font-bold uppercase tracking-wider text-gray-400">今日學習</p>
            <h2 className="mt-1 text-lg font-black text-gray-950">正在載入老師安排的作業</h2>
            <p className="mt-1 text-sm text-gray-500">
              請稍等一下，系統正在檢查待做作業、訂正記錄和自測紀錄。
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:min-w-[680px]">
          {['待做作業', '自測記錄', '學習進度'].map((label) => (
            <div key={label} className="min-h-20 rounded-lg border border-gray-100 bg-gray-50 px-3 py-3">
              <div className="h-4 w-20 animate-pulse rounded bg-gray-200" />
              <div className="mt-3 h-3 w-full animate-pulse rounded bg-gray-200" />
              <div className="mt-2 h-3 w-2/3 animate-pulse rounded bg-gray-200" />
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function StaffHomeShortcut({ orgslug }: { orgslug: string }) {
  const actions = [
    {
      label: '老師工作台',
      detail: '查看待批改、未提交和試行狀態',
      href: getUriWithOrg(orgslug, '/dash'),
      icon: <LayoutDashboard size={18} />,
      primary: true,
    },
    {
      label: '建立簡單作業',
      detail: 'AI 或手動出選擇、填空、短問答',
      href: getUriWithOrg(orgslug, '/dash/assignments'),
      icon: <ListChecks size={18} />,
      primary: false,
    },
    {
      label: '成績表',
      detail: '看提交、重做、分數和未交學生',
      href: getUriWithOrg(orgslug, '/dash/gradebook'),
      icon: <FileSpreadsheet size={18} />,
      primary: false,
    },
  ]

  return (
    <section className="mb-6 rounded-xl border border-gray-100 bg-white px-4 py-4 nice-shadow">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-gray-400">校內試行</p>
          <h2 className="mt-1 text-lg font-black text-gray-950">先把簡單作業閉環跑順</h2>
          <p className="mt-1 text-sm text-gray-500">
            老師先發 3 題簡單作業，學生提交後系統自動批改；成績表會保留提交、重做和分數記錄。
          </p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:min-w-[680px]">
          {actions.map((action) => (
            <Link
              key={action.label}
              href={action.href}
              className={`group flex min-h-20 items-center justify-between gap-3 rounded-lg border px-3 py-3 transition-colors ${
                action.primary
                  ? 'border-gray-950 bg-gray-950 text-white hover:bg-black'
                  : 'border-gray-100 bg-gray-50 text-gray-800 hover:border-gray-200 hover:bg-white'
              }`}
            >
              <div className="flex min-w-0 items-start gap-3">
                <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                  action.primary ? 'bg-white/15 text-white' : 'bg-white text-gray-500'
                }`}>
                  {action.icon}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-black">{action.label}</p>
                  <p className={`mt-1 text-xs leading-snug ${action.primary ? 'text-white/70' : 'text-gray-500'}`}>
                    {action.detail}
                  </p>
                </div>
              </div>
              <ArrowRight size={15} className={action.primary ? 'text-white/70' : 'text-gray-300 group-hover:text-gray-600'} />
            </Link>
          ))}
        </div>
      </div>
    </section>
  )
}

function isStaffUserForOrg(session: any, orgId?: number) {
  if (!orgId || session?.status !== 'authenticated') return false
  if (session?.data?.user?.is_superadmin === true) return true

  const roles = Array.isArray(session?.data?.roles) ? session.data.roles : []
  return roles.some((entry: any) => {
    if (Number(entry?.org?.id) !== Number(orgId)) return false
    const role = entry?.role || {}
    const roleUuid = String(role?.role_uuid || '')
    const roleId = Number(role?.id)
    const roleName = String(role?.name || '').toLowerCase()
    if (
      ['role_global_admin', 'role_global_maintainer', 'role_global_instructor'].includes(roleUuid)
      || [1, 2, 3].includes(roleId)
      || ['admin', 'maintainer', 'instructor', 'teacher'].some((keyword) => roleName.includes(keyword))
    ) {
      return true
    }
    return role?.rights?.dashboard?.action_access === true
  })
}
