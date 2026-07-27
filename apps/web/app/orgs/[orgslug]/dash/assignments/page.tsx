'use client';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { useOrg } from '@components/Contexts/OrgContext';
import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { getUriWithOrg } from '@services/config/config';
import { getAssignmentsFromACourse } from '@services/courses/assignments';
import { getCourseThumbnailMediaDirectory } from '@services/media/media';
import { getOrgCourses } from '@services/courses/courses';
import { getUserGroups } from '@services/usergroups/usergroups';
import { useQuery } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import Modal from '@components/Objects/StyledElements/Modal/Modal'
import QuickAssignmentWizard from './QuickAssignmentWizard'
import {
  AlertCircle,
  Backpack,
  Calendar,
  CheckCircle2,
  CirclePlus,
  EyeOff,
  GalleryVerticalEnd,
  Inbox,
  Layers2,
  ListChecks,
  Search,
  Shield,
  FileSpreadsheet,
  UserRoundPen,
  X,
  Zap,
} from 'lucide-react';
import Link from 'next/link';
import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next';
import {
  coerceSimplePilotBoolean,
  getSimplePilotAssignmentMissingSettings,
  getSimplePilotAssignmentTargetUsergroupIds,
} from '@lib/simple-pilot-assignments'
import { formatZhHkDate } from '@/lib/date-format'
import { useSearchParams } from 'next/navigation'

type StatusFilter = 'all' | 'published' | 'drafts' | 'needs_setup';
type CourseAssignmentsLoadResult = {
  assignments: any[]
  error: string | null
}
const STATUS_FILTERS = new Set<StatusFilter>(['all', 'published', 'drafts', 'needs_setup'])

function statusFilterFromParam(value: string | null): StatusFilter {
  return STATUS_FILTERS.has(value as StatusFilter) ? value as StatusFilter : 'all'
}

function getAssignmentEmptyFilterMessage({
  searchQuery,
  statusFilter,
  autoGradedOnly,
}: {
  searchQuery: string
  statusFilter: StatusFilter
  autoGradedOnly: boolean
}) {
  if (statusFilter === 'needs_setup') {
    return {
      title: '目前沒有需要設定的作業。',
      detail: '這通常代表已發布作業已指定有學生的班級，並已完成自動批改、可重做、顯示答案和最高分計分等基本設定。',
    }
  }
  if (autoGradedOnly) {
    return {
      title: '目前沒有符合條件的自動批改作業。',
      detail: '可清除篩選查看全部作業，或先建立選擇題、填空題和短問答的簡單作業。',
    }
  }
  if (searchQuery.trim()) {
    return {
      title: '找不到符合搜尋的作業。',
      detail: '可以清除搜尋字眼，或用科目、單元、作業名稱再試一次。',
    }
  }
  return {
    title: '暫時沒有符合條件的作業。',
    detail: '可以清除篩選條件，回到全部作業列表。',
  }
}

function countInvalidAssignmentTargetUsergroups(assignment: any, usergroups: any[] | undefined) {
  if (!Array.isArray(usergroups)) return 0
  const validIds = new Set(
    usergroups
      .map((usergroup) => Number(usergroup?.id))
      .filter((id) => Number.isFinite(id))
  )
  return getSimplePilotAssignmentTargetUsergroupIds(assignment)
    .filter((id: any) => !validIds.has(Number(id)))
    .length
}

function countEmptyAssignmentTargetUsergroups(assignment: any, usergroups: any[] | undefined) {
  if (!Array.isArray(usergroups)) return 0
  const usergroupsById = new Map<number, any>()
  usergroups.forEach((usergroup) => {
    const id = Number(usergroup?.id)
    if (Number.isFinite(id)) {
      usergroupsById.set(id, usergroup)
    }
  })
  return getSimplePilotAssignmentTargetUsergroupIds(assignment)
    .filter((id: any) => {
      const usergroup = usergroupsById.get(Number(id))
      if (!usergroup || !Object.prototype.hasOwnProperty.call(usergroup, 'member_count')) return false
      return Number(usergroup.member_count) <= 0
    })
    .length
}

function getAssignmentSetupIssues(
  assignment: any,
  orgHasUsergroups: boolean,
  usergroups: any[] | undefined
) {
  const missingSettings = getSimplePilotAssignmentMissingSettings(assignment, {
    requireTargets: true,
    targetLabel: orgHasUsergroups ? '指定班級' : '建立/指定班級',
  })
  const invalidTargetUsergroupCount = countInvalidAssignmentTargetUsergroups(assignment, usergroups)
  const emptyTargetUsergroupCount = countEmptyAssignmentTargetUsergroups(assignment, usergroups)
  const issues = [
    ...missingSettings,
    invalidTargetUsergroupCount > 0 ? '重新指定班級' : '',
    emptyTargetUsergroupCount > 0 ? '把學生加入班級' : '',
  ].filter(Boolean)

  return {
    missingSettings,
    invalidTargetUsergroupCount,
    emptyTargetUsergroupCount,
    issues,
  }
}

// Skeuomorphic badge color presets. Each value combines a vertical gradient
// (lighter at the top, slightly darker at the bottom), a thin colored ring
// for the "edge" of the pill, an inset white highlight for the lifted feel,
// and a soft colored drop shadow tinted to match the badge color. Keep the
// shapes consistent across all badges so they feel like a set.
const BADGE_BASE =
  'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold ring-1 ring-inset whitespace-nowrap'

const BADGE_BLUE =
  'bg-gradient-to-b from-blue-50 to-blue-100 text-blue-700 ring-blue-300/40 shadow-[0_1px_2px_rgba(59,130,246,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]'
