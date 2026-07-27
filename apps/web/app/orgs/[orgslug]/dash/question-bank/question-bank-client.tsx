'use client'

import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import {
  createQuestionBankCategory,
  createQuestionBankItem,
  deleteQuestionBankItem,
  getQuestionBankCategories,
  getQuestionBankItems,
} from '@services/question-bank/question-bank'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  BookOpenCheck,
  Boxes,
  CheckCircle2,
  FileQuestion,
  FolderPlus,
  ListChecks,
  Pencil,
  Plus,
  Search,
  Share2,
  Trash2,
} from 'lucide-react'
import Link from 'next/link'
import React from 'react'
import toast from 'react-hot-toast'
import { getUriWithOrg } from '@services/config/config'
import { queryKeys } from '@/lib/query/keys'
import {
  SIMPLE_SELF_TEST_TYPE_SET,
  isSelfTestReadyQuestionBankItem,
} from '@lib/question-bank-readiness'
import {
  getSimplePilotAssignmentTaskSetupIssue,
  isAiFallbackStarterTask,
} from '@lib/simple-pilot-assignments'

type Props = {
  org_id: number
  orgslug: string
}

const TYPE_META: Record<string, { label: string; Icon: any }> = {
  QUIZ: { label: '選擇題', Icon: ListChecks },
  FORM: { label: '填空題', Icon: FileQuestion },
  SHORT_ANSWER: { label: '短問答', Icon: Pencil },
}

function selfTestBadgeForItem(item: any, setupIssue = '') {
  if (item.visibility !== 'ORG') {
    return {
      label: '私人不進自測',
      className: 'bg-gray-100 text-gray-600',
    }
  }
  if (isSelfTestReadyQuestionBankItem(item)) {
    return {
      label: '自測可用',
      className: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    }
  }
  if (isAiFallbackStarterTask(item)) {
    return {
      label: 'AI 備用需修改',
      className: 'bg-orange-50 text-orange-700 ring-1 ring-orange-100',
    }
  }
  if (setupIssue) {
    return {
      label: '需修改',
      className: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100',
    }
  }
  return {
    label: '需補答案',
    className: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100',
  }
}

function splitTeacherListInput(value: string) {
  return value.split(/[,，、;；\n]+/).map((item) => item.trim()).filter(Boolean)
}

function splitTags(value: string) {
  return splitTeacherListInput(value)
}

function difficultyLabel(value: string) {
  if (value === 'beginner') return '基礎'
  if (value === 'intermediate') return '中等'
  if (value === 'advanced') return '進階'
  return value || '未設定'
}

