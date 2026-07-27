import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import ConfirmationModal from '@components/Objects/StyledElements/ConfirmationModal/ConfirmationModal'
import Toast from '@components/Objects/StyledElements/Toast/Toast'
import ToolTip from '@components/Objects/StyledElements/Tooltip/Tooltip'
import { getAPIUrl } from '@services/config/config'
import { inviteBatchUsers, removeInvitedUser } from '@services/organizations/invites'
import { downloadUsersImportTemplate, importUsersCsv } from '@services/organizations/orgs'
import { apiFetch } from '@services/utils/ts/requests'
import { searchMatches } from '@/lib/search/normalize'
import {
  Info,
  UserPlus,
  Check,
  X,
  AlertTriangle,
  Search,
  ChevronLeft,
  ChevronRight,
  Trash2,
  Mail,
  MailX,
  Clock,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  Upload,
} from 'lucide-react'
import React, { useMemo, useState, useCallback } from 'react'
import toast from 'react-hot-toast'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { useTranslation } from 'react-i18next'

const ITEMS_PER_PAGE = 10

type InviteResult = {
  email: string
  status: 'sent' | 'email_failed' | 'already_invited'
}

type InviteSummary = {
  total: number
  sent: number
  failed: number
  already_invited: number
}

type ImportResult = {
  line: number
  email: string
  username: string
  status: 'created' | 'failed' | 'existing'
  errors: string[]
  role?: string
  role_label?: string
  usergroups?: string[]
}

type ImportSummary = {
  total: number
  created: number
  failed: number
}

function isExistingImportResult(result: ImportResult) {
  return result.status === 'existing' || result.errors?.some((error) =>
    error.includes('已被使用') || error.includes('已存在')
  )
}

function csvCell(value: unknown) {
  const text = String(value ?? '')
  return `"${text.replace(/"/g, '""')}"`
}

function importErrorRowsToCsv(results: ImportResult[]) {
  const headers = ['行數', '電郵', '用戶名', '角色', '班級/群組', '狀態', '需要修正']
  const body = results.map((result) => [
    result.line,
    result.email,
    result.username,
    result.role_label || result.role || '',
    result.usergroups?.join('、') || '',
    isExistingImportResult(result) ? '已存在' : '失敗',
    result.errors?.join('；') || '',
  ])
  return [headers, ...body].map((row) => row.map(csvCell).join(',')).join('\n')
}