const BADGE_EMERALD =
  'bg-gradient-to-b from-emerald-50 to-emerald-100 text-emerald-700 ring-emerald-300/40 shadow-[0_1px_2px_rgba(16,185,129,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]'
const BADGE_AMBER =
  'bg-gradient-to-b from-amber-50 to-amber-100 text-amber-700 ring-amber-300/40 shadow-[0_1px_2px_rgba(245,158,11,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]'
const BADGE_ROSE =
  'bg-gradient-to-b from-rose-50 to-rose-100 text-rose-700 ring-rose-300/40 shadow-[0_1px_2px_rgba(244,63,94,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]'
const BADGE_CYAN =
  'bg-gradient-to-b from-cyan-50 to-cyan-100 text-cyan-700 ring-cyan-300/40 shadow-[0_1px_2px_rgba(6,182,212,0.18),inset_0_1px_0_rgba(255,255,255,0.85)]'

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail || response?.data?.message || response?.HTTPmessage
  if (typeof detail === 'string' && detail.trim()) return detail
  if (response instanceof Error && response.message) return response.message
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || item?.message || '')
      .filter(Boolean)
      .join('；') || fallback
  }
  return fallback
}

async function requireSuccess(responsePromise: Promise<any>, fallback: string) {
  const response = await responsePromise
  if (response?.success === false) {
    throw new Error(responseErrorMessage(response, fallback))
  }
  return response?.data
}

