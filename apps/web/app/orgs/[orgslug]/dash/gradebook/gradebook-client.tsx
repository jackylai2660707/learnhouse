'use client'

import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import {
  downloadAssignmentGradebookCsv,
  downloadSchoolOperationsSummaryCsv,
  getAssignmentGradebook,
  getAssignmentGradebookSummary,
  getSchoolOperationsSummary,
} from '@services/courses/assignments'
import { getUserGroups } from '@services/usergroups/usergroups'
import { queryKeys } from '@/lib/query/keys'
import { isOperationalGradebookRow } from '@/lib/gradebook-visibility'
import { gradebookMobileRecordLabel } from '@/lib/gradebook-mobile'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ClipboardCheck,
  ClipboardList,
  Copy,
  CalendarDays,
  Download,
  FileSpreadsheet,
  Sparkles,
  RotateCcw,
  Search,
  ShieldCheck,
  UserPlus,
} from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import React from 'react'
import toast from 'react-hot-toast'
import { getUriWithOrg } from '@services/config/config'
import { formatZhHkDate, formatZhHkDateTime } from '@/lib/date-format'
import { coerceSimplePilotBoolean } from '@lib/simple-pilot-assignments'

type Props = {
  orgId: number
  orgslug: string
}

const STATUS_LABELS: Record<string, string> = {
  NOT_SUBMITTED: '未提交',
  PENDING: '重做中',
  SUBMITTED: '已提交',
  LATE: '逾期提交',
  GRADED: '已批改',
  STARTED: '自測中',
  REVIEWED: '已覆核',
}

const REVIEW_LABELS: Record<string, string> = {
  pending: '待老師確認',
  confirmed: '已確認',
  not_required: '系統自動',
  missing: '未提交',
}

type GradebookViewFilter = 'all' | 'attention' | 'unsubmitted' | 'review' | 'low' | 'completed'
type OperationsDatePreset = 'current_week' | 'previous_week' | 'last_30_days' | 'custom'

const GRADEBOOK_VIEW_FILTERS: Array<{
  key: GradebookViewFilter
  label: string
  description: string
}> = [
  { key: 'all', label: '全部', description: '所有記錄' },
  { key: 'attention', label: '待處理', description: '先清急件' },
  { key: 'unsubmitted', label: '未交', description: '追交作業' },
  { key: 'review', label: '待覆核', description: '老師確認分數' },
  { key: 'low', label: '低分', description: '安排補強' },
  { key: 'completed', label: '已完成', description: '已出分記錄' },
]

const OPERATIONS_DATE_PRESETS: Array<{ key: OperationsDatePreset; label: string }> = [
  { key: 'current_week', label: '本週' },
  { key: 'previous_week', label: '上週' },
  { key: 'last_30_days', label: '近 30 日' },
  { key: 'custom', label: '自訂' },
]

function macauTodayUtcDate() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Macau',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return new Date(Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day)))
}

function dateInputValue(value: Date) {
  return value.toISOString().slice(0, 10)
}

function operationsPresetRange(preset: OperationsDatePreset) {
  if (preset === 'current_week' || preset === 'custom') return { start_date: null, end_date: null }
  const today = macauTodayUtcDate()
  if (preset === 'last_30_days') {
    const start = new Date(today)
    start.setUTCDate(start.getUTCDate() - 29)
    return { start_date: dateInputValue(start), end_date: dateInputValue(today) }
  }
  const weekday = (today.getUTCDay() + 6) % 7
  const currentMonday = new Date(today)
  currentMonday.setUTCDate(currentMonday.getUTCDate() - weekday)
  const previousMonday = new Date(currentMonday)
  previousMonday.setUTCDate(previousMonday.getUTCDate() - 7)
  const previousSunday = new Date(previousMonday)
  previousSunday.setUTCDate(previousSunday.getUTCDate() + 6)
  return {
    start_date: dateInputValue(previousMonday),
    end_date: dateInputValue(previousSunday),
  }
}

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail || response?.data?.message || response?.HTTPmessage
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || item?.message || '')
      .filter(Boolean)
      .join('；') || fallback
  }
  return fallback
}

function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([`\uFEFF${text}`], { type: 'text/plain;charset=utf-8' })
  downloadBlobFile(filename, blob)
}