function downloadCsv(filename: string, csvText: string) {
  const blob = new Blob([`\uFEFF${csvText}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function buildImportEvidence(results: ImportResult[] | null, summary: ImportSummary | null) {
  const rows = results || []
  const existingRows = rows.filter(isExistingImportResult)
  const failedRows = rows.filter((result) => result.status !== 'created' && !isExistingImportResult(result))
  const createdRows = rows.filter((result) => result.status === 'created')
  const roleCounts = createdRows.reduce<Record<string, number>>((acc, result) => {
    const label = result.role_label || (result.role === 'teacher' ? '老師' : result.role === 'student' ? '學生' : '其他角色')
    acc[label] = (acc[label] || 0) + 1
    return acc
  }, {})
  const usergroupCounts = createdRows.reduce<Record<string, number>>((acc, result) => {
    for (const group of result.usergroups || []) {
      acc[group] = (acc[group] || 0) + 1
    }
    return acc
  }, {})
  const topUsergroups = Object.entries(usergroupCounts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-Hant'))
    .slice(0, 8)

  return {
    total: summary?.total ?? rows.length,
    created: summary?.created ?? createdRows.length,
    failed: summary?.failed ?? rows.filter((result) => result.status !== 'created').length,
    existing: existingRows.length,
    needsFix: failedRows.length,
    createdRows,
    existingRows,
    failedRows,
    errorRows: [...failedRows, ...existingRows],
    roleCounts,
    topUsergroups,
  }
}

function ImportSummaryCard({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone: 'green' | 'yellow' | 'red' | 'gray'
}) {
  const tones = {
    green: 'border-green-100 bg-green-50 text-green-700',
    yellow: 'border-yellow-100 bg-yellow-50 text-yellow-700',
    red: 'border-red-100 bg-red-50 text-red-700',
    gray: 'border-gray-100 bg-white text-gray-700',
  }
  return (
    <div className={`rounded-lg border px-3 py-3 ${tones[tone]}`}>
      <p className="text-xs font-bold opacity-80">{label}</p>
      <p className="mt-1 text-2xl font-black">{value}</p>
    </div>
  )
}

// Query key for invited users (pending invitations list)
const invitedUsersKey = (orgId: number) => ['org', orgId, 'invitedUsers'] as const

function OrgUsersAdd() {
  const { t } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [isLoading, setIsLoading] = useState(false)
  const [invitedUsers, setInvitedUsers] = useState('')
  const [selectedInviteCode, setSelectedInviteCode] = useState<string | undefined>(undefined)
  const [selectedRoleUuid, setSelectedRoleUuid] = useState<string | undefined>(undefined)
  const [sendResults, setSendResults] = useState<InviteResult[] | null>(null)
  const [sendSummary, setSendSummary] = useState<InviteSummary | null>(null)
  const [searchValue, setSearchValue] = useState('')
  const [page, setPage] = useState(1)
  const [csvFile, setCsvFile] = useState<File | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const [isDownloadingTemplate, setIsDownloadingTemplate] = useState(false)
  const [importResults, setImportResults] = useState<ImportResult[] | null>(null)
  const [importSummary, setImportSummary] = useState<ImportSummary | null>(null)

  const { data: invites } = useQuery({
    queryKey: queryKeys.org.inviteCodes(org?.id),
    queryFn: () => apiFetch(`${getAPIUrl()}orgs/${org?.id}/invites`, access_token),
    enabled: !!org?.id && !!access_token,
    staleTime: 60_000,
  })

  const { data: roles } = useQuery({
    queryKey: queryKeys.org.roles(org?.id),
    queryFn: () => apiFetch(`${getAPIUrl()}roles/org/${org.id}`, access_token),
    enabled: !!org?.id && !!access_token,
    staleTime: 60_000,
  })

  const { data: invited_users, isLoading: isInvitedUsersLoading } = useQuery({
    queryKey: invitedUsersKey(org?.id),
    queryFn: () => apiFetch(`${getAPIUrl()}orgs/${org?.id}/invites/users`, access_token),
    enabled: !!org?.id && !!access_token,
    staleTime: 60_000,
  })

  // Filter + paginate invited users
  const filteredUsers = useMemo(() => {
    if (!invited_users) return []
    if (!searchValue) return invited_users
    return invited_users.filter((u: any) => searchMatches(u.email, searchValue))
  }, [invited_users, searchValue])

  const totalFiltered = filteredUsers.length
  const totalPages = Math.max(1, Math.ceil(totalFiltered / ITEMS_PER_PAGE))
  const paginatedUsers = useMemo(
    () => filteredUsers.slice((page - 1) * ITEMS_PER_PAGE, page * ITEMS_PER_PAGE),
    [filteredUsers, page]
  )
  const importEvidence = useMemo(
    () => buildImportEvidence(importResults, importSummary),
    [importResults, importSummary]
  )

  function refreshUserRosterEvidence(orgId: number) {
    queryClient.invalidateQueries({ queryKey: queryKeys.org.users(orgId) })
    queryClient.invalidateQueries({ queryKey: queryKeys.usergroups.list(orgId) })
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(orgId) })
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(orgId) })
  }

  // Reset page when search changes
  const handleSearchChange = useCallback((value: string) => {
    setSearchValue(value)
    setPage(1)
  }, [])

  async function sendInvites() {
    if (!invitedUsers.trim()) return
    const toastId = toast.loading(t('dashboard.users.invite_members.toasts.sending'))
    setIsLoading(true)
    setSendResults(null)
    setSendSummary(null)
    let res = await inviteBatchUsers(org.id, invitedUsers, selectedInviteCode, selectedRoleUuid, access_token)
    if (res.status == 200) {
      const data = res.data
      setSendResults(data.results || [])
      setSendSummary(data.summary || null)
      queryClient.invalidateQueries({ queryKey: invitedUsersKey(org?.id) })
      setIsLoading(false)
      setInvitedUsers('')

      if (data.summary?.failed > 0) {
        toast.error(
          t('dashboard.users.invite_members.toasts.partial', {
            sent: data.summary.sent,
            failed: data.summary.failed,
          }),
          { id: toastId }
        )
      } else {
        toast.success(t('dashboard.users.invite_members.toasts.success'), { id: toastId })
      }
    } else {
      toast.error(t('dashboard.users.invite_members.toasts.error'), { id: toastId })
      setIsLoading(false)
    }
  }

  async function handleRemoveInvitedUser(email: string) {
    const toastId = toast.loading(t('dashboard.users.invite_members.invited_users.removing'))
    const res = await removeInvitedUser(org.id, email, access_token)
    if (res.status === 200) {
      queryClient.invalidateQueries({ queryKey: invitedUsersKey(org?.id) })
      toast.success(t('dashboard.users.invite_members.invited_users.remove_success'), {
        id: toastId,
      })
    } else {
      toast.error(t('dashboard.users.invite_members.invited_users.remove_error'), {
        id: toastId,
      })
    }
  }

  async function downloadTemplate() {
    if (!org?.id || !access_token) return
    const toastId = toast.loading('正在下載模板...')
    setIsDownloadingTemplate(true)
    try {
      const res = await downloadUsersImportTemplate(org.id, access_token)
      if (!res.ok) {
        throw new Error('Download failed')
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'learnhouse-users-import-template.csv'
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      toast.success('模板已下載', { id: toastId })
    } catch {
      toast.error('下載模板失敗', { id: toastId })
    } finally {
      setIsDownloadingTemplate(false)
    }
  }

  async function uploadCsv() {
    if (!csvFile || !org?.id || !access_token) return
    const toastId = toast.loading('正在匯入帳號...')
    setIsImporting(true)
    setImportResults(null)
    setImportSummary(null)
    try {
      const res = await importUsersCsv(org.id, csvFile, access_token)

      if (res.status === 200) {
        setImportResults(res.data.results || [])
        setImportSummary(res.data.summary || null)
        setCsvFile(null)
        refreshUserRosterEvidence(org.id)
        queryClient.invalidateQueries({ queryKey: invitedUsersKey(org.id) })
        const created = res.data.summary?.created ?? 0
        const failed = res.data.summary?.failed ?? 0
        if (failed > 0) {
          toast.error(`已建立 ${created} 個帳號，${failed} 行失敗`, { id: toastId })
        } else {
          toast.success(`已建立 ${created} 個帳號`, { id: toastId })
        }
      } else {
        const detail = typeof res.data?.detail === 'string' ? res.data.detail : '匯入失敗'
        toast.error(detail, { id: toastId })
      }
    } catch (error: any) {
      const message = typeof error?.message === 'string' && error.message.trim()
        ? error.message
        : '匯入失敗，請確認網絡正常，稍後再試。'
      toast.error(message, { id: toastId })
    } finally {
      setIsImporting(false)
    }
  }

  function downloadImportErrorRows() {
    if (!importEvidence.errorRows.length) {
      toast('目前沒有需要下載的錯誤列。')
      return
    }
    downloadCsv('learnhouse-users-import-errors.csv', importErrorRowsToCsv(importEvidence.errorRows))
    toast.success('已下載錯誤列 CSV')
  }

  function handleCsvFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] || null
    setImportResults(null)
    setImportSummary(null)

    if (!file) {
      setCsvFile(null)
      return
    }

    if (!file.name.toLowerCase().endsWith('.csv')) {
      toast.error('請選擇 CSV 檔案。')
      event.target.value = ''
      setCsvFile(null)
      return
    }

    if (file.size > 2 * 1024 * 1024) {
      toast.error('CSV 檔案太大，請控制在 2MB 以內。')
      event.target.value = ''
      setCsvFile(null)
      return
    }

    setCsvFile(file)
  }

  const statusIcon = (status: string) => {
    switch (status) {
      case 'sent':
        return <Check className="w-3.5 h-3.5" />
      case 'email_failed':
        return <X className="w-3.5 h-3.5" />
      case 'already_invited':
        return <AlertTriangle className="w-3.5 h-3.5" />
      default:
        return null
    }
  }

  const statusStyle = (status: string) => {
    switch (status) {
      case 'sent':
        return 'bg-green-100 text-green-700 border-green-200'
      case 'email_failed':
        return 'bg-red-100 text-red-700 border-red-200'
      case 'already_invited':
        return 'bg-yellow-100 text-yellow-700 border-yellow-200'
      default:
        return 'bg-gray-100 text-gray-700 border-gray-200'
    }
  }

  return (
    <>
      <Toast />
      <div className="h-6"></div>

      {/* Send Invites Section */}
      <div className="mx-4 sm:mx-10 bg-white rounded-xl nice-shadow">
        <div className="flex flex-wrap gap-3 items-start justify-between px-4 sm:px-6 py-5 border-b border-gray-100">
          <div className="flex-1">
            <h1 className="font-bold text-xl text-gray-800">
              {t('dashboard.users.invite_members.title')}
            </h1>
            <p className="text-sm text-gray-500 mt-0.5">
              {t('dashboard.users.invite_members.subtitle')}
            </p>
          </div>
        </div>

        <div className="px-6 py-5">
          <textarea
            value={invitedUsers}
            onChange={(e) => setInvitedUsers(e.target.value)}
            aria-label={t('dashboard.users.invite_members.email_placeholder')}
            className="w-full h-[140px] rounded-lg border border-gray-200 px-4 py-3 bg-gray-50/50 placeholder:italic placeholder:text-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition-all resize-none"
            placeholder={t('dashboard.users.invite_members.email_placeholder')}
          />
          <div className="flex flex-wrap gap-3 items-center justify-between mt-4">
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-600 font-medium">
                {t('dashboard.users.invite_members.invite_code_label')}
              </span>
              <select
                onChange={(e) => setSelectedInviteCode(e.target.value || undefined)}
                value={selectedInviteCode || ''}
                aria-label={t('dashboard.users.invite_members.invite_code_label')}
                className="text-sm text-gray-600 border border-gray-200 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition-all"
              >
                <option value="">{t('dashboard.users.invite_members.no_invite_code') || 'None'}</option>
                {invites?.map((invite: any) => (
                  <option key={invite.invite_code_uuid} value={invite.invite_code_uuid}>
                    {invite.invite_code}
                  </option>
                ))}
              </select>
              <ToolTip
                content={t('dashboard.users.invite_members.invite_code_tooltip')}
                sideOffset={8}
                side="right"
              >
                <Info className="text-gray-400" size={14} />
              </ToolTip>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-600 font-medium">角色</span>
              <select
                onChange={(e) => setSelectedRoleUuid(e.target.value || undefined)}
                value={selectedRoleUuid || ''}
                aria-label="Invite role"
                className="text-sm text-gray-600 border border-gray-200 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition-all"
              >
                <option value="">預設學生角色</option>
                {roles?.map((role: any) => (
                  <option key={role.role_uuid} value={role.role_uuid}>
                    {role.name}
                  </option>
                ))}
              </select>
            </div>
            <button
              onClick={sendInvites}
              disabled={isLoading || !invitedUsers.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-green-700 hover:bg-green-800 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg font-semibold text-sm text-white transition-all"
            >
              <UserPlus className="w-4 h-4" />
              <span>{t('dashboard.users.invite_members.send_button')}</span>
            </button>
          </div>
        </div>

        {/* Send Results */}
        {sendResults && sendResults.length > 0 && (
          <div className="border-t border-gray-100">
            {sendSummary && (
              <div className="flex items-center gap-4 px-6 py-3 bg-gray-50/50 border-b border-gray-100 text-sm">
                <span className="font-medium text-gray-700">
                  {t('dashboard.users.invite_members.results.summary_title')}
                </span>
                {sendSummary.sent > 0 && (
                  <span className="flex items-center gap-1 text-green-700">
                    <Check className="w-3.5 h-3.5" />
                    {t('dashboard.users.invite_members.results.sent_count', {
                      count: sendSummary.sent,
                    })}
                  </span>
                )}
                {sendSummary.failed > 0 && (
                  <span className="flex items-center gap-1 text-red-700">
                    <X className="w-3.5 h-3.5" />
                    {t('dashboard.users.invite_members.results.failed_count', {
                      count: sendSummary.failed,
                    })}
                  </span>
                )}
                {sendSummary.already_invited > 0 && (
                  <span className="flex items-center gap-1 text-yellow-700">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    {t('dashboard.users.invite_members.results.skipped_count', {
                      count: sendSummary.already_invited,
                    })}
                  </span>
                )}
                <button
                  onClick={() => {
                    setSendResults(null)
                    setSendSummary(null)
                  }}
                  className="ml-auto text-xs text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}
            <div className="divide-y divide-gray-50">
              {sendResults.map((result) => (
                <div
                  key={result.email}
                  className="flex items-center justify-between px-6 py-2.5 text-sm"
                >
                  <span className="text-gray-700">{result.email}</span>
                  <span
                    className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-xs font-medium border ${statusStyle(result.status)}`}
                  >
                    {statusIcon(result.status)}
                    {t(`dashboard.users.invite_members.results.status.${result.status}`)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* CSV Bulk Create Section */}
      <div className="h-6"></div>
      <div className="mx-4 sm:mx-10 bg-white rounded-xl nice-shadow">
        <div className="flex flex-wrap gap-3 items-start justify-between px-4 sm:px-6 py-5 border-b border-gray-100">
          <div className="flex-1">
            <h2 className="font-bold text-xl text-gray-800">CSV 批量新增帳號</h2>
            <p className="text-sm text-gray-500 mt-0.5">
              下載繁體中文模板後填入學生或老師資料。學生角色填 student 或 學生，老師角色填 teacher 或 老師；學生必須填班級，班級不存在會自動建立。
            </p>
          </div>
          <button
            onClick={downloadTemplate}
            disabled={isDownloadingTemplate}
            className="inline-flex items-center gap-2 px-3 py-2 border border-gray-200 rounded-lg text-sm font-medium text-gray-600 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            <Download className="w-4 h-4" />
            <span>下載模板</span>
          </button>
        </div>

        <div className="px-6 py-5">
          <div className="mb-4 grid grid-cols-1 gap-2 text-xs font-semibold text-gray-600 md:grid-cols-3">
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              1. 先下載模板，不要手打欄位名稱
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              2. 學生填 student，老師填 teacher
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              3. 學生必須填班級；多個班級用分號分隔
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-[1fr_auto] lg:items-end">
            <div>
              <label className="text-sm font-medium text-gray-700">上傳 CSV</label>
              <div className="mt-2 flex flex-col gap-3 rounded-lg border border-dashed border-gray-200 bg-gray-50/50 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="rounded-lg bg-white p-2 nice-shadow">
                    <FileSpreadsheet className="w-5 h-5 text-green-700" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-700 truncate">
                      {csvFile ? csvFile.name : '選擇模板格式的 CSV 檔案'}
                    </p>
                    <p className="text-xs text-gray-400">
                      模板欄位：電郵、用戶名、名字、姓氏、密碼、角色、班級/群組、電郵已驗證
                    </p>
                    <p className="text-xs text-gray-400">
                      角色可填 student / teacher，也可填 學生 / 老師；學生班級必填，例如「小四A班」。
                    </p>
                    <p className="text-xs text-gray-400">
                      password 是初始密碼，至少 8 個字符；學生首次登入後可再自行修改。
                    </p>
                  </div>
                </div>
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={handleCsvFileChange}
                  className="text-sm text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
                />
              </div>
            </div>
            <button
              onClick={uploadCsv}
              disabled={isImporting || !csvFile}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg font-semibold text-sm text-white transition-all"
            >
              <Upload className="w-4 h-4" />
              <span>開始匯入</span>
            </button>
          </div>

          {importSummary && (
            <div className="mt-4 rounded-lg border border-gray-100 bg-gray-50/60">
              <div className="flex flex-col gap-3 px-4 py-4 border-b border-gray-100 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <p className="text-sm font-bold text-gray-800">匯入結果</p>
                  <p className="mt-0.5 text-xs text-gray-500">
                    已整理成功、已存在和需要修正的資料；有錯時可下載錯誤列再修改匯入。
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {importEvidence.errorRows.length > 0 && (
                    <button
                      type="button"
                      onClick={downloadImportErrorRows}
                      className="inline-flex h-9 items-center gap-2 rounded-lg border border-red-200 bg-white px-3 text-xs font-bold text-red-700 hover:bg-red-50"
                    >
                      <Download className="w-3.5 h-3.5" />
                      下載錯誤列
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setImportResults(null)
                      setImportSummary(null)
                    }}
                    className="inline-flex h-9 items-center rounded-lg border border-gray-200 bg-white px-3 text-xs font-bold text-gray-600 hover:bg-gray-50"
                  >
                    清除結果
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2 px-4 py-4 lg:grid-cols-4">
                <ImportSummaryCard label="成功建立" value={importEvidence.created} tone="green" />
                <ImportSummaryCard label="已存在" value={importEvidence.existing} tone="yellow" />
                <ImportSummaryCard label="需要修正" value={importEvidence.needsFix} tone="red" />
                <ImportSummaryCard label="總列數" value={importEvidence.total} tone="gray" />
              </div>
              <div className="grid gap-3 px-4 pb-4 lg:grid-cols-2">
                <div className="rounded-lg border border-gray-100 bg-white px-3 py-3">
                  <p className="text-xs font-bold text-gray-500">角色摘要</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {Object.entries(importEvidence.roleCounts).length > 0 ? (
                      Object.entries(importEvidence.roleCounts).map(([label, count]) => (
                        <span key={label} className="rounded-full bg-green-50 px-2.5 py-1 text-xs font-bold text-green-700">
                          {label} {count}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-gray-400">未建立新帳號</span>
                    )}
                  </div>
                </div>
                <div className="rounded-lg border border-gray-100 bg-white px-3 py-3">
                  <p className="text-xs font-bold text-gray-500">班級摘要</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {importEvidence.topUsergroups.length > 0 ? (
                      importEvidence.topUsergroups.map(([name, count]) => (
                        <span key={name} className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700">
                          {name} {count}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-gray-400">未加入班級/群組</span>
                    )}
                  </div>
                </div>
              </div>
              {importResults && importResults.length > 0 && (
                <div className="max-h-[320px] overflow-auto divide-y divide-gray-100 border-t border-gray-100">
                  {importResults.map((result) => (
                    <div key={`${result.line}-${result.email}`} className="grid gap-2 px-4 py-2.5 text-sm lg:grid-cols-[80px_1fr_110px_120px_1.5fr]">
                      <span className="text-gray-400">第 {result.line} 行</span>
                      <span className="text-gray-700 truncate">{result.email || result.username}</span>
                      <span className="text-gray-500">
                        {result.role_label || result.role || '-'}
                      </span>
                      <span className={result.status === 'created' ? 'text-green-700' : isExistingImportResult(result) ? 'text-yellow-700' : 'text-red-700'}>
                        {result.status === 'created' ? '已建立' : isExistingImportResult(result) ? '已存在' : '失敗'}
                      </span>
                      <span className="text-xs text-gray-500">
                        {result.errors?.length
                          ? result.errors.join('；')
                          : result.usergroups?.length
                            ? `完成，已加入：${result.usergroups.join('、')}`
                            : '完成'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Invited Users Table */}
      <div className="h-6"></div>
      <div className="mx-4 sm:mx-10 bg-white rounded-xl nice-shadow">
        {/* Header */}
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between px-4 sm:px-6 py-5 border-b border-gray-100">
          <div className="flex-1 min-w-0">
            <h1 className="font-bold text-xl text-gray-800">
              {t('dashboard.users.invite_members.invited_users.title')}
            </h1>
            <p className="text-sm text-gray-500 mt-0.5">
              {t('dashboard.users.invite_members.invited_users.subtitle')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {totalFiltered > 0 && (
              <div className="text-sm text-gray-500 bg-gray-50 px-3 py-1.5 rounded-lg font-medium">
                {totalFiltered} {totalFiltered === 1 ? 'invite' : 'invites'}
              </div>
            )}
            <div className="relative flex-1 lg:flex-none">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                placeholder={
                  t('dashboard.users.invite_members.invited_users.search_placeholder') ||
                  'Search by email...'
                }
                className="pl-10 pr-4 py-2 w-full sm:w-[200px] border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition-all"
                value={searchValue}
                onChange={(e) => handleSearchChange(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="px-0">
          {isInvitedUsersLoading && !invited_users ? (
            <div className="animate-pulse space-y-0 px-6 py-4">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="flex items-center gap-4 py-4 border-b border-gray-50">
                  <div className="h-4 bg-gray-100 rounded flex-1" />
                  <div className="h-5 bg-gray-100 rounded w-20" />
                  <div className="h-5 bg-gray-100 rounded w-16" />
                  <div className="h-7 bg-gray-100 rounded w-16 ml-auto" />
                </div>
              ))}
            </div>
          ) : paginatedUsers.length === 0 ? (
            <div className="py-16 text-center">
              <div className="flex flex-col items-center gap-3">
                <div className="bg-gray-100 p-4 rounded-full">
                  <Mail className="w-8 h-8 text-gray-400" />
                </div>
                <p className="text-gray-400 text-sm font-medium">
                  {searchValue
                    ? t('dashboard.users.invite_members.invited_users.no_results') ||
                      'No invitations found matching your search'
                    : t('dashboard.users.invite_members.invited_users.no_invites') ||
                      'No pending invitations'}
                </p>
              </div>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-6 py-3">
                    {t('dashboard.users.invite_members.invited_users.table.email')}
                  </th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-6 py-3">
                    角色
                  </th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-6 py-3">
                    {t('dashboard.users.invite_members.invited_users.table.signup_status')}
                  </th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-6 py-3">
                    {t('dashboard.users.invite_members.invited_users.table.email_sent')}
                  </th>
                  <th className="text-right text-xs font-semibold text-gray-500 uppercase tracking-wider px-6 py-3">
                    {t('dashboard.users.invite_members.invited_users.table.actions') || 'Actions'}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {paginatedUsers.map((invited_user: any) => (
                  <tr key={invited_user.email} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4">
                      <span className="text-sm text-gray-800">{invited_user.email}</span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-indigo-700 bg-indigo-50 px-2.5 py-1 rounded-md">
                        {invited_user.role_name || '預設學生角色'}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      {invited_user.pending ? (
                        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-700 bg-amber-50 px-2.5 py-1 rounded-md">
                          <Clock className="w-3.5 h-3.5" />
                          {t('dashboard.users.invite_members.invited_users.status.pending')}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-md">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          {t('dashboard.users.invite_members.invited_users.status.signed')}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4">
                      {invited_user.email_sent ? (
                        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-md">
                          <Mail className="w-3.5 h-3.5" />
                          {t('dashboard.users.invite_members.invited_users.email_status.sent')}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-red-700 bg-red-50 px-2.5 py-1 rounded-md">
                          <MailX className="w-3.5 h-3.5" />
                          {t('dashboard.users.invite_members.invited_users.email_status.no')}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <ConfirmationModal
                        confirmationButtonText={
                          t('dashboard.users.invite_members.invited_users.remove_button') ||
                          'Remove'
                        }
                        confirmationMessage={
                          t('dashboard.users.invite_members.invited_users.remove_message', {
                            email: invited_user.email,
                          }) || `Remove invitation for ${invited_user.email}?`
                        }
                        dialogTitle={
                          t('dashboard.users.invite_members.invited_users.remove_title') ||
                          'Remove invitation'
                        }
                        dialogTrigger={
                          <button className="inline-flex items-center gap-1.5 h-8 px-3 bg-white text-gray-600 hover:bg-rose-50 hover:text-rose-600 rounded-md text-xs font-medium nice-shadow transition-all">
                            <Trash2 className="w-3.5 h-3.5" />
                            <span>
                              {t('dashboard.users.invite_members.invited_users.remove_button') ||
                                'Remove'}
                            </span>
                          </button>
                        }
                        functionToExecute={() => handleRemoveInvitedUser(invited_user.email)}
                        status="warning"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        {totalFiltered > ITEMS_PER_PAGE && (
          <div className="flex items-center justify-between px-6 py-4 border-t border-gray-100 bg-gray-50/50">
            <div className="text-xs text-gray-500 font-medium">
              {`${(page - 1) * ITEMS_PER_PAGE + 1}-${Math.min(page * ITEMS_PER_PAGE, totalFiltered)} of ${totalFiltered}`}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                <ChevronLeft className="w-4 h-4 text-gray-600" />
              </button>
              <span className="text-sm text-gray-600 font-medium min-w-[80px] text-center bg-white px-3 py-2 rounded-lg border border-gray-200">
                {`Page ${page} of ${totalPages}`}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                <ChevronRight className="w-4 h-4 text-gray-600" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

export default OrgUsersAdd