function shortId(prefix: string) {
  const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

function splitAnswers(value: string) {
  const seen = new Set<string>()
  return splitTeacherListInput(value).filter((item) => {
    const key = item.toLowerCase()
    if (!item || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message ?? response?.HTTPmessage
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
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

function buildDraftContents(draft: {
  description: string
  assignment_type: string
  answer: string
  wrongOptions: string
}) {
  const prompt = draft.description.trim()
  const answer = draft.answer.trim()
  let contents: Record<string, any> = {
    prompt,
    correct_answers: [answer],
    match_mode: 'case_insensitive',
    explanation: '',
  }
  if (draft.assignment_type === 'QUIZ') {
    const wrongAnswers = splitAnswers(draft.wrongOptions).filter(
      (wrongAnswer) => wrongAnswer.toLowerCase() !== answer.toLowerCase()
    )
    const options = [
      {
        optionUUID: shortId('option'),
        text: answer,
        fileID: '',
        type: 'text',
        assigned_right_answer: true,
      },
      ...wrongAnswers.map((answer) => ({
        optionUUID: shortId('option'),
        text: answer,
        fileID: '',
        type: 'text',
        assigned_right_answer: false,
      })),
    ].sort(() => Math.random() - 0.5)
    contents = {
      questions: [
        {
          questionText: prompt,
          questionUUID: shortId('question'),
          options,
        },
      ],
    }
  } else if (draft.assignment_type === 'FORM') {
    contents = {
      questions: [
        {
          questionText: prompt,
          questionUUID: shortId('question'),
          blanks: [
            {
              blankUUID: shortId('blank'),
              placeholder: '填寫答案',
              correctAnswer: answer,
              hint: '',
            },
          ],
        },
      ],
    }
  }
  return contents
}

function draftValidationMessage(draft: {
  title: string
  description: string
  assignment_type: string
  answer: string
  wrongOptions: string
}) {
  if (!draft.title.trim()) return '請先填寫題目標題'
  if (!draft.description.trim()) return '請先填寫學生會看到的題目內容'
  if (!draft.answer.trim()) return '請先填寫正確答案'
  if (
    draft.assignment_type === 'QUIZ' &&
    !splitAnswers(draft.wrongOptions).some(
      (answer) => answer.toLowerCase() !== draft.answer.trim().toLowerCase()
    )
  ) {
    return '選擇題至少需要一個不同於正確答案的其他選項'
  }
  const setupIssue = getSimplePilotAssignmentTaskSetupIssue({
    title: draft.title.trim(),
    description: draft.description.trim(),
    hint: '',
    assignment_type: draft.assignment_type,
    contents: buildDraftContents(draft),
  })
  if (setupIssue) return setupIssue
  return ''
}

export default function QuestionBankClient({ org_id, orgslug }: Props) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [categoryId, setCategoryId] = React.useState('')
  const [type, setType] = React.useState('')
  const [selfTestReadyOnly, setSelfTestReadyOnly] = React.useState(false)
  const [newCategory, setNewCategory] = React.useState('')
  const [draftOpen, setDraftOpen] = React.useState(false)
  const [deletingItemUuid, setDeletingItemUuid] = React.useState<string | null>(null)
  const [draft, setDraft] = React.useState({
    title: '',
    description: '',
    assignment_type: 'SHORT_ANSWER',
    difficulty: 'beginner',
    visibility: 'ORG',
    tags: '',
    answer: '',
    wrongOptions: '',
  })
  const draftError = draftValidationMessage(draft)

  const categoriesQuery = useQuery({
    queryKey: queryKeys.questionBank.categories(org_id),
    queryFn: async () => requireSuccess(
      getQuestionBankCategories(org_id, accessToken),
      '載入題庫分類失敗'
    ),
    enabled: !!org_id && !!accessToken,
  })

  const itemsQuery = useQuery({
    queryKey: queryKeys.questionBank.itemsSearch(org_id, JSON.stringify({ search, categoryId, type })),
    queryFn: async () => requireSuccess(
      getQuestionBankItems({
        org_id,
        q: search,
        category_id: categoryId,
        assignment_type: type,
      }, accessToken),
      '載入題庫題目失敗'
    ),
    enabled: !!org_id && !!accessToken,
  })

  const categories = Array.isArray(categoriesQuery.data) ? categoriesQuery.data : []
  const items = Array.isArray(itemsQuery.data) ? itemsQuery.data : []
  const queryError = (categoriesQuery.error || itemsQuery.error) as any

  function refreshQuestionBankEvidence() {
    queryClient.invalidateQueries({ queryKey: queryKeys.questionBank.items(org_id) })
    queryClient.invalidateQueries({ queryKey: queryKeys.questionBank.selfTestReadiness(org_id) })
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org_id) })
  }

  const createCategoryMutation = useMutation({
    mutationFn: () => requireSuccess(
      createQuestionBankCategory({
        name: newCategory.trim(),
        description: '',
        color: '#111827',
        org_id,
      }, accessToken),
      '建立分類失敗'
    ),
    onSuccess: () => {
      setNewCategory('')
      toast.success('分類已建立')
      queryClient.invalidateQueries({ queryKey: queryKeys.questionBank.categories(org_id) })
    },
    onError: (error: any) => {
      toast.error(error?.message || '建立分類失敗')
    },
  })

  const createItemMutation = useMutation({
    mutationFn: () => {
      if (draftError) {
        throw new Error(draftError)
      }
      const prompt = draft.description.trim()
      const contents = buildDraftContents(draft)
      return requireSuccess(
        createQuestionBankItem({
          title: draft.title.trim(),
          description: prompt,
          hint: '',
          reference_file: '',
          assignment_type: draft.assignment_type,
          contents,
          tags: splitTags(draft.tags),
          difficulty: draft.difficulty,
          visibility: draft.visibility,
          category_id: categoryId ? Number(categoryId) : null,
          org_id,
          source_assignment_task_uuid: null,
        }, accessToken),
        '儲存題目失敗'
      )
    },
    onSuccess: () => {
      setDraftOpen(false)
      setDraft({ title: '', description: '', assignment_type: 'SHORT_ANSWER', difficulty: 'beginner', visibility: 'ORG', tags: '', answer: '', wrongOptions: '' })
      toast.success('題目已儲存')
      refreshQuestionBankEvidence()
    },
    onError: (error: any) => {
      toast.error(error?.message || '儲存題目失敗')
    },
  })

  async function deleteItem(item: any) {
    const itemUuid = item?.item_uuid
    if (!itemUuid || deletingItemUuid) return
    const title = String(item?.title || '這道題目').trim()
    if (!window.confirm(`確定要刪除「${title}」？刪除後不能復原。`)) {
      return
    }
    setDeletingItemUuid(itemUuid)
    try {
      await requireSuccess(deleteQuestionBankItem(itemUuid, accessToken), '刪除失敗')
      toast.success('題目已刪除')
      refreshQuestionBankEvidence()
    } catch (error: any) {
      toast.error(error?.message || '刪除失敗')
    } finally {
      setDeletingItemUuid(null)
    }
  }

  const sharedCount = items.filter((item: any) => item.visibility === 'ORG').length
  const privateCount = items.filter((item: any) => item.visibility === 'PRIVATE').length
  const selfTestReadyItems = items.filter((item: any) => item.visibility === 'ORG' && isSelfTestReadyQuestionBankItem(item))
  const sharedAiFallbackCount = items.filter((item: any) => (
    item.visibility === 'ORG' &&
    SIMPLE_SELF_TEST_TYPE_SET.has(item.assignment_type) &&
    isAiFallbackStarterTask(item)
  )).length
  const sharedNeedsSetupCount = items.filter((item: any) => (
    item.visibility === 'ORG' &&
    SIMPLE_SELF_TEST_TYPE_SET.has(item.assignment_type) &&
    !isSelfTestReadyQuestionBankItem(item) &&
    !isAiFallbackStarterTask(item)
  )).length
  const displayedItems = selfTestReadyOnly ? selfTestReadyItems : items
  const hasActiveFilters = Boolean(search.trim() || categoryId || type || selfTestReadyOnly)
  const canSaveDraft = !draftError
  const saveDraftButtonLabel = createItemMutation.isPending
    ? '儲存中'
    : draftError
      ? '先補完整題目'
      : '儲存題目'
  const selfTestSummary = selfTestReadyItems.length === 0
    ? '學生暫時不能開始自測，請先準備全校共享的簡單題。'
    : selfTestReadyItems.length < 3
      ? '建議至少 3 題，學生就能完成一次短練習。'
      : '學生可以用題庫自測，老師也能用作業自動批改。'

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-6 py-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <Breadcrumbs
          items={[
            { label: '校本題庫', href: '/dash/question-bank', icon: <BookOpenCheck size={14} /> },
          ]}
        />

        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-tight text-gray-950">校本題庫</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              老師可以儲存常用題目，之後快速組成作業或自測。全校共享題可供學生自測，私人草稿只供老師整理。
            </p>
          </div>
          <button
            type="button"
            onClick={() => setDraftOpen((value) => !value)}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
          >
            <Plus size={16} />
            新增題目
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Metric icon={<Boxes size={18} />} label="題目總數" value={items.length} />
          <Metric icon={<Share2 size={18} />} label="全校共享" value={sharedCount} />
          <Metric icon={<CheckCircle2 size={18} />} label="自測可用" value={selfTestReadyItems.length} />
          <Metric icon={<Pencil size={18} />} label="私人草稿" value={privateCount} />
        </div>

        <div className="rounded-lg border border-emerald-100 bg-emerald-50/70 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white text-emerald-700 shadow-sm">
                {selfTestReadyItems.length >= 3 ? <CheckCircle2 size={20} /> : <AlertCircle size={20} />}
              </div>
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">簡單自動批改題</p>
                <h2 className="mt-1 text-base font-black text-gray-950">{selfTestSummary}</h2>
                <p className="mt-1 text-sm leading-relaxed text-gray-600">
                  只有全校共享，而且有正確答案的選擇題、填空題、短問答，才會放入學生自測。
                  {sharedAiFallbackCount > 0 ? ` 目前有 ${sharedAiFallbackCount} 題 AI 備用題需要先改成正式題目。` : ''}
                  {sharedNeedsSetupCount > 0 ? ` 目前有 ${sharedNeedsSetupCount} 題共享題需要補答案或內容。` : ''}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setSelfTestReadyOnly((value) => !value)}
              className="inline-flex h-10 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-white px-4 text-sm font-bold text-emerald-800 hover:border-emerald-700"
            >
              {selfTestReadyOnly ? '顯示全部題目' : '只看自測可用'}
            </button>
          </div>
        </div>

        <div className="rounded-lg border border-cyan-100 bg-cyan-50/70 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">最快建立題庫</p>
              <h2 className="mt-1 text-base font-black text-gray-950">用 AI、題庫或手動快速建立簡單作業</h2>
              <p className="mt-1 text-sm leading-relaxed text-gray-600">
                AI 可以幫老師快速生成選擇、填空、短問答；老師也可以從題庫選題或手動新增。這些簡單題方便學生提交後自動批改。
              </p>
            </div>
            <Link
              href={getUriWithOrg(orgslug, '/dash/assignments')}
              className="inline-flex h-10 shrink-0 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
            >
              前往建立作業
            </Link>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
          <aside className="space-y-4">
            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-bold uppercase text-gray-400">分類</p>
              <div className="mt-3 space-y-2">
                <button
                  onClick={() => setCategoryId('')}
                  className={`w-full rounded-md px-3 py-2 text-left text-sm font-semibold ${!categoryId ? 'bg-gray-950 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
                >
                  全部題目
                </button>
                {categories.map((category: any) => (
                  <button
                    key={category.id}
                    onClick={() => setCategoryId(String(category.id))}
                    className={`w-full rounded-md px-3 py-2 text-left text-sm font-semibold ${categoryId === String(category.id) ? 'bg-gray-950 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
                  >
                    {category.name}
                  </button>
                ))}
              </div>
              <div className="mt-4 flex gap-2">
                <input
                  value={newCategory}
                  onChange={(e) => setNewCategory(e.target.value)}
                  placeholder="新增分類"
                  className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-gray-900"
                />
                <button
                  type="button"
                  disabled={!accessToken || !newCategory.trim() || createCategoryMutation.isPending}
                  onClick={() => createCategoryMutation.mutate()}
                  className="rounded-md bg-gray-900 px-3 text-white disabled:bg-gray-300"
                >
                  <FolderPlus size={16} />
                </button>
              </div>
            </div>
          </aside>

          <main className="space-y-4">
            {queryError && (
              <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800 sm:flex-row sm:items-center sm:justify-between">
                <span>{queryError?.message || '題庫資料載入失敗，請稍後再試。'}</span>
                <button
                  type="button"
                  onClick={() => {
                    categoriesQuery.refetch()
                    itemsQuery.refetch()
                  }}
                  className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-black text-amber-900 hover:border-amber-700"
                >
                  重新載入題庫
                </button>
              </div>
            )}

            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_180px]">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="搜尋題目或內容"
                    className="h-10 w-full rounded-lg border border-gray-200 pl-9 pr-3 text-sm outline-none focus:border-gray-900"
                  />
                </div>
                <select
                  value={type}
                  onChange={(e) => setType(e.target.value)}
                  className="h-10 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
                >
                  <option value="">全部題型</option>
                  {Object.entries(TYPE_META).map(([key, meta]) => (
                    <option key={key} value={key}>{meta.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {draftOpen && (
              <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
                <div className="mb-4 rounded-lg bg-cyan-50 px-3 py-2 text-xs font-semibold leading-relaxed text-cyan-800">
                  只要填標題、題目內容和正確答案。選擇題再加一個或多個其他選項，系統就能自動批改。
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="題目標題" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
                  <select className="rounded-lg border border-gray-200 px-3 py-2 text-sm" value={draft.assignment_type} onChange={(e) => setDraft({ ...draft, assignment_type: e.target.value })}>
                    <option value="QUIZ">選擇題</option>
                    <option value="FORM">填空題</option>
                    <option value="SHORT_ANSWER">短問答</option>
                  </select>
                  <textarea className="md:col-span-2 rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="題目內容（學生會看到）" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                  <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="正確答案" value={draft.answer} onChange={(e) => setDraft({ ...draft, answer: e.target.value })} />
                  {draft.assignment_type === 'QUIZ' && (
                    <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="其他選項，可用逗號、頓號或換行分隔" value={draft.wrongOptions} onChange={(e) => setDraft({ ...draft, wrongOptions: e.target.value })} />
                  )}
                  <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="標籤，可用逗號、頓號或換行分隔" value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
                  <select className="rounded-lg border border-gray-200 px-3 py-2 text-sm" value={draft.visibility} onChange={(e) => setDraft({ ...draft, visibility: e.target.value })}>
                    <option value="ORG">全校共享，學生可自測</option>
                    <option value="PRIVATE">私人草稿，不供自測</option>
                  </select>
                  <button
                    type="button"
                    disabled={!accessToken || !canSaveDraft || createItemMutation.isPending}
                    title={draftError || undefined}
                    onClick={() => createItemMutation.mutate()}
                    className="rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white disabled:bg-gray-300"
                  >
                    {saveDraftButtonLabel}
                  </button>
                  {draftError && (
                    <p className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800 md:col-span-2">
                      {draftError}
                    </p>
                  )}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {displayedItems.map((item: any) => {
                const meta = TYPE_META[item.assignment_type] || { label: item.assignment_type, Icon: FileQuestion }
                const Icon = meta.Icon
                const setupIssue = getSimplePilotAssignmentTaskSetupIssue(item)
                const selfTestBadge = selfTestBadgeForItem(item, setupIssue)
                return (
                  <article key={item.item_uuid} className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-700">
                          <Icon size={18} />
                        </div>
                        <div className="min-w-0">
                          <h2 className="truncate text-sm font-black text-gray-950">{item.title}</h2>
                          <p className="mt-1 line-clamp-2 text-xs text-gray-500">{item.description || item.contents?.prompt}</p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => deleteItem(item)}
                        disabled={deletingItemUuid === item.item_uuid}
                        aria-label={`刪除題目 ${item.title}`}
                        className="rounded-md p-2 text-gray-400 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{meta.label}</span>
                      <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{difficultyLabel(item.difficulty)}</span>
                      <span className="rounded-full bg-gray-950 px-2 py-1 text-[11px] font-bold text-white">{item.visibility === 'ORG' ? '全校共享' : '私人草稿'}</span>
                      <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${selfTestBadge.className}`}>{selfTestBadge.label}</span>
                      {(item.tags || []).map((tag: string) => (
                        <span key={tag} className="rounded-full bg-cyan-50 px-2 py-1 text-[11px] font-bold text-cyan-700">{tag}</span>
                      ))}
                    </div>
                    {setupIssue && (
                      <p className="mt-3 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800">
                        {setupIssue}
                      </p>
                    )}
                  </article>
                )
              })}
            </div>

            {!itemsQuery.isLoading && displayedItems.length === 0 && (
              <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center">
                <BookOpenCheck className="mx-auto text-gray-300" size={44} />
                <p className="mt-3 text-sm font-bold text-gray-800">
                  {selfTestReadyOnly ? '暫時沒有自測可用題' : hasActiveFilters ? '沒有符合條件的題目' : '先建立幾道簡單題'}
                </p>
                <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-gray-500">
                  {selfTestReadyOnly
                    ? '請建立全校共享的選擇題、填空題或短問答，並填好正確答案。'
                    : hasActiveFilters
                    ? '可以清除搜尋或篩選條件，看看其他題目。'
                    : '試行階段建議先準備選擇題、填空題和短問答。學生能自測，老師也容易自動批改。'}
                </p>
                <div className="mt-5 flex flex-col items-center justify-center gap-2 sm:flex-row">
                  {hasActiveFilters ? (
                    <button
                      type="button"
                      onClick={() => {
                        setSearch('')
                        setCategoryId('')
                        setType('')
                        setSelfTestReadyOnly(false)
                      }}
                      className="inline-flex h-9 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
                    >
                      清除篩選
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => setDraftOpen(true)}
                        className="inline-flex h-9 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
                      >
                        新增簡單題
                      </button>
                      <Link
                        href={getUriWithOrg(orgslug, '/dash/assignments')}
                        className="inline-flex h-9 items-center justify-center rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
                      >
                        從作業加入題目
                      </Link>
                    </>
                  )}
                </div>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-bold uppercase text-gray-400">{label}</p>
          <p className="mt-1 text-2xl font-black text-gray-950">{value}</p>
        </div>
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gray-100 text-gray-700">{icon}</div>
      </div>
    </div>
  )
}