function downloadBlobFile(filename: string, blob: Blob) {
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

async function requireSuccess(responsePromise: Promise<any>, fallback: string) {
  const response = await responsePromise
  if (response?.success === false) {
    throw new Error(responseErrorMessage(response, fallback))
  }
  return response?.data
}

export default function GradebookClient({ orgId, orgslug }: Props) {
  const session = useLHSession() as any
  const searchParams = useSearchParams()
  const accessToken = session?.data?.tokens?.access_token
  const [search, setSearch] = React.useState('')
  const [usergroupId, setUsergroupId] = React.useState('')
  const [includeSelfTests, setIncludeSelfTests] = React.useState(true)
  const attentionParam = searchParams.get('attention')
  const [viewFilter, setViewFilter] = React.useState<GradebookViewFilter>(() => attentionParam === '1' ? 'attention' : 'all')
  const [attentionCopied, setAttentionCopied] = React.useState(false)
  const [summaryCopied, setSummaryCopied] = React.useState(false)
  const [classSummaryCopied, setClassSummaryCopied] = React.useState(false)
  const [operationsCopied, setOperationsCopied] = React.useState(false)
  const [operationsDatePreset, setOperationsDatePreset] = React.useState<OperationsDatePreset>('current_week')
  const [operationsStartDate, setOperationsStartDate] = React.useState('')
  const [operationsEndDate, setOperationsEndDate] = React.useState('')
  const [operationsSubject, setOperationsSubject] = React.useState('')
  const [operationsCourseId, setOperationsCourseId] = React.useState('')
  const [operationsEducationStage, setOperationsEducationStage] = React.useState('')
  const [operationsGradeLevel, setOperationsGradeLevel] = React.useState('')
  const [operationsSchoolYear, setOperationsSchoolYear] = React.useState('')
  const [operationsTerm, setOperationsTerm] = React.useState('')

  React.useEffect(() => {
    if (attentionParam === '1') {
      setViewFilter('attention')
    }
  }, [attentionParam])

  const filters = React.useMemo(
    () => ({
      usergroup_id: usergroupId || null,
      include_self_tests: includeSelfTests,
    }),
    [usergroupId, includeSelfTests]
  )
  const filtersKey = JSON.stringify(filters)
  const operationsFilters = React.useMemo(() => {
    const presetRange = operationsPresetRange(operationsDatePreset)
    return {
      start_date: operationsDatePreset === 'custom' ? operationsStartDate || null : presetRange.start_date,
      end_date: operationsDatePreset === 'custom' ? operationsEndDate || null : presetRange.end_date,
      usergroup_id: usergroupId || null,
      course_id: operationsCourseId || null,
      subject: operationsSubject || null,
      education_stage: operationsEducationStage || null,
      grade_level: operationsGradeLevel || null,
      school_year: operationsSchoolYear || null,
      term: operationsTerm || null,
      include_self_tests: includeSelfTests,
    }
  }, [
    includeSelfTests,
    operationsCourseId,
    operationsDatePreset,
    operationsEducationStage,
    operationsEndDate,
    operationsGradeLevel,
    operationsSchoolYear,
    operationsStartDate,
    operationsSubject,
    operationsTerm,
    usergroupId,
  ])
  const operationsFiltersKey = JSON.stringify(operationsFilters)

  const gradebookQuery = useQuery({
    queryKey: queryKeys.assignments.gradebook(orgId, filtersKey),
    queryFn: async () => requireSuccess(
      getAssignmentGradebook(orgId, accessToken, filters),
      '載入成績表失敗'
    ),
    enabled: !!orgId && !!accessToken,
    staleTime: 30_000,
  })

  const classSummaryQuery = useQuery({
    queryKey: queryKeys.assignments.gradebookSummary(orgId, filtersKey),
    queryFn: async () => requireSuccess(
      getAssignmentGradebookSummary(orgId, accessToken, filters),
      '生成班級摘要失敗'
    ),
    enabled: false,
    retry: false,
  })

  const operationsQuery = useQuery({
    queryKey: queryKeys.assignments.operationsSummary(orgId, operationsFiltersKey),
    queryFn: async () => requireSuccess(
      getSchoolOperationsSummary(orgId, accessToken, operationsFilters),
      '載入校內營運摘要失敗'
    ),
    enabled: !!orgId && !!accessToken,
    staleTime: 30_000,
  })

  const usergroupsQuery = useQuery({
    queryKey: queryKeys.usergroups.list(orgId),
    queryFn: async () => requireSuccess(
      getUserGroups(orgId, accessToken),
      '載入班級/群組失敗'
    ),
    enabled: !!orgId && !!accessToken,
    staleTime: 60_000,
  })

  const gradebook = gradebookQuery.data
  const classSummary = classSummaryQuery.data
  const operationsSummary = operationsQuery.data
  // Keep setup diagnostics in the backend summary, but do not turn an
  // untargeted legacy assignment into one noisy "missing" row per student.
  const rows = Array.isArray(gradebook?.rows)
    ? gradebook.rows.filter(isOperationalGradebookRow)
    : []
  const summary = gradebook?.summary || {}
  const usergroups = Array.isArray(usergroupsQuery.data) ? usergroupsQuery.data : []
  const queryError = (gradebookQuery.error || usergroupsQuery.error) as any
  const isRetryingGradebook = gradebookQuery.isFetching || usergroupsQuery.isFetching
  const searchedRows = rows.filter((row: any) => {
    const q = search.trim().toLowerCase()
    const matchesSearch = !q || [
      row.student_name,
      row.student_email,
      row.assignment_title,
      row.course_name,
      row.subject,
      row.grade_level,
    ].some((value) => String(value || '').toLowerCase().includes(q))
    return matchesSearch
  })
  const filteredRows = searchedRows
    .filter((row: any) => gradebookViewFilterMatches(row, viewFilter))
    .sort(compareGradebookRowsForTeacher)
  const attentionRows = searchedRows
    .filter((row: any) => isGradebookFollowUpRow(row))
    .sort(compareGradebookRowsForTeacher)
  const hasActiveFilters = Boolean(search.trim() || usergroupId || viewFilter !== 'all')
  const hasClientSidePilotFilters = Boolean(search.trim() || viewFilter !== 'all')
  const pilotMetrics = React.useMemo(
    () => buildPilotMetrics(filteredRows, summary, !hasClientSidePilotFilters),
    [filteredRows, hasClientSidePilotFilters, summary]
  )
  const fullScopeMetrics = React.useMemo(
    () => buildPilotMetrics(rows, summary, true),
    [rows, summary]
  )
  const filterCounts = React.useMemo(
    () => buildGradebookFilterCounts(searchedRows),
    [searchedRows]
  )
  const pilotRecommendation = React.useMemo(
    () => buildPilotRecommendation(pilotMetrics),
    [pilotMetrics]
  )
  const pilotHealth = React.useMemo(
    () => buildPilotHealth(pilotMetrics),
    [pilotMetrics]
  )
  const principalEvidenceItems = React.useMemo(
    () => buildPrincipalEvidenceItems(pilotMetrics),
    [pilotMetrics]
  )
  const selectedUsergroup = React.useMemo(
    () => usergroups.find((group: any) => String(group.id) === String(usergroupId)) || null,
    [usergroupId, usergroups]
  )
  const pilotSummaryScope = React.useMemo(
    () => buildPilotSummaryScope({
      selectedUsergroupName: selectedUsergroup?.name,
      hasSearch: Boolean(search.trim()),
      viewFilter,
      includeSelfTests,
    }),
    [includeSelfTests, search, selectedUsergroup?.name, viewFilter]
  )

  function clearFilters() {
    setSearch('')
    setUsergroupId('')
    setViewFilter('all')
  }

  async function exportCsv() {
    if (filteredRows.length === 0) {
      toast('目前畫面沒有可匯出的記錄。')
      return
    }

    const filename = buildGradebookFilename({
      orgslug,
      selectedUsergroupName: selectedUsergroup?.name,
      includeSelfTests,
      viewFilter,
      hasSearch: Boolean(search.trim()),
    })

    if (!hasClientSidePilotFilters && accessToken) {
      try {
        const response = await downloadAssignmentGradebookCsv(orgId, accessToken, filters)
        if (response?.success !== false && response?.data instanceof Blob) {
          downloadBlobFile(filename, response.data)
          toast.success('已匯出含摘要 CSV')
          return
        }
      } catch {
        // Fall through to the local current-view export below.
      }
    }

    const csv = gradebookRowsToCsv(filteredRows, buildGradebookCsvSummaryRows(pilotMetrics))
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' })
    downloadBlobFile(filename, blob)
    toast.success('已匯出目前畫面 CSV')
  }

  async function exportOperationsCsv() {
    if (!accessToken) return
    try {
      const response = await downloadSchoolOperationsSummaryCsv(orgId, accessToken, operationsFilters)
      if (response?.success === false || !(response?.data instanceof Blob)) {
        throw new Error(responseErrorMessage(response, '匯出校內營運摘要失敗'))
      }
      const scope = operationsSummary?.scope || {}
      const filename = `learnhouse-校內營運摘要-${scope.start_date || '開始'}-${scope.end_date || '結束'}.csv`
      downloadBlobFile(filename, response.data)
      toast.success('已匯出聚合營運摘要 CSV')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '匯出校內營運摘要失敗')
    }
  }

  async function copyOperationsSummary() {
    if (!operationsSummary) {
      toast('營運摘要尚未載入。')
      return
    }
    const scope = operationsSummary.scope || {}
    const metrics = operationsSummary.metrics || {}
    const workload = operationsSummary.workload || {}
    const privacy = operationsSummary.privacy || {}
    const narrative = operationsSummary.summary || {}
    const hiddenLabel = '已按小群組私隱規則隱藏'
    const text = [
      'LearnHouse 澳門校內營運摘要',
      `摘要範圍：${scope.start_date || ''} 至 ${scope.end_date || ''}（${scope.timezone || 'Asia/Macau'}）`,
      `狀態：${narrative.status_label || '本期摘要'}`,
      `校長/主任摘要：${narrative.principal_brief || ''}`,
      `學生人數：${metrics.learner_count || 0} 名`,
      `有學習記錄學生：${metrics.engaged_student_count || 0} 名`,
      `學習記錄：${metrics.record_count || 0} 條`,
      `提交：${metrics.submitted || 0} 條`,
      `提交率：${metrics.submission_rate == null ? privacy.suppressed ? hiddenLabel : '暫無資料' : `${Math.round(metrics.submission_rate)}%`}`,
      `已批改：${metrics.graded || 0} 條`,
      `待覆核：${metrics.pending_review || 0} 條`,
      `未提交：${metrics.unsubmitted || 0} 條`,
      `平均分：${metrics.average_score == null ? privacy.suppressed ? hiddenLabel : '暫無評分' : `${Math.round(metrics.average_score)}%`}`,
      `自動批改：${metrics.auto_graded_records || 0} 條`,
      `估算節省時間：約 ${workload.estimated_teacher_minutes_saved || 0} 分鐘`,
      `估算方法：${workload.methodology || ''}`,
      `私隱保護：${privacy.suppressed ? '小群組敏感指標已隱藏' : '只包含聚合資料'}`,
      `下一步：${narrative.next_step || ''}`,
    ].join('\n')

    try {
      const result = await copyTextOrDownload(text, 'learnhouse-校內營運摘要.txt')
      setOperationsCopied(true)
      toast.success(result === 'copied' ? '已複製聚合營運摘要' : '剪貼簿不可用，已下載營運摘要')
      window.setTimeout(() => setOperationsCopied(false), 1800)
    } catch {
      toast.error('無法複製或下載校內營運摘要。')
    }
  }

  async function copyAttentionList() {
    if (attentionRows.length === 0) {
      toast('目前沒有待處理記錄。')
      return
    }

    const text = attentionRows
      .map((row: any, index: number) => formatAttentionRow(row, index))
      .join('\n')

    try {
      const result = await copyTextOrDownload(text, 'learnhouse-待處理名單.txt')
      setAttentionCopied(true)
      toast.success(result === 'copied' ? '已複製待處理名單' : '剪貼簿不可用，已下載待處理名單')
      window.setTimeout(() => setAttentionCopied(false), 1800)
    } catch {
      toast.error('無法複製或下載待處理名單，請手動選取。')
    }
  }

  async function copyPilotSummary() {
    const text = [
      'LearnHouse 校內試行成績摘要',
      `摘要範圍：${pilotSummaryScope}`,
      `校內試行狀態：${pilotMetrics.pilotStatusLabel || pilotHealth.label}`,
      `30 秒校長匯報：${buildPrincipalBriefing(pilotMetrics, pilotHealth, pilotRecommendation)}`,
      ...(pilotMetrics.principalEvidenceSummary ? [`校長展示摘要：${pilotMetrics.principalEvidenceSummary}`] : []),
      `核心作業閉環：${pilotHealth.label} - ${pilotHealth.detail}`,
      '核心證據：',
      `已匯入學生：${pilotMetrics.learnerCount}`,
      `已有學習記錄學生：${pilotMetrics.engagedStudentCount}`,
      `學生使用率：${pilotMetrics.engagementRateDisplay}`,
      `學習記錄：${pilotMetrics.totalRecords}`,
      `記錄參與率：${pilotMetrics.participationRateDisplay}`,
      `提交率：${pilotMetrics.submissionRateDisplay}`,
      `平均分（計入評分）：${pilotMetrics.averageScoreDisplay}`,
      `計入評分記錄：${pilotMetrics.scoredRecords}`,
      `待跟進：${pilotMetrics.attentionCount}`,
      `重做中：${pilotMetrics.retryInProgress}`,
      `重做/再練習記錄：${pilotMetrics.retryRecords}`,
      `已批改/已覆核：${pilotMetrics.gradedCount}`,
      `校內試行就緒作業：${pilotMetrics.autoGradedAssignments}`,
      `需整理已發布作業：${pilotMetrics.publishedNeedingSetup}`,
      `班級需重設或需加學生作業：${pilotMetrics.publishedWithoutTargets}`,
      `班級沒有學生作業：${pilotMetrics.publishedEmptyTargetUsergroups}`,
      '簡化原則：作業只用選擇、填空、短問答；可用 AI、題庫或手動出題；學生提交後自動批改，答錯可重做，顯示答案，成績取最高分。',
      '展示重點：',
      ...principalEvidenceItems.map((item, index) => `${index + 1}. ${item.label}：${item.detail}`),
      includeSelfTests ? '包含：作業與自測記錄' : '包含：作業記錄',
      `建議下一步：${pilotRecommendation}`,
      '加分項：',
      `AI 省力出題：${aiAssignmentStatusLabel(pilotMetrics.aiAssignmentReady)}`,
      `自測記錄：${pilotMetrics.selfTestRecords}`,
    ].join('\n')

    try {
      const result = await copyTextOrDownload(text, 'learnhouse-校內試行摘要.txt')
      setSummaryCopied(true)
      toast.success(result === 'copied' ? '已複製校內試行摘要' : '剪貼簿不可用，已下載校內試行摘要')
      window.setTimeout(() => setSummaryCopied(false), 1800)
    } catch {
      toast.error('無法複製或下載摘要，請手動選取。')
    }
  }

  async function generateClassSummary() {
    if (!rows.length && !summary?.learner_count) {
      toast('目前沒有足夠資料生成班級摘要。')
      return
    }
    const result = await classSummaryQuery.refetch()
    if (result.error) {
      toast.error((result.error as Error)?.message || '生成班級摘要失敗，請稍後再試。')
      return
    }
    toast.success(result.data?.ai_used ? '已生成 AI 班級摘要' : '已生成系統統計摘要')
  }

  async function copyClassSummary() {
    if (!classSummary) {
      toast('請先生成班級摘要。')
      return
    }
    const narrative = classSummary.narrative || {}
    const facts = classSummary.facts || {}
    const metrics = facts.metrics || {}
    const display = facts.display || {}
    const weakAreas = Array.isArray(facts.weak_areas) ? facts.weak_areas : []
    const followUpStudents = Array.isArray(facts.follow_up_students) ? facts.follow_up_students : []
    const text = [
      narrative.title || '班級學情摘要',
      classSummary.ai_used ? '來源：AI 根據成績表生成' : '來源：系統統計摘要',
      classSummary.ai_message ? `提示：${classSummary.ai_message}` : '',
      '',
      narrative.summary || '',
      '',
      '重點：',
      ...(Array.isArray(narrative.key_points) ? narrative.key_points.map((item: string, index: number) => `${index + 1}. ${item}`) : []),
      '',
      '建議行動：',
      ...(Array.isArray(narrative.suggested_actions) ? narrative.suggested_actions.map((item: string, index: number) => `${index + 1}. ${item}`) : []),
      '',
      `提交率：${display.submission_rate || '暫無資料'}`,
      `平均分：${display.average_score || '暫無評分'}`,
      `未提交：${metrics.unsubmitted || 0}`,
      `待覆核：${metrics.pending_review || 0}`,
      `低分需補強：${metrics.low_score_records || 0}`,
      weakAreas.length ? `主要跟進範圍：${weakAreas.map((item: any) => item.label).join('、')}` : '',
      followUpStudents.length ? '需跟進學生：' : '',
      ...followUpStudents.map((student: any, index: number) => `${index + 1}. ${student.student_name}${student.student_email ? `（${student.student_email}）` : ''}：${(student.reasons || []).join('、')}`),
      narrative.principal_brief ? '' : '',
      narrative.principal_brief ? `主任/校長短報告：${narrative.principal_brief}` : '',
    ].filter((line) => line !== undefined).join('\n')

    try {
      const result = await copyTextOrDownload(text, 'learnhouse-班級學情摘要.txt')
      setClassSummaryCopied(true)
      toast.success(result === 'copied' ? '已複製班級摘要' : '剪貼簿不可用，已下載班級摘要')
      window.setTimeout(() => setClassSummaryCopied(false), 1800)
    } catch {
      toast.error('無法複製或下載班級摘要，請手動選取。')
    }
  }

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-4 py-6 sm:px-8">
      <div className="mx-auto max-w-[1500px] space-y-5">
        <Breadcrumbs
          items={[
            { label: '成績表', href: '/dash/gradebook', icon: <FileSpreadsheet size={14} /> },
          ]}
        />

        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-wider text-gray-400">
              作業與評分閉環
            </p>
            <h1 className="mt-1 text-3xl font-black tracking-tight text-gray-950">成績表</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              查看全班提交狀態、自動批改結果、老師覆核狀態，並匯出 CSV 作校內記錄。
            </p>
          </div>
          <button
            type="button"
            onClick={exportCsv}
            disabled={gradebookQuery.isLoading || !filteredRows.length}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-800 hover:border-gray-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download size={16} />
            匯出目前畫面 CSV
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <SummaryCard icon={<FileSpreadsheet size={18} />} label="提交率" value={fullScopeMetrics.submissionRateDisplay} tone="cyan" />
          <SummaryCard icon={<ClipboardCheck size={18} />} label="待覆核" value={fullScopeMetrics.pendingReviewCount} tone="amber" />
          <SummaryCard icon={<AlertCircle size={18} />} label="未交" value={fullScopeMetrics.unsubmittedCount} tone="rose" />
          <SummaryCard icon={<RotateCcw size={18} />} label="低分需補強" value={fullScopeMetrics.needsPracticeRecords} tone="blue" />
        </div>

        <SchoolOperationsPanel
          payload={operationsSummary}
          loading={operationsQuery.isLoading}
          fetching={operationsQuery.isFetching}
          error={operationsQuery.error as Error | null}
          datePreset={operationsDatePreset}
          startDate={operationsStartDate}
          endDate={operationsEndDate}
          subject={operationsSubject}
          courseId={operationsCourseId}
          educationStage={operationsEducationStage}
          gradeLevel={operationsGradeLevel}
          schoolYear={operationsSchoolYear}
          term={operationsTerm}
          copied={operationsCopied}
          onDatePresetChange={setOperationsDatePreset}
          onStartDateChange={setOperationsStartDate}
          onEndDateChange={setOperationsEndDate}
          onSubjectChange={setOperationsSubject}
          onCourseIdChange={setOperationsCourseId}
          onEducationStageChange={setOperationsEducationStage}
          onGradeLevelChange={setOperationsGradeLevel}
          onSchoolYearChange={setOperationsSchoolYear}
          onTermChange={setOperationsTerm}
          onRetry={() => operationsQuery.refetch()}
          onCopy={copyOperationsSummary}
          onExport={exportOperationsCsv}
        />

        <ClassSummaryPanel
          summary={classSummary}
          loading={classSummaryQuery.isFetching}
          copied={classSummaryCopied}
          disabled={gradebookQuery.isLoading || (!rows.length && !summary?.learner_count)}
          onGenerate={generateClassSummary}
          onCopy={copyClassSummary}
        />

        {queryError && (
          <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800 sm:flex-row sm:items-center sm:justify-between">
            <span>{queryError?.message || '成績表資料載入失敗，請稍後再試。'}</span>
            <button
              type="button"
              onClick={() => {
                gradebookQuery.refetch()
                usergroupsQuery.refetch()
              }}
              disabled={isRetryingGradebook}
              className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isRetryingGradebook ? '重新載入中' : '重新載入成績表'}
            </button>
          </div>
        )}

        <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-4 flex flex-wrap gap-2">
            {GRADEBOOK_VIEW_FILTERS.map((filter) => {
              const active = viewFilter === filter.key
              return (
                <button
                  key={filter.key}
                  type="button"
                  onClick={() => setViewFilter(filter.key)}
                  className={`inline-flex h-10 items-center gap-2 rounded-lg border px-3 text-sm font-black transition-colors ${
                    active
                      ? 'border-gray-950 bg-gray-950 text-white'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                  title={filter.description}
                >
                  <span>{filter.label}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] ${active ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-500'}`}>
                    {filterCounts[filter.key]}
                  </span>
                </button>
              )
            })}
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_220px_auto_auto]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜尋學生、作業、科目或年級"
                className="h-10 w-full rounded-lg border border-gray-200 pl-9 pr-3 text-sm outline-none focus:border-gray-900"
              />
            </div>
            <select
              value={usergroupId}
              onChange={(event) => setUsergroupId(event.target.value)}
              className="h-10 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
            >
              <option value="">全部班級/群組</option>
              {usergroups.map((group: any) => (
                <option key={group.id} value={group.id}>{group.name}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={copyAttentionList}
              disabled={!attentionRows.length}
              className="flex h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 px-3 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {attentionCopied ? <Check size={15} /> : <Copy size={15} />}
              複製待處理
            </button>
            <label className="flex h-10 items-center gap-2 rounded-lg border border-gray-200 px-3 text-sm font-semibold text-gray-700">
              <input
                type="checkbox"
                checked={includeSelfTests}
                onChange={(event) => setIncludeSelfTests(event.target.checked)}
              />
              包含自測
            </label>
          </div>
          <p className="mt-3 text-xs font-semibold text-gray-500">
            預設會把待覆核、未交、低分和逾期記錄排在前面；目前顯示 {filteredRows.length} 條記錄。
          </p>
        </section>

        <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="p-3 lg:hidden" data-testid="gradebook-mobile-list">
            {gradebookQuery.isLoading && (
              <div className="px-4 py-10 text-center text-sm text-gray-400">載入成績表...</div>
            )}
            {!queryError && !gradebookQuery.isLoading && filteredRows.length === 0 && (
              <div className="px-2 py-8 text-center">
                <GradebookEmptyState
                  hasActiveFilters={hasActiveFilters}
                  viewFilter={viewFilter}
                  learnerCount={pilotMetrics.learnerCount}
                  publishedWithoutTargets={pilotMetrics.publishedWithoutTargets}
                  publishedEmptyTargetUsergroups={pilotMetrics.publishedEmptyTargetUsergroups}
                  onClearFilters={clearFilters}
                  assignmentsHref={getUriWithOrg(orgslug, '/dash/assignments')}
                  usersHref={getUriWithOrg(orgslug, '/dash/users/settings/add')}
                  selfTestsHref={getUriWithOrg(orgslug, '/dash/self-tests')}
                />
              </div>
            )}
            <div className="space-y-3">
              {filteredRows.map((row: any) => (
                <GradebookMobileRow key={`${row.source_type}-${row.assignment_uuid}-${row.student_id}`} row={row} orgslug={orgslug} />
              ))}
            </div>
          </div>

          <div className="hidden overflow-x-auto lg:block">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs font-bold uppercase tracking-wider text-gray-500">
                <tr>
                  <th className="px-4 py-3">學生</th>
                  <th className="px-4 py-3">作業/自測</th>
                  <th className="px-4 py-3">科目與年級</th>
                  <th className="px-4 py-3">提交</th>
                  <th className="px-4 py-3">覆核</th>
                  <th className="px-4 py-3 text-right">分數</th>
                  <th className="px-4 py-3">時間</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {gradebookQuery.isLoading && (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-gray-400">載入成績表...</td>
                  </tr>
                )}
                {!queryError && !gradebookQuery.isLoading && filteredRows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center">
                      <GradebookEmptyState
                        hasActiveFilters={hasActiveFilters}
                        viewFilter={viewFilter}
                        learnerCount={pilotMetrics.learnerCount}
                        publishedWithoutTargets={pilotMetrics.publishedWithoutTargets}
                        publishedEmptyTargetUsergroups={pilotMetrics.publishedEmptyTargetUsergroups}
                        onClearFilters={clearFilters}
                        assignmentsHref={getUriWithOrg(orgslug, '/dash/assignments')}
                        usersHref={getUriWithOrg(orgslug, '/dash/users/settings/add')}
                        selfTestsHref={getUriWithOrg(orgslug, '/dash/self-tests')}
                      />
                    </td>
                  </tr>
                )}
                {filteredRows.map((row: any) => (
                  <tr key={`${row.source_type}-${row.assignment_uuid}-${row.student_id}`} className="hover:bg-gray-50/70">
                    <td className="px-4 py-3">
                      <p className="font-bold text-gray-950">{row.student_name}</p>
                      <p className="text-xs text-gray-500">{row.student_email}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-bold text-gray-900">{row.assignment_title}</p>
                      <p className="text-xs text-gray-500">{row.course_name || (row.source_type === 'self_test' ? '自測系統' : '')}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="text-gray-800">{row.subject || '未設定科目'}</p>
                      <p className="text-xs text-gray-500">{row.grade_level || '未設定年級'} {row.unit ? `· ${row.unit}` : ''}</p>
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge label={STATUS_LABELS[row.submission_status] || row.submission_status} late={row.late} />
                    </td>
                    <td className="px-4 py-3">
                      <ReviewBadge row={row} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <p className="font-black text-gray-950">
                        {gradebookScoreLabel(row)}
                      </p>
                      {gradebookPercentageLabel(row) && (
                        <p className="text-xs text-gray-500">{gradebookPercentageLabel(row)}</p>
                      )}
                      <ScoreEvidence row={row} />
                      <RemediationEvidence row={row} />
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">
                      <p>交：{formatZhHkDateTime(row.submitted_at)}</p>
                      <p>截：{formatZhHkDate(row.due_date)}</p>
                    </td>
                    <td className="px-4 py-3">
                      {row.source_type === 'assignment' ? (
                        <Link
                          href={getUriWithOrg(orgslug, `/dash/assignments/${cleanAssignmentUuid(row.assignment_uuid)}?subpage=submissions`)}
                          className={`inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-bold ${assignmentActionClass(row)}`}
                        >
                          <ClipboardList size={13} />
                          {assignmentActionLabel(row)}
                        </Link>
                      ) : (
                        <Link
                          href={getUriWithOrg(orgslug, '/dash/self-tests')}
                          className="inline-flex items-center gap-1 rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-bold text-gray-700 hover:bg-gray-200"
                        >
                          自測
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <details>
          <summary className="inline-flex h-10 cursor-pointer items-center rounded-lg border border-cyan-100 bg-white px-3 text-sm font-black text-cyan-900 hover:bg-cyan-50">
            校長摘要與試行證據
          </summary>
          <div className="mt-4">
            <PilotSummaryPanel
              metrics={pilotMetrics}
              health={pilotHealth}
              recommendation={pilotRecommendation}
              evidenceItems={principalEvidenceItems}
              scopeText={pilotSummaryScope}
              copied={summaryCopied}
              onCopy={copyPilotSummary}
            />
          </div>
        </details>
      </div>
    </div>
  )
}

function GradebookMobileRow({ row, orgslug }: { row: any; orgslug: string }) {
  const isAssignment = row.source_type === 'assignment'
  const actionHref = isAssignment
    ? getUriWithOrg(orgslug, `/dash/assignments/${cleanAssignmentUuid(row.assignment_uuid)}?subpage=submissions`)
    : getUriWithOrg(orgslug, '/dash/self-tests')
  const actionLabel = isAssignment ? assignmentActionLabel(row) : '查看自測'
  const actionClass = isAssignment
    ? assignmentActionClass(row)
    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'

  return (
    <article
      aria-label={gradebookMobileRecordLabel(row)}
      className="min-w-0 rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-black text-gray-950">{row.student_name || '未命名學生'}</p>
          {row.student_email && <p className="mt-0.5 break-all text-xs text-gray-500">{row.student_email}</p>}
        </div>
        <div className="shrink-0">
          <StatusBadge label={STATUS_LABELS[row.submission_status] || row.submission_status} late={row.late} />
        </div>
      </div>

      <div className="mt-3 min-w-0 rounded-md bg-gray-50 px-3 py-2.5">
        <p className="break-words text-sm font-bold text-gray-900">{row.assignment_title || '未命名作業'}</p>
        <p className="mt-1 break-words text-xs text-gray-500">
          {row.course_name || (row.source_type === 'self_test' ? '自測系統' : '未設定課程')}
        </p>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <GradebookMobileField label="科目與年級">
          <span>{row.subject || '未設定科目'}</span>
          <span className="block text-gray-500">{row.grade_level || '未設定年級'}{row.unit ? ` · ${row.unit}` : ''}</span>
        </GradebookMobileField>
        <GradebookMobileField label="老師覆核">
          <ReviewBadge row={row} />
        </GradebookMobileField>
        <GradebookMobileField label="分數">
          <span className="text-sm font-black text-gray-950">{gradebookScoreLabel(row)}</span>
          {gradebookPercentageLabel(row) && <span className="ml-1 text-gray-500">{gradebookPercentageLabel(row)}</span>}
          <ScoreEvidence row={row} />
          <RemediationEvidence row={row} />
        </GradebookMobileField>
        <GradebookMobileField label="時間">
          <span className="block">交：{formatZhHkDateTime(row.submitted_at)}</span>
          <span className="mt-0.5 block text-gray-500">截：{formatZhHkDate(row.due_date)}</span>
        </GradebookMobileField>
      </dl>

      <Link
        href={actionHref}
        className={`mt-4 inline-flex min-h-10 w-full items-center justify-center gap-1 rounded-lg px-3 py-2 text-sm font-bold ${actionClass}`}
      >
        <ClipboardList size={15} />
        {actionLabel}
      </Link>
    </article>
  )
}

function GradebookMobileField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-md bg-gray-50 px-3 py-2">
      <dt className="font-bold text-gray-500">{label}</dt>
      <dd className="mt-1 min-w-0 break-words font-semibold leading-relaxed text-gray-800">{children}</dd>
    </div>
  )
}

function SchoolOperationsPanel({
  payload,
  loading,
  fetching,
  error,
  datePreset,
  startDate,
  endDate,
  subject,
  courseId,
  educationStage,
  gradeLevel,
  schoolYear,
  term,
  copied,
  onDatePresetChange,
  onStartDateChange,
  onEndDateChange,
  onSubjectChange,
  onCourseIdChange,
  onEducationStageChange,
  onGradeLevelChange,
  onSchoolYearChange,
  onTermChange,
  onRetry,
  onCopy,
  onExport,
}: {
  payload: any
  loading: boolean
  fetching: boolean
  error: Error | null
  datePreset: OperationsDatePreset
  startDate: string
  endDate: string
  subject: string
  courseId: string
  educationStage: string
  gradeLevel: string
  schoolYear: string
  term: string
  copied: boolean
  onDatePresetChange: (value: OperationsDatePreset) => void
  onStartDateChange: (value: string) => void
  onEndDateChange: (value: string) => void
  onSubjectChange: (value: string) => void
  onCourseIdChange: (value: string) => void
  onEducationStageChange: (value: string) => void
  onGradeLevelChange: (value: string) => void
  onSchoolYearChange: (value: string) => void
  onTermChange: (value: string) => void
  onRetry: () => void
  onCopy: () => void
  onExport: () => void
}) {
  const scope = payload?.scope || {}
  const metrics = payload?.metrics || {}
  const workload = payload?.workload || {}
  const privacy = payload?.privacy || {}
  const narrative = payload?.summary || {}
  const available = payload?.available_filters || {}
  const protectedText = privacy.suppressed ? '已隱藏' : '暫無資料'
  const metricItems = [
    ['有記錄學生', `${Number(metrics.engaged_student_count || 0)}/${Number(metrics.learner_count || 0)} 名`],
    ['學習記錄', `${Number(metrics.record_count || 0)} 條`],
    ['提交率', metrics.submission_rate == null ? protectedText : `${Math.round(Number(metrics.submission_rate))}%`],
    ['平均分', metrics.average_score == null ? protectedText : `${Math.round(Number(metrics.average_score))}%`],
    ['待覆核', `${Number(metrics.pending_review || 0)} 條`],
    ['未提交', `${Number(metrics.unsubmitted || 0)} 條`],
    ['自動批改', `${Number(metrics.auto_graded_records || 0)} 條`],
    ['估算減工', `約 ${Number(workload.estimated_teacher_minutes_saved || 0)} 分鐘`],
  ]

  return (
    <section className="overflow-hidden rounded-lg border border-emerald-200 bg-white shadow-sm">
      <div className="border-b border-emerald-100 bg-emerald-50/60 p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <p className="inline-flex items-center gap-2 text-sm font-black text-emerald-900">
              <ShieldCheck size={16} />
              校長/主任聚合摘要
            </p>
            <h2 className="mt-1 text-lg font-black text-gray-950">
              {narrative.status_label || (loading ? '正在整理摘要' : '指定期間營運證據')}
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-gray-700">
              {narrative.principal_brief || '只顯示聚合資料，不包含學生姓名、電郵、答案或檔案資料。'}
            </p>
            {scope.start_date && scope.end_date && (
              <p className="mt-2 inline-flex items-center gap-1.5 text-xs font-bold text-emerald-800">
                <CalendarDays size={14} />
                {scope.start_date} 至 {scope.end_date} · {scope.timezone || 'Asia/Macau'}
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onCopy}
              disabled={!payload || fetching}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 text-xs font-black text-emerald-800 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              複製摘要
            </button>
            <button
              type="button"
              onClick={onExport}
              disabled={!payload || fetching}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-emerald-800 px-3 text-xs font-black text-white hover:bg-emerald-900 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              <Download size={14} />
              聚合 CSV
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="inline-flex w-full overflow-x-auto rounded-lg border border-emerald-200 bg-white p-1 lg:w-auto">
            {OPERATIONS_DATE_PRESETS.map((preset) => (
              <button
                key={preset.key}
                type="button"
                onClick={() => onDatePresetChange(preset.key)}
                className={`h-8 shrink-0 rounded-md px-3 text-xs font-black ${
                  datePreset === preset.key
                    ? 'bg-emerald-800 text-white'
                    : 'text-gray-600 hover:bg-emerald-50'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <select
            value={subject}
            onChange={(event) => onSubjectChange(event.target.value)}
            className="h-10 w-full rounded-lg border border-emerald-200 bg-white px-3 text-sm font-semibold outline-none focus:border-emerald-700 lg:w-52"
          >
            <option value="">全部科目</option>
            {(available.subjects || []).map((item: string) => <option key={item} value={item}>{item}</option>)}
          </select>
          <p className="text-xs font-semibold text-gray-600">班級範圍跟隨下方成績表的班級選擇。</p>
        </div>

        {datePreset === 'custom' && (
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:max-w-xl">
            <label className="text-xs font-bold text-gray-700">
              開始日期
              <input
                type="date"
                value={startDate}
                onChange={(event) => onStartDateChange(event.target.value)}
                className="mt-1 h-10 w-full rounded-lg border border-emerald-200 bg-white px-3 text-sm outline-none focus:border-emerald-700"
              />
            </label>
            <label className="text-xs font-bold text-gray-700">
              結束日期
              <input
                type="date"
                value={endDate}
                onChange={(event) => onEndDateChange(event.target.value)}
                className="mt-1 h-10 w-full rounded-lg border border-emerald-200 bg-white px-3 text-sm outline-none focus:border-emerald-700"
              />
            </label>
          </div>
        )}

        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-black text-emerald-800">更多篩選</summary>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <select value={courseId} onChange={(event) => onCourseIdChange(event.target.value)} className="h-10 rounded-lg border border-emerald-200 bg-white px-3 text-sm">
              <option value="">全部課程</option>
              {(available.courses || []).map((item: any) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <select value={educationStage} onChange={(event) => onEducationStageChange(event.target.value)} className="h-10 rounded-lg border border-emerald-200 bg-white px-3 text-sm">
              <option value="">全部學段</option>
              {(available.education_stages || []).map((item: string) => <option key={item} value={item}>{item}</option>)}
            </select>
            <select value={gradeLevel} onChange={(event) => onGradeLevelChange(event.target.value)} className="h-10 rounded-lg border border-emerald-200 bg-white px-3 text-sm">
              <option value="">全部年級</option>
              {(available.grade_levels || []).map((item: string) => <option key={item} value={item}>{item}</option>)}
            </select>
            <select value={schoolYear} onChange={(event) => onSchoolYearChange(event.target.value)} className="h-10 rounded-lg border border-emerald-200 bg-white px-3 text-sm">
              <option value="">全部學年</option>
              {(available.school_years || []).map((item: string) => <option key={item} value={item}>{item}</option>)}
            </select>
            <select value={term} onChange={(event) => onTermChange(event.target.value)} className="h-10 rounded-lg border border-emerald-200 bg-white px-3 text-sm">
              <option value="">全部學期</option>
              {(available.terms || []).map((item: string) => <option key={item} value={item}>{item}</option>)}
            </select>
          </div>
        </details>
      </div>

      {error && (
        <div className="flex flex-col gap-2 border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-900 sm:flex-row sm:items-center sm:justify-between">
          <span>{error.message || '校內營運摘要載入失敗，成績表仍可正常使用。'}</span>
          <button type="button" onClick={onRetry} disabled={fetching} className="h-8 rounded-lg border border-amber-300 bg-white px-3 text-xs font-black disabled:opacity-60">
            {fetching ? '重新載入中' : '重新載入摘要'}
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 divide-x divide-y divide-gray-100 sm:grid-cols-4 xl:grid-cols-8">
        {metricItems.map(([label, value]) => (
          <div key={label} className="min-w-0 px-3 py-4">
            <p className="text-[11px] font-bold text-gray-500">{label}</p>
            <p className="mt-1 break-words text-base font-black text-gray-950">{loading ? '...' : value}</p>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-2 border-t border-gray-100 px-4 py-3 text-xs font-semibold text-gray-600 lg:flex-row lg:items-center lg:justify-between">
        <span>{privacy.suppressed ? `群組少於 ${privacy.minimum_cohort_size || 5} 人，已隱藏比率和成績。` : '此區只顯示聚合資料，不顯示學生答案或身分資料。'}</span>
        <span>{workload.methodology || '自動批改減工量使用透明估算，不冒充實際 AI 出題次數。'}</span>
      </div>
    </section>
  )
}

function ClassSummaryPanel({
  summary,
  loading,
  copied,
  disabled,
  onGenerate,
  onCopy,
}: {
  summary: any
  loading: boolean
  copied: boolean
  disabled: boolean
  onGenerate: () => void
  onCopy: () => void
}) {
  const narrative = summary?.narrative || {}
  const facts = summary?.facts || {}
  const metrics = facts.metrics || {}
  const display = facts.display || {}
  const weakAreas = Array.isArray(facts.weak_areas) ? facts.weak_areas : []
  const followUpStudents = Array.isArray(facts.follow_up_students) ? facts.follow_up_students : []
  const keyPoints = Array.isArray(narrative.key_points) ? narrative.key_points : []
  const suggestedActions = Array.isArray(narrative.suggested_actions) ? narrative.suggested_actions : []

  return (
    <section className="rounded-lg border border-violet-100 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-3xl">
          <p className="inline-flex items-center gap-2 text-sm font-black text-violet-900">
            <Sparkles size={16} />
            AI 班級學情摘要
          </p>
          <p className="mt-1 text-sm leading-relaxed text-gray-600">
            一鍵把目前成績表整理成提交率、未交、低分、待覆核和教學建議；AI 不可用時會改用系統統計摘要。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onGenerate}
            disabled={disabled || loading}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-violet-700 px-4 text-sm font-black text-white hover:bg-violet-800 disabled:cursor-not-allowed disabled:bg-gray-400"
          >
            <Sparkles size={15} />
            {loading ? '生成中' : summary ? '重新生成' : '生成班級摘要'}
          </button>
          <button
            type="button"
            onClick={onCopy}
            disabled={!summary || loading}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {copied ? <Check size={15} /> : <Copy size={15} />}
            複製摘要
          </button>
        </div>
      </div>

      {!summary && (
        <div className="mt-4 rounded-lg border border-dashed border-violet-200 bg-violet-50 px-4 py-3 text-sm font-semibold text-violet-900">
          老師可先篩選班級，再生成摘要；摘要會跟隨目前的班級和是否包含自測設定。
        </div>
      )}

      {summary && (
        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[1.4fr_1fr]">
          <div className="space-y-3">
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-black text-gray-950">{narrative.title || '班級學情摘要'}</h2>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-black ${summary.ai_used ? 'bg-violet-100 text-violet-800' : 'bg-amber-100 text-amber-800'}`}>
                  {summary.ai_used ? 'AI 生成' : '系統統計'}
                </span>
                {summary.requires_attention && (
                  <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-black text-rose-700">
                    需跟進
                  </span>
                )}
              </div>
              {summary.ai_message && (
                <p className="mt-2 text-xs font-semibold text-amber-700">{summary.ai_message}</p>
              )}
              <p className="mt-3 text-sm leading-relaxed text-gray-700">{narrative.summary}</p>
            </div>

            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <MiniMetric label="提交率" value={display.submission_rate || '暫無資料'} />
              <MiniMetric label="平均分" value={display.average_score || '暫無評分'} />
              <MiniMetric label="未提交" value={metrics.unsubmitted || 0} />
              <MiniMetric label="待覆核" value={metrics.pending_review || 0} />
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <SummaryList title="重點" items={keyPoints} empty="暫無重點。" />
              <SummaryList title="建議行動" items={suggestedActions} empty="暫無建議。" ordered />
            </div>
          </div>

          <div className="space-y-3">
            <div className="rounded-lg border border-gray-100 bg-white px-4 py-3">
              <p className="text-sm font-black text-gray-900">主要跟進範圍</p>
              <div className="mt-2 space-y-2">
                {weakAreas.length === 0 && (
                  <p className="text-xs font-semibold text-gray-500">暫時沒有明顯弱項。</p>
                )}
                {weakAreas.slice(0, 3).map((area: any) => (
                  <div key={area.label} className="rounded-lg bg-gray-50 px-3 py-2">
                    <p className="text-xs font-black text-gray-800">{area.label}</p>
                    <p className="mt-0.5 text-[11px] font-semibold text-gray-500">
                      {area.count} 條記錄 · {area.student_count} 名學生
                    </p>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-lg border border-gray-100 bg-white px-4 py-3">
              <p className="text-sm font-black text-gray-900">需跟進學生</p>
              <div className="mt-2 space-y-2">
                {followUpStudents.length === 0 && (
                  <p className="text-xs font-semibold text-gray-500">暫時沒有學生需要特別跟進。</p>
                )}
                {followUpStudents.slice(0, 4).map((student: any) => (
                  <div key={`${student.student_id}-${student.student_email}`} className="rounded-lg bg-gray-50 px-3 py-2">
                    <p className="truncate text-xs font-black text-gray-800">{student.student_name}</p>
                    <p className="mt-0.5 line-clamp-2 text-[11px] font-semibold text-gray-500">
                      {(student.reasons || []).join('、')}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            {narrative.principal_brief && (
              <div className="rounded-lg border border-cyan-100 bg-cyan-50 px-4 py-3">
                <p className="text-sm font-black text-cyan-900">主任/校長短報告</p>
                <p className="mt-1 text-xs font-semibold leading-relaxed text-cyan-800">{narrative.principal_brief}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

function MiniMetric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-white px-3 py-2">
      <p className="text-[11px] font-bold text-gray-500">{label}</p>
      <p className="mt-1 text-lg font-black text-gray-950">{value}</p>
    </div>
  )
}

function SummaryList({
  title,
  items,
  empty,
  ordered = false,
}: {
  title: string
  items: string[]
  empty: string
  ordered?: boolean
}) {
  const ListTag = ordered ? 'ol' : 'ul'
  return (
    <div className="rounded-lg border border-gray-100 bg-white px-4 py-3">
      <p className="text-sm font-black text-gray-900">{title}</p>
      {items.length === 0 ? (
        <p className="mt-2 text-xs font-semibold text-gray-500">{empty}</p>
      ) : (
        <ListTag className="mt-2 space-y-1.5 text-xs font-semibold leading-relaxed text-gray-600">
          {items.slice(0, 4).map((item, index) => (
            <li key={`${title}-${index}`} className={ordered ? 'ml-4 list-decimal' : 'ml-4 list-disc'}>
              {item}
            </li>
          ))}
        </ListTag>
      )}
    </div>
  )
}

function GradebookEmptyState({
  hasActiveFilters,
  viewFilter,
  learnerCount,
  publishedWithoutTargets,
  publishedEmptyTargetUsergroups,
  onClearFilters,
  assignmentsHref,
  usersHref,
  selfTestsHref,
}: {
  hasActiveFilters: boolean
  viewFilter: GradebookViewFilter
  learnerCount: number
  publishedWithoutTargets: number
  publishedEmptyTargetUsergroups: number
  onClearFilters: () => void
  assignmentsHref: string
  usersHref: string
  selfTestsHref: string
}) {
  if (hasActiveFilters) {
    const title = viewFilter !== 'all'
      ? '目前沒有待處理記錄。'
      : '暫時沒有符合條件的記錄。'
    const detail = viewFilter !== 'all'
      ? '可以切換分類或清除篩選，查看其他學生和作業記錄。'
      : '可以清除搜尋、班級或篩選條件，回到全部成績。'

    return (
      <div className="flex flex-col items-center gap-3 text-gray-400">
        <div className="max-w-md space-y-1">
          <p className="text-sm font-semibold text-gray-600">{title}</p>
          <p className="text-xs leading-relaxed text-gray-400">{detail}</p>
        </div>
        <button
          type="button"
          onClick={onClearFilters}
          className="inline-flex h-9 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
        >
          清除篩選
        </button>
      </div>
    )
  }

  if (publishedWithoutTargets > 0) {
    return (
      <div className="mx-auto max-w-4xl text-left">
        <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 px-5 py-5">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
            <div className="max-w-xl">
              <p className="text-xs font-black uppercase tracking-wider text-amber-700">
                發布範圍需整理
              </p>
              <h2 className="mt-2 text-xl font-black tracking-tight text-gray-950">
                已發布作業暫時沒有可計入的學生記錄
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-gray-600">
                有 {publishedWithoutTargets} 份已發布作業未指定班級、班級已不存在或班級沒有學生
                {publishedEmptyTargetUsergroups > 0 ? `；其中 ${publishedEmptyTargetUsergroups} 份要先把學生加入班級` : ''}。
                先整理發布範圍，學生提交後成績表才會有可靠記錄。
              </p>
            </div>
            <div className="flex shrink-0 flex-col gap-2 sm:flex-row lg:flex-col">
              <Link
                href={assignmentsHref}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
              >
                <ClipboardList size={16} />
                整理作業
              </Link>
              <Link
                href={usersHref}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-amber-200 bg-white px-4 text-sm font-bold text-amber-800 hover:bg-amber-50"
              >
                <UserPlus size={16} />
                匯入/分配學生
              </Link>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (learnerCount <= 0) {
    return (
      <div className="mx-auto max-w-4xl text-left">
        <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 px-5 py-5">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
            <div className="max-w-xl">
              <p className="text-xs font-black uppercase tracking-wider text-amber-700">
                第一次試行
              </p>
              <h2 className="mt-2 text-xl font-black tracking-tight text-gray-950">
                先匯入學生，再建立 3 題簡單作業
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-gray-600">
                成績表目前沒有學生對象。先用 CSV 批量新增學生並分配班級，之後老師發布作業，學生提交後才會形成提交率、分數和待跟進名單。
              </p>
            </div>
            <div className="flex shrink-0 flex-col gap-2 sm:flex-row lg:flex-col">
              <Link
                href={usersHref}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
              >
                <UserPlus size={16} />
                匯入學生
              </Link>
              <Link
                href={assignmentsHref}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-amber-200 bg-white px-4 text-sm font-bold text-amber-800 hover:bg-amber-50"
              >
                <ClipboardList size={16} />
                建立簡單作業
              </Link>
            </div>
          </div>
          <div className="mt-5 grid grid-cols-1 gap-2 md:grid-cols-3">
            <PilotStarterStep
              icon={<UserPlus size={16} />}
              title="1. 匯入學生"
              detail="用 CSV 新增帳號，並分配到班級。"
            />
            <PilotStarterStep
              icon={<ClipboardList size={16} />}
              title="2. 發簡單作業"
              detail="只用選擇、填空、短問答。"
            />
            <PilotStarterStep
              icon={<FileSpreadsheet size={16} />}
              title="3. 看學習證據"
              detail="提交率、平均分和未提交名單自動整理。"
            />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl text-left">
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-5 py-5">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-xl">
            <p className="text-xs font-black uppercase tracking-wider text-cyan-700">
              第一次試行
            </p>
            <h2 className="mt-2 text-xl font-black tracking-tight text-gray-950">
              先做一份 3 題簡單作業，成績表就會有第一批學習證據
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-gray-600">
              老師可用 AI、題庫或手動建立選擇、填空、短問答。學生提交後系統自動批改，這裡會顯示提交率、分數、自測和待跟進名單。
            </p>
          </div>
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row lg:flex-col">
            <Link
              href={assignmentsHref}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
            >
              <ClipboardList size={16} />
              去建立簡單作業
            </Link>
            <Link
              href={selfTestsHref}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
            >
              <CheckCircle2 size={16} />
              查看自測記錄
            </Link>
          </div>
        </div>
        <div className="mt-5 grid grid-cols-1 gap-2 md:grid-cols-3">
          <PilotStarterStep
            icon={<ClipboardList size={16} />}
            title="1. 建 3 題"
            detail="AI、題庫或手動；答案一併儲存。"
          />
          <PilotStarterStep
            icon={<CheckCircle2 size={16} />}
            title="2. 學生提交"
            detail="答錯可以再做，形成再練習記錄。"
          />
          <PilotStarterStep
            icon={<FileSpreadsheet size={16} />}
            title="3. 老師看證據"
            detail="提交率、平均分、待跟進名單自動整理。"
          />
        </div>
      </div>
    </div>
  )
}

function PilotStarterStep({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode
  title: string
  detail: string
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-cyan-50 text-cyan-700">
          {icon}
        </span>
        <div>
          <p className="text-sm font-black text-gray-950">{title}</p>
          <p className="mt-1 text-xs leading-relaxed text-gray-500">{detail}</p>
        </div>
      </div>
    </div>
  )
}

function PilotSummaryPanel({
  metrics,
  health,
  recommendation,
  evidenceItems,
  scopeText,
  copied,
  onCopy,
}: {
  metrics: ReturnType<typeof buildPilotMetrics>
  health: ReturnType<typeof buildPilotHealth>
  recommendation: string
  evidenceItems: ReturnType<typeof buildPrincipalEvidenceItems>
  scopeText: string
  copied: boolean
  onCopy: () => void
}) {
  return (
    <section className="rounded-lg border border-cyan-100 bg-cyan-50/70 p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="max-w-2xl">
          <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">
            校長可看摘要
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-black text-gray-950">
              學生有沒有用上平台，一眼看清
            </h2>
            <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-black ${health.className}`}>
              {health.label}
            </span>
          </div>
          <p className="mt-1 text-sm leading-relaxed text-gray-600">
            用現有作業和自測記錄生成簡單摘要，方便老師向學校展示參與率、提交率、計分平均分和需跟進學生。
          </p>
          <p className="mt-2 text-xs font-semibold leading-relaxed text-gray-700">
            校長視角：{health.detail}
          </p>
          <p className="mt-2 rounded-lg border border-cyan-100 bg-white/75 px-3 py-2 text-xs font-black leading-relaxed text-gray-900">
            30 秒校長匯報：{buildPrincipalBriefing(metrics, health, recommendation)}
          </p>
          <p className="mt-1 text-xs font-semibold leading-relaxed text-cyan-800">
            摘要範圍：{scopeText}
          </p>
          <p className="mt-1 text-xs font-semibold leading-relaxed text-cyan-800">
            簡化原則：選擇、填空、短問答；自動批改；答錯可重做；最高分計分。
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:min-w-[840px] xl:grid-cols-6">
          <PilotMetric label="已匯入學生" value={metrics.learnerCountDisplay} detail={`${metrics.engagedStudentCountDisplay} 名已有學習記錄`} />
          <PilotMetric label="學生使用率" value={metrics.engagementRateDisplay} detail={`${metrics.engagedStudentCountDisplay}/${metrics.learnerCountDisplay} 名已有記錄`} />
          <PilotMetric label="平均分" value={metrics.averageScoreDisplay} detail={`${metrics.passingScoreRecordsDisplay} 達標 / ${metrics.needsPracticeRecordsDisplay} 補強`} />
          <PilotMetric label="自測記錄" value={metrics.selfTestRecordsDisplay} detail="學生自主練習" />
          <PilotMetric label="待跟進" value={metrics.attentionCountDisplay} detail="未交、逾期或待覆核" />
          <PilotMetric label="重做記錄" value={metrics.retryRecordsDisplay} detail={`${metrics.retryInProgressDisplay} 條重做中`} />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-2 border-t border-cyan-100 pt-3 lg:grid-cols-3">
        {evidenceItems.map((item) => (
          <div key={item.label} className={`rounded-lg border bg-white/85 px-3 py-3 ${principalEvidenceToneClass(item.tone)}`}>
            <p className="text-[11px] font-black uppercase tracking-wider opacity-70">{item.label}</p>
            <p className="mt-1 text-sm font-black text-gray-950">{item.value}</p>
            <p className="mt-1 text-xs font-semibold leading-relaxed text-gray-600">{item.detail}</p>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-col gap-3 border-t border-cyan-100 pt-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-2">
          <p className="text-xs font-semibold text-cyan-800">
            {metrics.totalRecords > 0
              ? `目前有 ${metrics.totalRecords} 條學習記錄，${metrics.gradedCount} 條已批改或已覆核，當中 ${metrics.selfTestRecords} 條是學生自測。`
              : '暫時沒有學習記錄，建立作業後這裡會自動顯示摘要。'}
          </p>
          <p className="text-xs font-semibold text-cyan-800">
            校內試行就緒作業：{metrics.autoGradedAssignments} 份
            {metrics.publishedNeedingSetup > 0 ? `，${metrics.publishedNeedingSetup} 份需要整理` : '。'}
          </p>
          {metrics.publishedWithoutTargets > 0 && (
            <p className="text-xs font-semibold text-amber-800">
              發佈範圍：{metrics.publishedWithoutTargets} 份已發布作業未指定班級/群組、班級已不存在或班級沒有學生
              {metrics.publishedEmptyTargetUsergroups > 0 ? `；其中 ${metrics.publishedEmptyTargetUsergroups} 份要先把學生加入班級` : ''}。
            </p>
          )}
          <p className="text-xs font-semibold text-cyan-800">
            重做/再練習：{metrics.retryRecords} 條記錄，現在 {metrics.retryInProgress} 條重做中。
          </p>
          <p className="text-xs font-semibold text-gray-700">
            下一步建議：{recommendation}
          </p>
        </div>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-cyan-700 px-3 text-xs font-bold text-white hover:bg-cyan-800"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          複製摘要
        </button>
      </div>
    </section>
  )
}

function PilotMetric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="rounded-lg border border-white bg-white/85 px-3 py-3">
      <p className="text-xs font-bold text-gray-500">{label}</p>
      <p className="mt-1 text-xl font-black text-gray-950">{value}</p>
      <p className="mt-1 text-[11px] font-medium text-gray-500">{detail}</p>
    </div>
  )
}

function SummaryCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  tone: 'amber' | 'rose' | 'blue' | 'emerald' | 'cyan'
}) {
  const tones = {
    amber: 'bg-amber-50 text-amber-700 border-amber-100',
    rose: 'bg-rose-50 text-rose-700 border-rose-100',
    blue: 'bg-blue-50 text-blue-700 border-blue-100',
    emerald: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    cyan: 'bg-cyan-50 text-cyan-700 border-cyan-100',
  }
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <span className={`inline-flex h-9 w-9 items-center justify-center rounded-lg border ${tones[tone]}`}>
          {icon}
        </span>
        <span className="text-2xl font-black text-gray-950">{value}</span>
      </div>
      <p className="mt-3 text-xs font-bold text-gray-500">{label}</p>
    </div>
  )
}

function StatusBadge({ label, late }: { label: string; late: boolean }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-1 text-xs font-bold ${late ? 'bg-rose-50 text-rose-700' : 'bg-gray-100 text-gray-700'}`}>
      {label}
    </span>
  )
}

function gradebookReviewLabel(row: any) {
  if (row?.submission_status === 'PENDING' && row?.teacher_review_status === 'missing') {
    return '等待重新提交'
  }
  return REVIEW_LABELS[row?.teacher_review_status] || row?.teacher_review_status || '-'
}

function ReviewBadge({ row }: { row: any }) {
  const state = row?.teacher_review_status
  const label = gradebookReviewLabel(row)
  const className =
    row?.submission_status === 'PENDING' && state === 'missing'
      ? 'bg-blue-50 text-blue-700'
      : state === 'pending'
      ? 'bg-amber-50 text-amber-700'
      : state === 'confirmed'
        ? 'bg-emerald-50 text-emerald-700'
        : 'bg-gray-100 text-gray-700'
  return <span className={`inline-flex rounded-full px-2 py-1 text-xs font-bold ${className}`}>{label}</span>
}

function ScoreEvidence({ row }: { row: any }) {
  const attemptNumber = numberOrNull(row.attempt_number) || 0
  const bestAttemptNumber = numberOrNull(row.best_attempt_number) || 0
  const bestGrade = numberOrNull(row.best_grade)
  const maxGrade = numberOrNull(row.max_grade)
  const scorePolicy = String(row.score_policy || '')
  const hasRetry = attemptNumber > 1
  const hasBestScore = scorePolicy === 'highest' && bestAttemptNumber > 0 && bestGrade !== null && maxGrade !== null && maxGrade > 0
  const isExcludedFromGrade = isExplicitFalse(row?.counts_for_grade)

  if (!hasRetry && !hasBestScore && !isExcludedFromGrade) return null

  return (
    <div className="mt-1 flex flex-wrap justify-end gap-1">
      {isExcludedFromGrade && (
        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-600">
          不計入評分
        </span>
      )}
      {hasRetry && (
        <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700">
          第 {attemptNumber} 次
        </span>
      )}
      {hasBestScore && (
        <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
          最高分 {bestGrade}/{maxGrade}
          {bestAttemptNumber > 0 ? ` · 第 ${bestAttemptNumber} 次` : ''}
        </span>
      )}
    </div>
  )
}

function RemediationEvidence({ row }: { row: any }) {
  const remediation = row?.remediation || {}
  if (!remediation?.eligible && remediation?.status !== 'completed') return null

  const status = remediation.status
  const label =
    status === 'completed'
      ? `補練 ${remediation.score ?? 0}/${remediation.max_score ?? 0}`
      : remediation.label || '未補練'
  const className =
    status === 'completed'
      ? 'bg-emerald-50 text-emerald-700'
      : status === 'generated'
        ? 'bg-amber-50 text-amber-700'
        : 'bg-orange-50 text-orange-700'

  return (
    <div className="mt-1 flex justify-end">
      <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${className}`}>
        {label}
      </span>
    </div>
  )
}

function gradebookViewFilterMatches(row: any, filter: GradebookViewFilter) {
  if (filter === 'all') return true
  if (filter === 'attention') return isGradebookFollowUpRow(row)
  if (filter === 'unsubmitted') return row?.submission_status === 'NOT_SUBMITTED'
  if (filter === 'review') return row?.teacher_review_status === 'pending'
  if (filter === 'low') return isLowScoreRow(row)
  if (filter === 'completed') return isCompletedGradebookRow(row)
  return true
}

function buildGradebookFilterCounts(rows: any[]): Record<GradebookViewFilter, number> {
  return {
    all: rows.length,
    attention: rows.filter((row: any) => isGradebookFollowUpRow(row)).length,
    unsubmitted: rows.filter((row: any) => gradebookViewFilterMatches(row, 'unsubmitted')).length,
    review: rows.filter((row: any) => gradebookViewFilterMatches(row, 'review')).length,
    low: rows.filter((row: any) => gradebookViewFilterMatches(row, 'low')).length,
    completed: rows.filter((row: any) => gradebookViewFilterMatches(row, 'completed')).length,
  }
}

function isGradebookFollowUpRow(row: any) {
  return isAttentionRow(row) || isLowScoreRow(row)
}

function isLowScoreRow(row: any) {
  if (isExplicitFalse(row?.counts_for_grade)) return false
  const percentage = percentageFromRow(row)
  return percentage !== null && percentage < 60
}

function isCompletedGradebookRow(row: any) {
  const status = row?.submission_status
  if (row?.source_type === 'self_test') {
    return ['SUBMITTED', 'REVIEWED'].includes(status)
  }
  return status === 'GRADED' && row?.teacher_review_status !== 'pending'
}

function compareGradebookRowsForTeacher(a: any, b: any) {
  const priorityDelta = gradebookTeacherPriority(a) - gradebookTeacherPriority(b)
  if (priorityDelta !== 0) return priorityDelta
  const dueDelta = gradebookDueSortValue(a) - gradebookDueSortValue(b)
  if (dueDelta !== 0) return dueDelta
  const titleDelta = String(a?.assignment_title || '').localeCompare(String(b?.assignment_title || ''), 'zh-Hant')
  if (titleDelta !== 0) return titleDelta
  return String(a?.student_name || '').localeCompare(String(b?.student_name || ''), 'zh-Hant')
}

function gradebookTeacherPriority(row: any) {
  if (row?.teacher_review_status === 'pending') return 0
  if (row?.submission_status === 'NOT_SUBMITTED') return 1
  if (isLowScoreRow(row)) return 2
  if (row?.late === true) return 3
  if (['SUBMITTED', 'LATE'].includes(row?.submission_status)) return 4
  if (row?.submission_status === 'PENDING') return 5
  return 6
}

function gradebookDueSortValue(row: any) {
  const dueDate = row?.due_date ? Date.parse(String(row.due_date)) : Number.POSITIVE_INFINITY
  return Number.isFinite(dueDate) ? dueDate : Number.POSITIVE_INFINITY
}

function isAttentionRow(row: any) {
  if (row.source_type === 'self_test') {
    return (
      row.teacher_review_status === 'pending' ||
      row.late === true ||
      row.submission_status === 'STARTED'
    )
  }

  return (
    row.teacher_review_status === 'pending' ||
    row.late === true ||
    ['NOT_SUBMITTED', 'SUBMITTED', 'LATE', 'STARTED'].includes(row.submission_status)
  )
}

function assignmentActionLabel(row: any) {
  if (['NOT_SUBMITTED', 'PENDING'].includes(row.submission_status)) return '查看'
  if (row.teacher_review_status === 'pending' || ['SUBMITTED', 'LATE'].includes(row.submission_status)) {
    return '批改'
  }
  return '查看'
}

function assignmentActionClass(row: any) {
  if (['NOT_SUBMITTED', 'PENDING'].includes(row.submission_status)) {
    return 'bg-gray-100 text-gray-700 hover:bg-gray-200'
  }
  if (row.teacher_review_status === 'pending' || ['SUBMITTED', 'LATE'].includes(row.submission_status)) {
    return 'bg-gray-950 text-white hover:bg-black'
  }
  return 'bg-gray-100 text-gray-700 hover:bg-gray-200'
}

function cleanAssignmentUuid(uuid: string) {
  return (uuid || '').replace(/^assignment_/, '')
}

function buildPilotMetrics(rows: any[], summary?: any, useSummaryRecordCounts = true) {
  const summaryValue = (key: string) => useSummaryRecordCounts ? numberOrNull(summary?.[key]) : null
  const actionableRows = rows.filter((row: any) =>
    !isExplicitFalse(row?.visible_to_students) &&
    !isExplicitFalse(row?.target_usergroups_valid)
  )
  const totalRecords = summaryValue('actionable_rows') ?? summaryValue('total_rows') ?? actionableRows.length
  const studentIds = new Set(actionableRows.map((row: any) => row.student_id).filter(Boolean))
  const learnerCountFromSummary = numberOrNull(summary?.learner_count)
  const activeStudentCount = summaryValue('active_student_count') ?? summaryValue('student_count') ?? studentIds.size
  const engagedStudentIds = new Set(
    actionableRows
      .filter((row: any) => !['NOT_SUBMITTED', 'STARTED'].includes(row.submission_status))
      .map((row: any) => row.student_id)
      .filter(Boolean)
  )
  const engagedStudentCount = summaryValue('engaged_student_count') ?? engagedStudentIds.size
  const learnerCountKnown = useSummaryRecordCounts && learnerCountFromSummary !== null
  const learnerCount = learnerCountKnown ? learnerCountFromSummary : activeStudentCount
  const submittedCount = summaryValue('submitted')
    ?? actionableRows.filter((row: any) => !['NOT_SUBMITTED', 'PENDING', 'STARTED'].includes(row.submission_status)).length
  const unsubmittedCount = summaryValue('unsubmitted')
    ?? actionableRows.filter((row: any) => row.submission_status === 'NOT_SUBMITTED').length
  const pendingReviewCount = summaryValue('pending_review')
    ?? actionableRows.filter((row: any) => row.teacher_review_status === 'pending').length
  const retryInProgress = summaryValue('retry_in_progress')
    ?? actionableRows.filter((row: any) => row.submission_status === 'PENDING').length
  const participationCount = summaryValue('participation_records')
    ?? actionableRows.filter((row: any) => !['NOT_SUBMITTED', 'STARTED'].includes(row.submission_status)).length
  const scoredRows = actionableRows.filter((row: any) => !isExplicitFalse(row?.counts_for_grade) && percentageFromRow(row) !== null)
  const gradedCount = summaryValue('graded')
    ?? actionableRows.filter((row: any) => ['GRADED', 'REVIEWED'].includes(row.submission_status) || row.teacher_review_status === 'confirmed').length
  const attentionCount = summaryValue('attention_count')
    ?? actionableRows.filter((row: any) => isAttentionRow(row)).length
  const averageScore = summaryValue('average_score') ?? (scoredRows.length > 0
    ? scoredRows.reduce((sum: number, row: any) => sum + (percentageFromRow(row) || 0), 0) / scoredRows.length
    : null)
  const submissionRate = summaryValue('submission_rate')
  const submissionRatePercent = submissionRate === null
    ? (totalRecords > 0 ? (submittedCount / totalRecords) * 100 : null)
    : submissionRate
  const participationRate = summaryValue('participation_rate')
  const participationRatePercent = participationRate === null
    ? (totalRecords > 0 ? (participationCount / totalRecords) * 100 : null)
    : participationRate
  const engagementRate = summaryValue('engagement_rate')
  const engagementRatePercent = engagementRate === null
    ? (learnerCount > 0 ? (engagedStudentCount / learnerCount) * 100 : null)
    : engagementRate
  const averageScorePercent = averageScore === null ? null : averageScore
  const aiAssignmentReady = typeof summary?.ai_assignment_ready === 'boolean'
    ? summary.ai_assignment_ready
    : null
  const autoGradedAssignments = numberOrNull(summary?.simple_pilot_ready_assignments)
    ?? numberOrNull(summary?.simple_auto_graded_assignments)
    ?? numberOrNull(summary?.auto_graded_assignments)
    ?? 0
  const retryRecords = summaryValue('retry_records')
    ?? actionableRows.filter((row: any) => (numberOrNull(row.attempt_number) || 0) > 1).length
  const selfTestRecords = summaryValue('self_test_rows')
    ?? actionableRows.filter((row: any) => row.source_type === 'self_test').length
  const scoredRecords = summaryValue('scored_records') ?? scoredRows.length
  const passingScoreRecords = summaryValue('passing_score_records')
    ?? scoredRows.filter((row: any) => (percentageFromRow(row) ?? 0) >= 60).length
  const needsPracticeRecords = summaryValue('needs_practice_records')
    ?? scoredRows.filter((row: any) => (percentageFromRow(row) ?? 100) < 60).length
  const pilotStatus = String(summary?.pilot_status || '').trim()
  const pilotStatusLabel = String(summary?.pilot_status_label || '').trim()
  const pilotStatusDetail = String(summary?.pilot_status_detail || '').trim()
  const pilotNextStep = String(summary?.pilot_next_step || '').trim()
  const principalEvidenceSummary = String(summary?.principal_evidence_summary || '').trim()

  return {
    totalRecords,
    studentCount: activeStudentCount,
    studentCountDisplay: String(activeStudentCount),
    activeStudentCount,
    activeStudentCountDisplay: String(activeStudentCount),
    engagedStudentCount,
    engagedStudentCountDisplay: String(engagedStudentCount),
    learnerCount,
    learnerCountDisplay: String(learnerCount),
    learnerCountKnown,
    submittedCount,
    unsubmittedCount,
    pendingReviewCount,
    participationCount,
    gradedCount,
    scoredRecords,
    scoredRecordsDisplay: String(scoredRecords),
    passingScoreRecords,
    passingScoreRecordsDisplay: String(passingScoreRecords),
    needsPracticeRecords,
    needsPracticeRecordsDisplay: String(needsPracticeRecords),
    retryInProgress,
    retryInProgressDisplay: String(retryInProgress),
    retryRecords,
    retryRecordsDisplay: String(retryRecords),
    selfTestRecords,
    selfTestRecordsDisplay: String(selfTestRecords),
    attentionCount,
    attentionCountDisplay: String(attentionCount),
    submissionRatePercent,
    participationRatePercent,
    engagementRatePercent,
    averageScorePercent,
    submissionRateDisplay: submissionRatePercent === null ? '-' : `${Math.round(submissionRatePercent)}%`,
    participationRateDisplay: participationRatePercent === null ? '-' : `${Math.round(participationRatePercent)}%`,
    engagementRateDisplay: engagementRatePercent === null ? '-' : `${Math.round(engagementRatePercent)}%`,
    averageScoreDisplay: averageScore === null ? '-' : `${Math.round(averageScore)}%`,
    aiAssignmentReady,
    aiAssignmentMessage: String(summary?.ai_assignment_message || ''),
    publishedAssignments: numberOrNull(summary?.published) ?? 0,
    autoGradedAssignments,
    publishedNeedingSetup: numberOrNull(summary?.published_needing_setup) ?? 0,
    publishedWithoutTargets: numberOrNull(summary?.published_without_targets) ?? 0,
    publishedEmptyTargetUsergroups: numberOrNull(summary?.published_empty_target_usergroups) ?? 0,
    publishedHiddenFromStudents: numberOrNull(summary?.published_hidden_from_students) ?? 0,
    pilotStatus,
    pilotStatusLabel,
    pilotStatusDetail,
    pilotNextStep,
    principalEvidenceSummary,
  }
}

type PrincipalEvidenceTone = 'emerald' | 'blue' | 'amber' | 'rose'

function buildPrincipalEvidenceItems(metrics: ReturnType<typeof buildPilotMetrics>) {
  const hasLearningRecords = metrics.totalRecords > 0
  const hasScoredRecords = metrics.scoredRecords > 0
  const participationRate = metrics.participationRatePercent
  const engagementRate = metrics.engagementRatePercent
  const averageScore = metrics.averageScorePercent
  const learnerValue = metrics.learnerCountKnown
    ? `${metrics.engagedStudentCount}/${metrics.learnerCount} 名學生`
    : `${metrics.engagedStudentCount} 名學生`

  return [
    {
      label: '學生有使用',
      value: hasLearningRecords ? learnerValue : '未有記錄',
      detail: hasLearningRecords
        ? `已有 ${metrics.totalRecords} 條學習記錄，學生使用率 ${metrics.engagementRateDisplay}，提交率 ${metrics.submissionRateDisplay}。`
        : '先做一份 3 題簡單作業，讓學生完成第一次提交。',
      tone: !hasLearningRecords
        ? 'amber'
        : (engagementRate ?? participationRate ?? 0) >= 80
          ? 'emerald'
          : 'blue',
    },
    {
      label: '學習有成果',
      value: hasScoredRecords ? `平均 ${metrics.averageScoreDisplay}` : '未有計分',
      detail: hasScoredRecords
        ? `${metrics.scoredRecords} 條計入評分，${metrics.passingScoreRecords} 條已達標，${metrics.needsPracticeRecords} 條需補強；${metrics.retryRecords} 條重做/再練習記錄可看到改錯過程。`
        : hasLearningRecords
          ? `${metrics.gradedCount} 條已批改或已覆核，但暫時未有計入評分記錄；可先發布一份簡單作業，或把合適自測設為計分。`
          : '學生提交後，系統批改或老師覆核完成，這裡就會形成分數證據。',
      tone: !hasScoredRecords
        ? 'amber'
        : (averageScore ?? 0) >= 60
          ? 'emerald'
          : 'blue',
    },
    {
      label: '老師能跟進',
      value: metrics.attentionCount > 0 ? `${metrics.attentionCount} 條待處理` : '暫無急件',
      detail: metrics.attentionCount > 0
        ? '未交、逾期或待覆核學生已整理好，可直接複製名單跟進。'
        : hasLearningRecords
          ? '未交、逾期和待覆核狀態已集中在成績表，日常管理簡單。'
          : '等學生提交後，未交和待覆核名單會自動出現在這裡。',
      tone: metrics.attentionCount > 0 ? 'amber' : hasLearningRecords ? 'emerald' : 'blue',
    },
  ] as Array<{
    label: string
    value: string
    detail: string
    tone: PrincipalEvidenceTone
  }>
}

function principalEvidenceToneClass(tone: PrincipalEvidenceTone) {
  const tones: Record<PrincipalEvidenceTone, string> = {
    emerald: 'border-emerald-100 text-emerald-800',
    blue: 'border-blue-100 text-blue-800',
    amber: 'border-amber-100 text-amber-800',
    rose: 'border-rose-100 text-rose-800',
  }
  return tones[tone]
}

function buildPrincipalBriefing(
  metrics: ReturnType<typeof buildPilotMetrics>,
  health: ReturnType<typeof buildPilotHealth>,
  recommendation: string
) {
  if (metrics.learnerCountKnown && metrics.learnerCount <= 0) {
    return '目前仍是準備階段，下一步先批量匯入學生並分班，再發一份 3 題簡單作業收第一批學習記錄。'
  }

  if (metrics.totalRecords <= 0) {
    return metrics.autoGradedAssignments > 0
      ? `平台已準備好 ${metrics.autoGradedAssignments} 份簡單自動批改作業，下一步讓學生完成第一次提交，就能看到提交率、分數和待跟進名單。`
      : '目前未有學生學習記錄，建議先用選擇、填空、短問答建立一份 3 題簡單作業，快速跑通提交和自動批改。'
  }

  const learnerPart = metrics.learnerCountKnown
    ? `${metrics.engagedStudentCount}/${metrics.learnerCount} 名學生已有學習記錄`
    : `${metrics.engagedStudentCount} 名學生已有學習記錄`
  const scorePart = metrics.scoredRecords > 0
    ? `平均分 ${metrics.averageScoreDisplay}`
    : '暫未有計入評分的分數'
  const followUpPart = metrics.attentionCount > 0
    ? `有 ${metrics.attentionCount} 條需要老師跟進`
    : '暫無急件'
  const retryPart = metrics.retryRecords > 0
    ? `，另有 ${metrics.retryRecords} 條重做/再練習記錄，可展示學生改錯過程`
    : ''

  return `${health.label}：${learnerPart}，使用率 ${metrics.engagementRateDisplay}，提交率 ${metrics.submissionRateDisplay}，${scorePart}；${followUpPart}${retryPart}。下一步：${recommendation}`
}

function buildPilotRecommendation(metrics: ReturnType<typeof buildPilotMetrics>) {
  if (metrics.pilotNextStep) {
    return metrics.pilotNextStep
  }
  if (metrics.learnerCountKnown && metrics.learnerCount <= 0) {
    return '先批量匯入學生，或把學生加入班級/群組。'
  }
  if (metrics.autoGradedAssignments <= 0) {
    return metrics.aiAssignmentReady === false
      ? '先用題庫或手動建立一份選擇、填空、短問答的簡單作業；AI 出題之後再配置。'
      : '先建立一份選擇、填空、短問答的簡單作業，並啟用自動批改。'
  }
  if (metrics.publishedWithoutTargets > 0) {
    const emptyClassDetail = metrics.publishedEmptyTargetUsergroups > 0
      ? `，其中 ${metrics.publishedEmptyTargetUsergroups} 份要先把學生加入班級`
      : ''
    return `先為 ${metrics.publishedWithoutTargets} 份已發布作業重新設定班級/群組或補學生${emptyClassDetail}，避免試行數據混到錯誤範圍。`
  }
  if (metrics.publishedHiddenFromStudents > 0) {
    return `先發布相關課程和活動；目前有 ${metrics.publishedHiddenFromStudents} 份已發布作業學生暫時看不到。`
  }
  if (metrics.publishedNeedingSetup > 0) {
    return `先整理 ${metrics.publishedNeedingSetup} 份已發布作業，檢查有效截止日期，只保留適合自動批改的簡單題型。`
  }
  if (metrics.totalRecords === 0) {
    return '先建立一份 3 題簡單作業，讓學生完成第一次提交。'
  }
  if (metrics.scoredRecords <= 0) {
    return '已有學生使用記錄；下一步讓學生完成一份計入評分的簡單作業，或把合適自測設為計分。'
  }
  if (metrics.attentionCount > 0) {
    return `先跟進 ${metrics.attentionCount} 條未交、逾期或待覆核記錄。`
  }
  if (metrics.retryInProgress > 0) {
    return `有 ${metrics.retryInProgress} 條重做中的記錄，先等學生完成重新提交，再看最高分和錯題改善。`
  }
  if ((metrics.participationRatePercent ?? 100) < 80) {
    return '先提醒未參與學生，讓學習參與率回到 80% 以上。'
  }
  if ((metrics.averageScorePercent ?? 100) < 60) {
    return '安排一份低難度補充練習，幫學生重做基礎題。'
  }
  if (metrics.aiAssignmentReady === false) {
    return '核心作業閉環已可展示；下一步再配置 AI 出題，減少老師備課時間。'
  }
  if (metrics.aiAssignmentReady === null) {
    return '核心作業閉環已可展示；之後可再確認 AI 出題端點、模型和 API Key。'
  }
  return '作業閉環運作正常，可以維持每週短練習和自測記錄。'
}

function pilotHealthClassName(status: string) {
  if (status === 'showcase_ready') return 'bg-emerald-50 text-emerald-700'
  if (status === 'active_needs_followup' || status === 'setup_needed') return 'bg-amber-50 text-amber-700'
  if (status === 'not_started') return 'bg-rose-50 text-rose-700'
  return 'bg-gray-100 text-gray-700'
}

function buildPilotHealth(metrics: ReturnType<typeof buildPilotMetrics>) {
  if (metrics.pilotStatusLabel || metrics.pilotStatusDetail) {
    return {
      label: metrics.pilotStatusLabel || '校內試行',
      detail: metrics.pilotStatusDetail || metrics.principalEvidenceSummary || buildPilotRecommendation(metrics),
      className: pilotHealthClassName(metrics.pilotStatus),
    }
  }

  if (metrics.learnerCountKnown && metrics.learnerCount <= 0) {
    return {
      label: '未匯入學生',
      detail: '先批量匯入學生，或把學生加入班級/群組，校內試行才有發布對象。',
      className: 'bg-rose-50 text-rose-700',
    }
  }

  if (metrics.autoGradedAssignments <= 0) {
    if (metrics.aiAssignmentReady === false) {
      return {
        label: 'AI 待配置',
        detail: metrics.aiAssignmentMessage || 'AI 出題尚未配置完整；也可以先用題庫或手動建立選擇、填空、短問答。',
        className: 'bg-amber-50 text-amber-700',
      }
    }
    return {
      label: '未有簡單作業',
      detail: '先建立一份只包含選擇、填空、短問答的自動批改作業。',
      className: 'bg-gray-100 text-gray-700',
    }
  }

  if (metrics.publishedWithoutTargets > 0) {
    return {
      label: metrics.publishedEmptyTargetUsergroups > 0 ? '班級沒有學生' : '班級需重設',
      detail: `有 ${metrics.publishedWithoutTargets} 份已發布作業未指定班級/群組、班級已不存在或班級沒有學生${metrics.publishedEmptyTargetUsergroups > 0 ? `；其中 ${metrics.publishedEmptyTargetUsergroups} 份要先把學生加入班級` : ''}，成績表可能按錯誤範圍計算。`,
      className: 'bg-amber-50 text-amber-700',
    }
  }

  if (metrics.publishedNeedingSetup > 0) {
    if (metrics.publishedHiddenFromStudents > 0) {
      return {
        label: '學生看不到',
        detail: `有 ${metrics.publishedHiddenFromStudents} 份已發布作業的課程或活動未發布，學生暫時看不到。`,
        className: 'bg-amber-50 text-amber-700',
      }
    }
    return {
      label: '作業需整理',
      detail: `有 ${metrics.publishedNeedingSetup} 份已發布作業不適合簡單試行，請檢查有效截止日期，改成選擇、填空、短問答或先取消發布。`,
      className: 'bg-amber-50 text-amber-700',
    }
  }

  if (metrics.totalRecords === 0) {
    return {
      label: '未開始',
      detail: '還沒有學生學習記錄，先建立一份 3 題簡單作業。',
      className: 'bg-gray-100 text-gray-700',
    }
  }

  if (metrics.attentionCount > 0) {
    return {
      label: '需跟進',
      detail: `有 ${metrics.attentionCount} 條未交、逾期或待覆核記錄，老師可以先清這批名單。`,
      className: 'bg-amber-50 text-amber-700',
    }
  }

  if (metrics.retryInProgress > 0) {
    return {
      label: '重做中',
      detail: `有 ${metrics.retryInProgress} 條學生正在改錯後重新提交，這是平台有被用來學習修正的證據。`,
      className: 'bg-blue-50 text-blue-700',
    }
  }

  if (metrics.submittedCount === 0) {
    return {
      label: '未提交',
      detail: '已有作業記錄，但學生尚未開始提交，先提醒學生完成第一次練習。',
      className: 'bg-rose-50 text-rose-700',
    }
  }

  if (metrics.scoredRecords <= 0) {
    return {
      label: '未有計分',
      detail: '已有學生使用記錄，但未有計入評分記錄；先讓學生完成一份簡單作業，或把合適自測設為計分。',
      className: 'bg-amber-50 text-amber-700',
    }
  }

  if ((metrics.participationRatePercent ?? 100) < 80) {
    return {
      label: '參與不足',
      detail: '學習參與率未達 80%，先用 3 題短作業和提醒提高學生使用率。',
      className: 'bg-blue-50 text-blue-700',
    }
  }

  if ((metrics.averageScorePercent ?? 100) < 60) {
    return {
      label: '需要補強',
      detail: '平均分偏低，建議再出一份低難度基礎練習；AI、題庫或手動都可以。',
      className: 'bg-violet-50 text-violet-700',
    }
  }

  if (metrics.gradedCount === 0) {
    return {
      label: '待出分',
      detail: '學生已提交，等待系統批改或老師覆核後即可形成成績證據。',
      className: 'bg-blue-50 text-blue-700',
    }
  }

  if (metrics.aiAssignmentReady === false) {
    return {
      label: '核心可展示',
      detail: '已有學生提交和評分記錄；AI 出題尚未配置，展示時可先主打自動批改、可重做和成績表。',
      className: 'bg-blue-50 text-blue-700',
    }
  }

  if (metrics.aiAssignmentReady === null) {
    return {
      label: '核心可展示',
      detail: '已有學生提交和評分記錄；AI 出題狀態未確認，之後可再檢查配置。',
      className: 'bg-blue-50 text-blue-700',
    }
  }

  return {
    label: '運作正常',
    detail: '已有提交和評分記錄，能向校長展示學生確實在平台完成學習。',
    className: 'bg-emerald-50 text-emerald-700',
  }
}

function aiAssignmentStatusLabel(value: boolean | null) {
  if (value === true) return '可用'
  if (value === false) return '未配置完整'
  return '未確認'
}

function formatAttentionRow(row: any, index: number) {
  const status = STATUS_LABELS[row.submission_status] || row.submission_status || '-'
  const review = gradebookReviewLabel(row)
  const student = row.student_name || row.student_email || `學生 ${row.student_id}`
  const title = row.assignment_title || '未命名作業'
  const reason = gradebookAttentionReason(row)
  return `${index + 1}. ${student}｜${title}｜${reason || status}｜${review}`
}

function gradebookRowsToCsv(rows: any[], summaryRows: string[][] = []) {
  const headers = [
    '來源',
    '學生',
    '電郵',
    '作業/自測',
    '課程',
    '科目',
    '年級',
    '單元',
    '班級/群組',
    '提交狀態',
    '參與狀態',
    '覆核狀態',
    '跟進原因',
    '自動批改',
    '可重做',
    '顯示參考答案',
    '分數',
    '百分比',
    '提交時間',
    '截止日期',
    '嘗試次數',
    '是否重做',
    '最高分',
    '最高分嘗試',
    '是否計入評分',
    '計分策略',
    '老師評語',
  ]
  const body = rows.map((row: any) => [
    row.source_type === 'self_test' ? '自測' : '作業',
    row.student_name || '',
    row.student_email || '',
    row.assignment_title || '',
    row.course_name || '',
    row.subject || '',
    row.grade_level || '',
    row.unit || '',
    joinedList(row.target_usergroups),
    STATUS_LABELS[row.submission_status] || row.submission_status || '',
    gradebookParticipationLabel(row),
    gradebookReviewLabel(row),
    gradebookAttentionReason(row),
    coerceSimplePilotBoolean(row.auto_grading) ? '是' : '否',
    coerceSimplePilotBoolean(row.allow_retries) ? '是' : '否',
    coerceSimplePilotBoolean(row.show_correct_answers) ? '是' : '否',
    gradebookScoreLabel(row, ''),
    gradebookPercentageLabel(row),
    formatZhHkDateTime(row.submitted_at, ''),
    formatZhHkDate(row.due_date, ''),
    row.attempt_number || '',
    (numberOrNull(row.attempt_number) || 0) > 1 ? '是' : '否',
    formatBestGrade(row),
    formatBestAttempt(row),
    isExplicitFalse(row.counts_for_grade) ? '否' : '是',
    scorePolicyLabel(row.score_policy),
    row.teacher_feedback || row.overall_feedback || '',
  ])

  const lines = summaryRows.length > 0
    ? [
      ['摘要項目', '摘要數值'],
      ...summaryRows,
      [],
      headers,
      ...body,
    ]
    : [headers, ...body]

  return lines
    .map((line) => line.map(csvCell).join(','))
    .join('\n')
}

function buildGradebookCsvSummaryRows(metrics: ReturnType<typeof buildPilotMetrics>) {
  const recommendation = metrics.pilotNextStep || buildPilotRecommendation(metrics)
  const health = buildPilotHealth(metrics)
  return [
    ['校內試行狀態', metrics.pilotStatusLabel || ''],
    ['30 秒校長匯報', buildPrincipalBriefing(metrics, health, recommendation)],
    ['校長展示摘要', metrics.principalEvidenceSummary || ''],
    ['建議下一步', recommendation],
    ['已匯入學生', metrics.learnerCountDisplay],
    ['已有學習記錄學生', metrics.engagedStudentCountDisplay],
    ['學生使用率', metrics.engagementRateDisplay],
    ['學習記錄', String(metrics.totalRecords)],
    ['提交率', metrics.submissionRateDisplay],
    ['平均分（計入評分）', metrics.averageScoreDisplay],
    ['計入評分記錄', metrics.scoredRecordsDisplay],
    ['達標記錄（60% 以上）', metrics.passingScoreRecordsDisplay],
    ['需補強記錄（低於 60%）', metrics.needsPracticeRecordsDisplay],
    ['待跟進', metrics.attentionCountDisplay],
    ['重做中', metrics.retryInProgressDisplay],
    ['重做/再練習記錄', metrics.retryRecordsDisplay],
    ['自測記錄', metrics.selfTestRecordsDisplay],
    ['校內試行就緒作業', String(metrics.autoGradedAssignments)],
    ['班級需重設或需加學生作業', String(metrics.publishedWithoutTargets)],
    ['班級沒有學生作業', String(metrics.publishedEmptyTargetUsergroups)],
    ['學生暫時看不到的已發布作業', String(metrics.publishedHiddenFromStudents)],
    ['簡化原則', '作業只用選擇、填空、短問答；可用 AI、題庫或手動出題；學生提交後自動批改，答錯可重做，成績取最高分。'],
  ]
}

function gradebookParticipationLabel(row: any) {
  const status = row?.submission_status
  if (status === 'NOT_SUBMITTED') return '未參與'
  if (status === 'PENDING') return '重做中'
  if (row?.source_type === 'self_test') {
    if (status === 'STARTED') return '自測中'
    if (['SUBMITTED', 'REVIEWED'].includes(status)) return '已完成'
  }
  if (['SUBMITTED', 'LATE'].includes(status)) return '已提交'
  if (status === 'GRADED') return '已完成'
  return '已參與'
}

function gradebookAttentionReason(row: any) {
  if (typeof row?.attention_reason === 'string' && row.attention_reason.trim()) {
    return row.attention_reason
  }
  const status = row?.submission_status
  const review = row?.teacher_review_status
  const isLate = row?.late === true
  if (status === 'NOT_SUBMITTED') {
    return isLate ? '逾期未提交' : '未提交'
  }
  if (status === 'PENDING') {
    return isLate ? '重做中，已逾期' : ''
  }
  if (row?.source_type === 'self_test' && status === 'STARTED') {
    return '自測中未提交'
  }
  if (review === 'pending') {
    if (status === 'GRADED') return '已批改，待老師確認'
    if (isLate || status === 'LATE') return '逾期提交，待老師批改'
    return '已提交，待老師批改'
  }
  if (isLate) return '逾期'
  return ''
}

function gradebookScoreLabel(row: any, emptyLabel = '-') {
  if (row?.submission_status === 'PENDING') return '重做中'
  if (row?.grade_display?.display_grade) return row.grade_display.display_grade
  return row?.grade == null ? emptyLabel : `${row.grade}/${row.max_grade || ''}`
}

function gradebookPercentageLabel(row: any) {
  if (row?.submission_status === 'PENDING') return ''
  return row?.grade_display?.percentage_display || ''
}

function csvCell(value: any) {
  const text = String(value ?? '')
  return `"${text.replace(/"/g, '""')}"`
}

function formatBestGrade(row: any) {
  const bestGrade = numberOrNull(row.best_grade)
  const maxGrade = numberOrNull(row.max_grade)
  const bestAttemptNumber = numberOrNull(row.best_attempt_number)
  if (bestGrade === null || maxGrade === null || maxGrade <= 0 || !bestAttemptNumber || bestAttemptNumber <= 0) return ''
  return `${bestGrade}/${maxGrade}`
}

function formatBestAttempt(row: any) {
  if (!formatBestGrade(row)) return ''
  const bestAttemptNumber = numberOrNull(row.best_attempt_number)
  return bestAttemptNumber && bestAttemptNumber > 0 ? String(bestAttemptNumber) : ''
}

function scorePolicyLabel(value: string) {
  if (value === 'highest') return '最高分'
  if (value === 'latest') return '最後一次'
  return value || ''
}

function joinedList(value: any) {
  if (Array.isArray(value)) return value.filter(Boolean).join('、')
  return String(value || '')
}

function buildPilotSummaryScope({
  selectedUsergroupName,
  includeSelfTests,
  viewFilter,
  hasSearch,
}: {
  selectedUsergroupName?: string
  includeSelfTests: boolean
  viewFilter: GradebookViewFilter
  hasSearch: boolean
}) {
  const parts = [
    selectedUsergroupName ? `班級/群組：${selectedUsergroupName}` : '全部班級/群組',
    includeSelfTests ? '包含自測' : '只含作業',
    viewFilter !== 'all' ? `分類：${gradebookFilterLabel(viewFilter)}` : '',
    hasSearch ? '目前搜尋結果' : '',
  ].filter(Boolean)
  return parts.join('；')
}

function gradebookFilterLabel(filter: GradebookViewFilter) {
  return GRADEBOOK_VIEW_FILTERS.find((item) => item.key === filter)?.label || '全部'
}

function buildGradebookFilename({
  orgslug,
  selectedUsergroupName,
  includeSelfTests,
  viewFilter,
  hasSearch,
}: {
  orgslug: string
  selectedUsergroupName?: string
  includeSelfTests: boolean
  viewFilter: GradebookViewFilter
  hasSearch: boolean
}) {
  const date = localDateStamp(new Date())
  const scope = selectedUsergroupName || '全部班級'
  const parts = [
    'learnhouse',
    '成績表',
    orgslug || 'org',
    scope,
    includeSelfTests ? '含自測' : '只含作業',
    viewFilter !== 'all' ? gradebookFilterLabel(viewFilter) : '',
    hasSearch ? '搜尋結果' : '',
    date,
  ].filter(Boolean)
  return `${parts.map(safeFilenamePart).join('-')}.csv`
}

function localDateStamp(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}${month}${day}`
}

function safeFilenamePart(value: string) {
  return String(value || '')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

function isExplicitFalse(value: unknown) {
  if (value === false) return true
  if (typeof value === 'number') return value === 0
  if (typeof value === 'string') {
    return ['false', '0', 'no', 'n', 'incorrect', 'wrong', '否', '錯', '錯誤', ''].includes(
      value.trim().toLowerCase()
    )
  }
  return false
}

function percentageFromRow(row: any): number | null {
  const direct = row?.grade_display?.percentage
  if (typeof direct === 'number' && Number.isFinite(direct)) {
    return direct
  }
  if (row?.grade === null || row?.grade === undefined || row?.grade === '') {
    return null
  }
  const grade = Number(row.grade)
  const maxGrade = Number(row?.max_grade)
  if (Number.isFinite(grade) && Number.isFinite(maxGrade) && maxGrade > 0) {
    return (grade / maxGrade) * 100
  }
  return null
}

function percentDisplay(rate: number | null) {
  if (rate === null) return '-'
  return `${Math.round(rate * 100)}%`
}

function numberOrNull(value: any): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