function AssignmentsHome() {
  const { t } = useTranslation()
  const searchParams = useSearchParams()
  const session = useLHSession() as any;
  const access_token = session?.data?.tokens?.access_token;
  const org = useOrg() as any;
  const statusParam = searchParams.get('status')

  const {
    data: courses,
    error: coursesError,
    isFetching: coursesFetching,
    isLoading: coursesLoading,
    refetch: refetchCourses,
  } = useQuery({
    queryKey: queryKeys.courses.list(org?.slug ?? ''),
    queryFn: () => getOrgCourses(org.slug, {}, access_token, true),
    enabled: !!(org?.slug && access_token),
    staleTime: 60_000,
  })
  const courseList = Array.isArray(courses) ? courses : []
  const {
    data: usergroups,
    error: usergroupsError,
    isFetching: usergroupsFetching,
    refetch: refetchUsergroups,
  } = useQuery({
    queryKey: queryKeys.usergroups.list(org?.id),
    queryFn: async () => {
      const response = await getUserGroups(org.id, access_token)
      if (response?.success === false) {
        throw new Error(responseErrorMessage(response, '讀取班級/群組失敗'))
      }
      return Array.isArray(response?.data) ? response.data : response
    },
    enabled: !!(org?.id && access_token),
    staleTime: 60_000,
    retry: 1,
  })
  const usergroupList = Array.isArray(usergroups) ? usergroups : []
  const orgHasUsergroups = usergroupList.length > 0
  // Fetch all course assignments in one query, but keep each course isolated so
  // one broken course does not blank the whole teacher assignment list.
  const courseUuids = useMemo(() => courseList.map((c: any) => c.course_uuid), [courseList])
  const {
    data: courseAssignments,
    error: assignmentsError,
    isFetching: assignmentsFetching,
    isLoading: assignmentsLoading,
    refetch: refetchAssignments,
  } = useQuery({
    queryKey: [...queryKeys.assignments.allCourseAssignments(), ...courseUuids],
    queryFn: async () => {
      const results = await Promise.all(
        courseUuids.map(async (uuid: string): Promise<CourseAssignmentsLoadResult> => {
          try {
            const assignments = await requireSuccess(
              getAssignmentsFromACourse(uuid, access_token),
              '載入作業失敗'
            )
            return {
              assignments: Array.isArray(assignments) ? assignments : [],
              error: null,
            }
          } catch (error) {
            return {
              assignments: [],
              error: responseErrorMessage(error, '載入作業失敗'),
            }
          }
        })
      )
      return results
    },
    enabled: courseUuids.length > 0 && !!access_token,
    staleTime: 60_000,
  })
  const assignmentResults: CourseAssignmentsLoadResult[] = Array.isArray(courseAssignments)
    ? courseAssignments.map((result: any) => {
        if (Array.isArray(result)) {
          return { assignments: result, error: null }
        }
        return {
          assignments: Array.isArray(result?.assignments) ? result.assignments : [],
          error: result?.error || null,
        }
      })
    : []
  const assignmentGroups = assignmentResults.map((result) => result.assignments)
  const assignmentLoadFailures = assignmentResults
    .map((result, index) => result.error ? { course: courseList[index], message: result.error } : null)
    .filter(Boolean) as { course: any; message: string }[]
  const queryError = (coursesError || assignmentsError) as any
  const setupDataError = usergroupsError as any
  const isRetryingList = coursesFetching || assignmentsFetching || usergroupsFetching
  const isInitialLoading = !queryError && (coursesLoading || assignmentsLoading || (courseList.length > 0 && !courseAssignments))

  // === Filter / search state ===
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>(() => (
    statusFilterFromParam(statusParam)
  ))
  const [autoGradedOnly, setAutoGradedOnly] = useState(false)
  const [isQuickAssignmentWizardOpen, setIsQuickAssignmentWizardOpen] = useState(false)

  React.useEffect(() => {
    const nextStatusFilter = statusFilterFromParam(statusParam)
    setStatusFilter(nextStatusFilter)
    if (nextStatusFilter === 'needs_setup') {
      setAutoGradedOnly(false)
    }
  }, [statusParam])

  // === Stats — computed from the unfiltered data ===
  const stats = useMemo(() => {
    const allAssignments: any[] = assignmentGroups.flat()
    return {
      total: allAssignments.length,
      published: allAssignments.filter((a: any) => a.published).length,
      drafts: allAssignments.filter((a: any) => !a.published).length,
      auto_graded: allAssignments.filter((a: any) => coerceSimplePilotBoolean(a.auto_grading)).length,
      settings_ready: allAssignments.filter((a: any) => (
        a.published && getAssignmentSetupIssues(a, orgHasUsergroups, usergroupList).issues.length === 0
      )).length,
      needing_setup: allAssignments.filter((a: any) => (
        a.published && getAssignmentSetupIssues(a, orgHasUsergroups, usergroupList).issues.length > 0
      )).length,
    }
  }, [assignmentGroups, orgHasUsergroups, usergroupList])

  // === Filtering ===
  // Match an assignment against the active filters. Returns true if it should be shown.
  const matchesFilters = (assignment: any) => {
    // Search by title or description (case-insensitive)
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      const title = (assignment.title || '').toLowerCase()
      const desc = (assignment.description || '').toLowerCase()
      if (!title.includes(q) && !desc.includes(q)) return false
    }
    // Status pill
    if (statusFilter === 'published' && !assignment.published) return false
    if (statusFilter === 'drafts' && assignment.published) return false
    if (
      statusFilter === 'needs_setup' &&
      (!assignment.published || getAssignmentSetupIssues(assignment, orgHasUsergroups, usergroupList).issues.length === 0)
    ) return false
    // Auto-graded toggle
    if (autoGradedOnly && !coerceSimplePilotBoolean(assignment.auto_grading)) return false
    return true
  }

  // Build the filtered course rows. Each entry has { course, assignments } where
  // assignments has been filtered. Courses with zero assignments are always
  // hidden — empty courses are noise on this dashboard, the teacher uses the
  // course editor for those.
  const filteredCourseRows = useMemo(() => {
    if (!assignmentGroups.length || !courseList.length) return []
    return assignmentGroups
      .map((assignments: any[], index: number) => {
        const filtered = (assignments || []).filter(matchesFilters)
        return { course: courseList[index], assignments: filtered, originalCount: (assignments || []).length }
      })
      .filter((r: any) => r.assignments.length > 0)
  }, [assignmentGroups, courseList, searchQuery, statusFilter, autoGradedOnly, orgHasUsergroups, usergroupList])

  const filteredAssignmentTotal = filteredCourseRows.reduce(
    (sum: number, row: any) => sum + row.assignments.length,
    0
  )

  function removeAssignmentPrefix(assignment_uuid: string) {
    return assignment_uuid.replace('assignment_', '')
  }

  function removeCoursePrefix(course_uuid: string) {
    return course_uuid.replace('course_', '')
  }

  function selectStatusFilter(nextFilter: StatusFilter) {
    setStatusFilter(nextFilter)
    if (nextFilter === 'needs_setup') {
      setAutoGradedOnly(false)
    }
  }

  function toggleAutoGradedOnly() {
    if (statusFilter === 'needs_setup') {
      setStatusFilter('all')
    }
    setAutoGradedOnly((value) => !value)
  }

  const hasActiveFilters = Boolean(searchQuery || statusFilter !== 'all' || autoGradedOnly)
  const hasAnyAssignments = stats.total > 0
  const emptyFilterMessage = getAssignmentEmptyFilterMessage({
    searchQuery,
    statusFilter,
    autoGradedOnly,
  })
  const firstCourse = courseList[0]
  const firstCourseEditorHref = firstCourse
    ? {
        pathname: getUriWithOrg(org.slug, `/dash/courses/course/${removeCoursePrefix(firstCourse.course_uuid)}/content`),
        query: { subpage: 'editor' },
      }
    : null
  const allAssignmentRows = useMemo(
    () => assignmentGroups.flatMap((assignments: any[]) => assignments || []),
    [assignmentGroups]
  )
  const firstDraftAssignment = allAssignmentRows.find((assignment: any) => !assignment.published)
  const firstNeedsSetupAssignment = allAssignmentRows.find((assignment: any) => (
    assignment.published && getAssignmentSetupIssues(assignment, orgHasUsergroups, usergroupList).issues.length > 0
  ))
  const firstDraftAssignmentHref = firstDraftAssignment
    ? {
        pathname: getUriWithOrg(org.slug, `/dash/assignments/${removeAssignmentPrefix(firstDraftAssignment.assignment_uuid)}`),
        query: { subpage: 'editor' },
      }
    : null
  const firstNeedsSetupAssignmentHref = firstNeedsSetupAssignment
    ? {
        pathname: getUriWithOrg(org.slug, `/dash/assignments/${removeAssignmentPrefix(firstNeedsSetupAssignment.assignment_uuid)}`),
        query: { subpage: 'editor' },
      }
    : null

  return (
    <div className='flex w-full'>
      <div className='pl-4 sm:pl-10 mr-4 sm:mr-10 tracking-tighter flex flex-col space-y-5 w-full'>
        <div className='flex flex-col space-y-2 pt-6'>
          <Breadcrumbs items={[
            { label: t('common.assignments'), href: '/dash/assignments', icon: <Backpack size={14} /> }
          ]} />
          <div className="flex flex-col gap-3 pt-3 sm:flex-row sm:items-center sm:justify-between">
            <h1 className="flex font-bold text-4xl">{t('dashboard.assignments.home.title')}</h1>
            <button
              type="button"
              onClick={() => setIsQuickAssignmentWizardOpen(true)}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
            >
              <CirclePlus size={16} />
              3 步建立作業
            </button>
          </div>
        </div>

        <Modal
          isDialogOpen={isQuickAssignmentWizardOpen}
          onOpenChange={setIsQuickAssignmentWizardOpen}
          minWidth="md"
          minHeight="no-min"
          dialogTitle="3 步建立作業"
          dialogDescription="填課題、選題目來源、發布班級。AI 不可用時可改用題庫或作文題。"
          dialogContent={
            <QuickAssignmentWizard
              courses={courseList}
              usergroups={usergroupList}
              org={org}
              accessToken={access_token}
              onClose={() => setIsQuickAssignmentWizardOpen(false)}
            />
          }
        />

        {/* Stats bar */}
        <div className="flex flex-wrap gap-3">
          <StatPill
            icon={<Backpack size={14} className="text-gray-500" />}
            label={t('dashboard.assignments.home.stats.total')}
            value={stats.total}
          />
          <StatPill
            icon={<CheckCircle2 size={14} className="text-emerald-500" />}
            label={t('dashboard.assignments.home.stats.published')}
            value={stats.published}
          />
          <StatPill
            icon={<EyeOff size={14} className="text-gray-500" />}
            label={t('dashboard.assignments.home.stats.drafts')}
            value={stats.drafts}
          />
          <StatPill
            icon={<Zap size={14} className="text-amber-500" />}
            label={t('dashboard.assignments.home.stats.auto_graded')}
            value={stats.auto_graded}
          />
          <StatPill
            icon={<AlertCircle size={14} className="text-rose-500" />}
            label="需設定"
            value={stats.needing_setup}
          />
        </div>

        {!isInitialLoading && !queryError && (
          <PilotAssignmentQuickStart
            stats={stats}
            firstCourseName={firstCourse?.name}
            firstCourseEditorHref={firstCourseEditorHref}
            firstDraftAssignmentTitle={firstDraftAssignment?.title}
            firstDraftAssignmentHref={firstDraftAssignmentHref}
            firstNeedsSetupAssignmentTitle={firstNeedsSetupAssignment?.title}
            firstNeedsSetupAssignmentHref={firstNeedsSetupAssignmentHref}
            createCourseHref={getUriWithOrg(org.slug, '/dash/courses?new=true')}
            gradebookHref={getUriWithOrg(org.slug, '/dash/gradebook')}
            onStartQuickCreate={() => setIsQuickAssignmentWizardOpen(true)}
            onShowNeedsSetup={() => selectStatusFilter('needs_setup')}
          />
        )}

        {/* Toolbar */}
        <div className='flex flex-col sm:flex-row gap-3 items-stretch sm:items-center'>
          {/* Search input */}
          <div className='relative flex-1 max-w-md'>
            <Search size={14} className='absolute left-3 top-1/2 -translate-y-1/2 text-gray-400' />
            <input
              type='text'
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('dashboard.assignments.home.search_placeholder')}
              className='w-full pl-9 pr-8 py-2 text-sm bg-white nice-shadow rounded-lg focus:outline-none focus:ring-2 focus:ring-black/5 placeholder:text-gray-400'
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className='absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600'
                aria-label='Clear search'
              >
                <X size={14} />
              </button>
            )}
          </div>

          {/* Status pills */}
          <div className='flex gap-1.5'>
            <FilterPill
              label={t('dashboard.assignments.home.filters.all')}
              active={statusFilter === 'all'}
              activeClass='bg-neutral-700 text-white'
              onClick={() => selectStatusFilter('all')}
            />
            <FilterPill
              label={t('dashboard.assignments.home.filters.published')}
              active={statusFilter === 'published'}
              activeClass='bg-emerald-600 text-white'
              onClick={() => selectStatusFilter('published')}
            />
            <FilterPill
              label={t('dashboard.assignments.home.filters.drafts')}
              active={statusFilter === 'drafts'}
              activeClass='bg-gray-700 text-white'
              onClick={() => selectStatusFilter('drafts')}
            />
            <FilterPill
              icon={<AlertCircle size={12} />}
              label="需設定"
              active={statusFilter === 'needs_setup'}
              activeClass='bg-rose-600 text-white'
              onClick={() => selectStatusFilter('needs_setup')}
            />
          </div>

          {/* Toggles */}
          <div className='flex gap-1.5'>
            <FilterPill
              icon={<Zap size={12} />}
              label={t('dashboard.assignments.home.filters.auto_graded')}
              active={autoGradedOnly}
              activeClass='bg-amber-500 text-white'
              onClick={toggleAutoGradedOnly}
            />
            <Link
              href={getUriWithOrg(org.slug, '/dash/gradebook')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-full transition-colors whitespace-nowrap bg-white nice-shadow text-gray-700 hover:bg-gray-50"
            >
              <FileSpreadsheet size={12} />
              成績表
            </Link>
          </div>
        </div>

        {queryError && (
          <div className="flex flex-col gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800 sm:flex-row sm:items-center sm:justify-between">
            <span>{queryError?.message || '作業資料載入失敗，請稍後再試。'}</span>
            <button
              type="button"
              onClick={() => {
                refetchCourses()
                refetchAssignments()
              }}
              disabled={isRetryingList}
              className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isRetryingList ? '重新載入中' : '重新載入作業'}
            </button>
          </div>
        )}

        {!queryError && setupDataError && (
          <div className="flex flex-col gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2">
              <AlertCircle size={16} className="mt-0.5 flex-none" />
              <div>
                <p className="font-bold">班級/群組資料暫時未載入</p>
                <p className="mt-1 text-xs leading-relaxed">
                  {setupDataError?.message || '需設定判斷可能暫時不完整。請重新載入班級後，再確認哪些作業需要指定班級或加入學生。'}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => refetchUsergroups()}
              disabled={usergroupsFetching}
              className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {usergroupsFetching ? '重新載入中' : '重新載入班級'}
            </button>
          </div>
        )}

        {!queryError && assignmentLoadFailures.length > 0 && (
          <div className="flex flex-col gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2">
              <AlertCircle size={16} className="mt-0.5 flex-none" />
              <div>
                <p className="font-bold">
                  有 {assignmentLoadFailures.length} 個課程的作業暫時未載入
                </p>
                <p className="mt-1 text-xs leading-relaxed">
                  其他課程作業仍可正常查看。可以稍後刷新，或先處理已載入的作業。
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => refetchAssignments()}
              disabled={assignmentsFetching}
              className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {assignmentsFetching ? '重新載入中' : '重新載入作業'}
            </button>
          </div>
        )}

        {/* Content */}
        {isInitialLoading && (
          <div className="animate-pulse space-y-6">
            {[1, 2].map((i) => (
              <div key={i} className="flex flex-col space-y-3">
                {/* Course header skeleton */}
                <div className="flex items-center justify-between gap-3 px-1">
                  <div className="flex items-center gap-3">
                    <div className="w-[70px] h-[40px] bg-gray-200 rounded-lg shrink-0" />
                    <div className="flex flex-col gap-1.5">
                      <div className="h-2.5 bg-gray-200 rounded w-16" />
                      <div className="h-5 bg-gray-200 rounded w-48" />
                    </div>
                  </div>
                  <div className="h-8 bg-gray-200 rounded-lg w-32" />
                </div>
                {/* Assignment cards skeleton */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {[1, 2, 3].map((j) => (
                    <div key={j} className="bg-white nice-shadow rounded-xl p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="h-4 bg-gray-100 rounded-full w-20" />
                        <div className="h-3 bg-gray-100 rounded w-16" />
                      </div>
                      <div className="h-5 bg-gray-200 rounded w-3/4" />
                      <div className="space-y-1.5">
                        <div className="h-3 bg-gray-100 rounded w-full" />
                        <div className="h-3 bg-gray-100 rounded w-2/3" />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="h-5 bg-gray-100 rounded-full w-20" />
                        <div className="h-5 bg-gray-100 rounded-full w-16" />
                      </div>
                      <div className="flex gap-2 pt-3 border-t border-gray-100">
                        <div className="h-6 bg-gray-100 rounded-full w-20" />
                        <div className="h-6 bg-gray-100 rounded-full w-24" />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {!queryError && !isInitialLoading && filteredCourseRows.length === 0 && assignmentLoadFailures.length === 0 && (
          hasActiveFilters || hasAnyAssignments ? (
            <div className='flex flex-col items-center justify-center py-16 text-gray-400 gap-3'>
              <div className='bg-gray-100 rounded-2xl p-4'>
                <Inbox size={28} />
              </div>
              <p className='text-sm font-semibold'>
                {hasActiveFilters ? emptyFilterMessage.title : t('dashboard.assignments.home.empty')}
              </p>
              {hasActiveFilters && (
                <p className="max-w-md text-center text-xs leading-relaxed text-gray-400">
                  {emptyFilterMessage.detail}
                </p>
              )}
              {hasActiveFilters && (
                <button
                  onClick={() => {
                    setSearchQuery('')
                    setStatusFilter('all')
                    setAutoGradedOnly(false)
                  }}
                  className='text-xs text-gray-500 hover:text-gray-700 underline'
                >
                  {t('dashboard.assignments.home.clear_filters')}
                </button>
              )}
            </div>
          ) : (
            <FirstAssignmentEmptyState
              courseCount={courseList.length}
              firstCourseName={firstCourse?.name}
              firstCourseEditorHref={firstCourseEditorHref}
              createCourseHref={getUriWithOrg(org.slug, '/dash/courses?new=true')}
              onStartQuickCreate={() => setIsQuickAssignmentWizardOpen(true)}
            />
          )
        )}

        <div className='flex flex-col space-y-3 w-full'>
          {filteredCourseRows.map((row: any) => (
            <CourseCard
              key={row.course?.course_uuid || Math.random()}
              course={row.course}
              assignments={row.assignments}
              originalCount={row.originalCount}
              org={org}
              orgHasUsergroups={orgHasUsergroups}
              usergroups={usergroupList}
              removeAssignmentPrefix={removeAssignmentPrefix}
              removeCoursePrefix={removeCoursePrefix}
            />
          ))}
        </div>

        {/* Filter result summary */}
        {!queryError && filteredCourseRows.length > 0 && (
          <p className='text-xs text-gray-400 pt-1'>
            {t('dashboard.assignments.home.showing_count', {
              shown: filteredAssignmentTotal,
              total: stats.total,
            })}
          </p>
        )}
      </div>
    </div>
  )
}

// ---------- helper components ----------

function FirstAssignmentEmptyState({
  courseCount,
  firstCourseName,
  firstCourseEditorHref,
  createCourseHref,
  onStartQuickCreate,
}: {
  courseCount: number
  firstCourseName?: string
  firstCourseEditorHref: any
  createCourseHref: string
  onStartQuickCreate: () => void
}) {
  const hasCourse = courseCount > 0 && firstCourseEditorHref

  return (
    <div className="rounded-2xl border border-dashed border-gray-300 bg-white px-5 py-8 nice-shadow">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700">
              <Zap size={13} />
              簡單穩定優先
            </div>
            <h2 className="mt-3 text-2xl font-black tracking-tight text-gray-950">
              先建立第一份可自動批改的簡單作業
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-gray-600">
              試行階段建議先用選擇題、填空題和短問答。老師可用 AI、題庫或手動出 3 題，學生提交後系統即時批改，答錯可以再做。
            </p>
            <div className="mt-5 flex flex-col gap-2 sm:flex-row">
              {hasCourse ? (
                <button
                  type="button"
                  onClick={onStartQuickCreate}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
                >
                  <CirclePlus size={16} />
                  3 步建立作業
                </button>
              ) : (
                <Link
                  href={createCourseHref}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
                >
                  <CirclePlus size={16} />
                  先建立課程
                </Link>
              )}
            </div>
            {hasCourse && firstCourseName && (
              <p className="mt-3 text-xs font-semibold text-gray-400">
                會預設放到「{firstCourseName}」，需要時可在第一步改選其他課程。
              </p>
            )}
          </div>

          <div className="grid min-w-0 gap-2 sm:grid-cols-3 lg:w-[480px]">
            <SimpleStep icon={<ListChecks size={17} />} label="出 3 題" detail="AI、題庫或手動" />
            <SimpleStep icon={<CheckCircle2 size={17} />} label="學生提交" detail="答錯可重做" />
            <SimpleStep icon={<FileSpreadsheet size={17} />} label="看成績" detail="未交和分數一眼看清" />
          </div>
        </div>
      </div>
    </div>
  )
}

function PilotAssignmentQuickStart({
  stats,
  firstCourseName,
  firstCourseEditorHref,
  firstDraftAssignmentTitle,
  firstDraftAssignmentHref,
  firstNeedsSetupAssignmentTitle,
  firstNeedsSetupAssignmentHref,
  createCourseHref,
  gradebookHref,
  onStartQuickCreate,
  onShowNeedsSetup,
}: {
  stats: {
    total: number
    published: number
    drafts: number
    auto_graded: number
    settings_ready: number
    needing_setup: number
  }
  firstCourseName?: string
  firstCourseEditorHref: any
  firstDraftAssignmentTitle?: string
  firstDraftAssignmentHref: any
  firstNeedsSetupAssignmentTitle?: string
  firstNeedsSetupAssignmentHref: any
  createCourseHref: string
  gradebookHref: string
  onStartQuickCreate: () => void
  onShowNeedsSetup: () => void
}) {
  const hasCourse = Boolean(firstCourseEditorHref)
  const primaryAction = firstNeedsSetupAssignmentHref
    ? {
        href: firstNeedsSetupAssignmentHref,
        label: '查看需設定作業',
        icon: <AlertCircle size={16} />,
        detail: firstNeedsSetupAssignmentTitle
          ? `先查看「${firstNeedsSetupAssignmentTitle}」缺少哪些試行設定，成績表才會準確。`
          : '先查看需要設定的作業，成績表才會準確。',
      }
    : firstDraftAssignmentHref
      ? {
          href: firstDraftAssignmentHref,
          label: '完成草稿並發布',
          icon: <Layers2 size={16} />,
          detail: firstDraftAssignmentTitle
            ? `先完成「${firstDraftAssignmentTitle}」，再發布給學生。`
            : '先完成現有草稿，不用重複建立新作業。',
        }
      : {
          href: hasCourse ? '' : createCourseHref,
          onClick: hasCourse ? onStartQuickCreate : undefined,
          label: hasCourse ? '建立 3 題簡單作業' : '先建立課程',
          icon: <CirclePlus size={16} />,
          detail: hasCourse && firstCourseName
            ? `會預設放到「${firstCourseName}」，3 步內完成課題、題目和班級發布。`
            : '先建立課程，再新增第一份簡單作業。',
        }
  const statusText = stats.total <= 0
    ? '建議先做一份 3 題短練習，讓學生馬上有提交和分數記錄。'
    : stats.needing_setup > 0
      ? `已有 ${stats.total} 份作業，其中 ${stats.needing_setup} 份需要整理班級、自動批改、可重做、顯示答案或最高分計分。`
      : stats.settings_ready > 0
        ? `已有 ${stats.settings_ready} 份基本設定完整的作業，可以到成績表查看提交和未交名單。`
        : '建議把下一份作業改成選擇、填空、短問答，先跑順自動批改。'

  return (
    <section className="rounded-xl border border-cyan-100 bg-cyan-50/70 px-4 py-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-[11px] font-black text-cyan-700 ring-1 ring-cyan-100">
            <ListChecks size={13} />
            校內試行首選
          </div>
          <h2 className="mt-2 text-lg font-black tracking-tight text-gray-950">
            老師先做一份簡單作業，不用一次設定太多功能
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-gray-600">
            {statusText}
          </p>
          {primaryAction.detail && (
            <p className="mt-1 text-xs font-semibold text-cyan-800">
              {primaryAction.detail}
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col gap-2 sm:flex-row lg:flex-col xl:flex-row">
          {'onClick' in primaryAction && primaryAction.onClick ? (
            <button
              type="button"
              onClick={primaryAction.onClick}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
            >
              {primaryAction.icon}
              {primaryAction.label}
            </button>
          ) : (
            <Link
              href={primaryAction.href}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
            >
              {primaryAction.icon}
              {primaryAction.label}
            </Link>
          )}
          {stats.needing_setup > 0 ? (
            <button
              type="button"
              onClick={onShowNeedsSetup}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-amber-200 bg-white px-4 text-sm font-bold text-amber-700 hover:bg-amber-50"
            >
              <AlertCircle size={16} />
              查看需設定
            </button>
          ) : (
            <Link
              href={gradebookHref}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
            >
              <FileSpreadsheet size={16} />
              看成績表
            </Link>
          )}
        </div>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 border-t border-cyan-100 pt-3 text-xs font-semibold text-cyan-900 sm:grid-cols-3">
        <div className="rounded-lg bg-white/80 px-3 py-2">1. AI、題庫或手動出 3 題</div>
        <div className="rounded-lg bg-white/80 px-3 py-2">2. 發布到指定班級</div>
        <div className="rounded-lg bg-white/80 px-3 py-2">3. 學生提交後自動批改</div>
      </div>
    </section>
  )
}

function SimpleStep({
  icon,
  label,
  detail,
}: {
  icon: React.ReactNode
  label: string
  detail: string
}) {
  return (
    <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-gray-800 nice-shadow">
        {icon}
      </div>
      <p className="mt-3 text-sm font-black text-gray-950">{label}</p>
      <p className="mt-1 text-xs leading-snug text-gray-500">{detail}</p>
    </div>
  )
}

function StatPill({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: number
}) {
  return (
    <div className='flex items-center gap-2 bg-white nice-shadow rounded-xl px-3.5 py-2'>
      {icon}
      <span className='text-[10px] uppercase tracking-wider font-semibold text-gray-400'>
        {label}
      </span>
      <span className='text-sm font-bold text-gray-900'>{value}</span>
    </div>
  )
}

function FilterPill({
  icon,
  label,
  active,
  activeClass,
  onClick,
}: {
  icon?: React.ReactNode
  label: string
  active: boolean
  activeClass: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-full transition-colors whitespace-nowrap ${
        active ? activeClass : 'bg-white nice-shadow text-gray-600 hover:bg-gray-50'
      }`}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

function CourseCard({
  course,
  assignments,
  originalCount,
  org,
  orgHasUsergroups,
  usergroups,
  removeAssignmentPrefix,
  removeCoursePrefix,
}: {
  course: any
  assignments: any[]
  originalCount: number
  org: any
  orgHasUsergroups: boolean
  usergroups: any[] | undefined
  removeAssignmentPrefix: (uuid: string) => string
  removeCoursePrefix: (uuid: string) => string
}) {
  const { t } = useTranslation()

  if (!course) return null

  return (
    <div className='flex flex-col space-y-3'>
      {/* Course header — sits above the assignment grid as a section title.
          No outer card wrapper around the whole course because the assignments
          themselves are now the cards. */}
      <div className='flex items-center justify-between gap-3 px-1'>
        <div className='flex items-center gap-3 min-w-0'>
          <MiniThumbnail course={course} />
          <div className='flex flex-col min-w-0'>
            <span className='text-[10px] uppercase tracking-wider font-bold text-gray-400'>
              {t('dashboard.assignments.home.course_label')} · {assignments.length}
              {assignments.length !== originalCount && (
                <span className='text-gray-300'> / {originalCount}</span>
              )}
            </span>
            <p className='font-bold text-lg text-gray-900 truncate leading-tight'>
              {course.name}
            </p>
          </div>
        </div>
        <Link
          href={{
            pathname: getUriWithOrg(org.slug, `/dash/courses/course/${removeCoursePrefix(course.course_uuid)}/content`),
            query: { subpage: 'editor' },
          }}
          prefetch
          className='bg-black font-semibold text-xs text-zinc-100 rounded-lg flex space-x-1.5 nice-shadow items-center px-3 py-1.5 flex-none hover:bg-gray-800 transition-colors'
        >
          <GalleryVerticalEnd size={14} />
          <p>{t('dashboard.assignments.home.course_editor')}</p>
        </Link>
      </div>

      {/* Assignment grid — 1 column on mobile, 2 on tablet, 3 on desktop */}
      <div className='grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3'>
        {assignments.map((assignment: any) => (
          <AssignmentCard
            key={assignment.assignment_uuid}
            assignment={assignment}
            org={org}
            orgHasUsergroups={orgHasUsergroups}
            usergroups={usergroups}
            removeAssignmentPrefix={removeAssignmentPrefix}
          />
        ))}
      </div>
    </div>
  )
}

function AssignmentCard({
  assignment,
  org,
  orgHasUsergroups,
  usergroups,
  removeAssignmentPrefix,
}: {
  assignment: any
  org: any
  orgHasUsergroups: boolean
  usergroups: any[] | undefined
  removeAssignmentPrefix: (uuid: string) => string
}) {
  const { t } = useTranslation()
  const targetUsergroupIds = getSimplePilotAssignmentTargetUsergroupIds(assignment)
  const targetUsergroupCount = targetUsergroupIds.length
  const setupIssues = getAssignmentSetupIssues(assignment, orgHasUsergroups, usergroups)
  const missingSimplePilotSettings = setupIssues.issues
  const simplePilotSettingsReady = missingSimplePilotSettings.length === 0
  const targetBadgeLabel = setupIssues.invalidTargetUsergroupCount > 0
    ? '班級需重設'
    : setupIssues.emptyTargetUsergroupCount > 0
      ? '班級沒有學生'
      : targetUsergroupCount > 0
        ? `已指定 ${targetUsergroupCount} 個班級`
        : orgHasUsergroups
          ? '未指定班級'
          : '未建立班級'
  const targetBadgeClass = setupIssues.invalidTargetUsergroupCount > 0 || setupIssues.emptyTargetUsergroupCount > 0
    ? BADGE_ROSE
    : targetUsergroupCount > 0
      ? BADGE_BLUE
      : assignment.published
        ? BADGE_ROSE
        : BADGE_AMBER
  const simplePilotStatusLabel = simplePilotSettingsReady
    ? assignment.published ? '基本設定就緒' : '可發布試行'
    : assignment.published ? '需設定試行' : '發布前需設定'
  const missingSettingsIntro = assignment.published ? '請補' : '發布前建議補'
  const missingSettingsDetail = assignment.published
    ? '整理好後，學生才可重做、看答案，成績和提交率會更準確。'
    : '這樣發布後學生可重做、看答案，成績和提交率會更準確。'
  const editorHref = {
    pathname: getUriWithOrg(org.slug, `/dash/assignments/${removeAssignmentPrefix(assignment.assignment_uuid)}`),
    query: { subpage: 'editor' },
  }
  const submissionsHref = {
    pathname: getUriWithOrg(org.slug, `/dash/assignments/${removeAssignmentPrefix(assignment.assignment_uuid)}`),
    query: { subpage: 'submissions' },
  }

  return (
    <div className='group flex flex-col bg-white nice-shadow rounded-xl p-4 hover:bg-gray-50/40 transition-colors'>
      {/* Status indicator strip on the very top */}
      <div className='flex items-center justify-between mb-2'>
        {assignment.published ? (
          <span className='flex items-center gap-1 text-[9px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700'>
            <CheckCircle2 size={10} />
            {t('dashboard.assignments.detail.publishing.published')}
          </span>
        ) : (
          <span className='flex items-center gap-1 text-[9px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500'>
            <EyeOff size={10} />
            {t('dashboard.assignments.detail.publishing.unpublished')}
          </span>
        )}
        {assignment.due_date && (
          <span className='flex items-center gap-1 text-[10px] font-medium text-gray-500'>
            <Calendar size={11} />
            <span>{formatZhHkDate(assignment.due_date)}</span>
          </span>
        )}
      </div>

      {/* Title */}
      <Link
        href={editorHref}
        prefetch
        className='block text-base font-bold text-gray-900 leading-tight hover:text-black mb-1 line-clamp-2 break-words'
      >
        {assignment.title || t('dashboard.assignments.home.untitled')}
      </Link>

      {/* Description — fixed min-height so cards align even when one has no description */}
      <p className='text-xs text-gray-500 line-clamp-2 min-h-[2rem] mb-3 break-words'>
        {assignment.description || ''}
      </p>

      {/* Badges row */}
      <div className='flex items-center gap-1.5 flex-wrap mb-3'>
        {coerceSimplePilotBoolean(assignment.auto_grading) && (
          <span className={`${BADGE_BASE} ${BADGE_AMBER}`}>
            <Zap size={13} />
            <span>{t('dashboard.assignments.detail.header_badges.auto_grading')}</span>
          </span>
        )}
        <span className={`${BADGE_BASE} ${targetBadgeClass}`}>
          <UserRoundPen size={13} />
          <span>{targetBadgeLabel}</span>
        </span>
        <span
          className={`${BADGE_BASE} ${simplePilotSettingsReady ? BADGE_EMERALD : BADGE_AMBER}`}
          title={missingSimplePilotSettings.length > 0 ? `${missingSettingsIntro}：${missingSimplePilotSettings.join('、')}` : undefined}
        >
          {simplePilotSettingsReady ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
          <span>{simplePilotStatusLabel}</span>
        </span>
        {assignment.anti_copy_paste && (
          <span className={`${BADGE_BASE} ${BADGE_CYAN}`}>
            <Shield size={13} />
            <span>{t('dashboard.assignments.detail.header_badges.anti_copy_paste')}</span>
          </span>
        )}
        {assignment.subject && (
          <span className='flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold bg-gray-100 text-gray-700'>
            {assignment.subject}
          </span>
        )}
        {assignment.grade_level && (
          <span className='flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold bg-gray-100 text-gray-700'>
            {assignment.grade_level}
          </span>
        )}
        {assignment.unit && (
          <span className='flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold bg-gray-100 text-gray-700'>
            {assignment.unit}
          </span>
        )}
        {assignment.score_policy === 'highest' && (
          <span className='flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold bg-emerald-50 text-emerald-700'>
            最高分計算
          </span>
        )}
      </div>

      {missingSimplePilotSettings.length > 0 && (
        <div className="mb-3 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800">
          {missingSettingsIntro}：{missingSimplePilotSettings.join('、')}。{missingSettingsDetail}
        </div>
      )}

      {/* Footer actions — pinned to the bottom of the card. Restored to the
          classic white pill-with-nice-shadow look. */}
      <div className='flex items-center gap-2 mt-auto pt-3 border-t border-gray-100'>
        <Link
          href={editorHref}
          prefetch
          className='bg-white rounded-full flex space-x-1.5 nice-shadow items-center px-3 py-1 text-xs font-bold text-gray-700 hover:bg-gray-50 transition-colors'
        >
          <Layers2 size={13} />
          <p>{t('dashboard.assignments.home.editor')}</p>
        </Link>
        <Link
          href={submissionsHref}
          prefetch
          className='bg-white rounded-full flex space-x-1.5 nice-shadow items-center px-3 py-1 text-xs font-bold text-gray-700 hover:bg-gray-50 transition-colors'
        >
          <UserRoundPen size={13} />
          <p>{t('dashboard.assignments.home.submissions')}</p>
        </Link>
      </div>
    </div>
  )
}

const MiniThumbnail = (props: { course: any }) => {
  const org = useOrg() as any

  function removeCoursePrefix(course_uuid: string) {
    return course_uuid.replace('course_', '')
  }

  return (
    <Link
      href={getUriWithOrg(
        org.orgslug,
        '/course/' + removeCoursePrefix(props.course.course_uuid)
      )}
    >
      {props.course.thumbnail_image ? (
        <div
          className="inset-0 ring-1 ring-inset ring-black/10 rounded-lg shadow-xl w-[70px] h-[40px] bg-cover flex-none"
          style={{
            backgroundImage: `url(${getCourseThumbnailMediaDirectory(
              org?.org_uuid,
              props.course.course_uuid,
              props.course.thumbnail_image
            )})`,
          }}
        />
      ) : (
        <div
          className="inset-0 ring-1 ring-inset ring-black/10 rounded-lg shadow-xl w-[70px] h-[40px] bg-cover flex-none"
          style={{
            backgroundImage: `url('/empty_thumbnail.png')`,
            backgroundSize: 'contain',
          }}
        />
      )}
    </Link>
  )
}


export default AssignmentsHome
