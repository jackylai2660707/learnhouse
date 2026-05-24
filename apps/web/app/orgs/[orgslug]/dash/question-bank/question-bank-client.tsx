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
  BookOpenCheck,
  Boxes,
  Code2,
  FileQuestion,
  FolderPlus,
  Hash,
  ListChecks,
  Pencil,
  Plus,
  Search,
  Share2,
  Trash2,
} from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast'

type Props = {
  org_id: number
  orgslug: string
}

const TYPE_META: Record<string, { label: string; Icon: any }> = {
  QUIZ: { label: 'Quiz', Icon: ListChecks },
  FORM: { label: 'Fill blanks', Icon: FileQuestion },
  CODE: { label: 'Code', Icon: Code2 },
  SHORT_ANSWER: { label: 'Short answer', Icon: Pencil },
  NUMBER_ANSWER: { label: 'Number', Icon: Hash },
}

function splitTags(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

export default function QuestionBankClient({ org_id }: Props) {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const [categoryId, setCategoryId] = React.useState('')
  const [type, setType] = React.useState('')
  const [newCategory, setNewCategory] = React.useState('')
  const [draftOpen, setDraftOpen] = React.useState(false)
  const [draft, setDraft] = React.useState({
    title: '',
    description: '',
    assignment_type: 'SHORT_ANSWER',
    difficulty: 'intermediate',
    visibility: 'ORG',
    tags: '',
    answer: '',
  })

  const categoriesQuery = useQuery({
    queryKey: ['question-bank-categories', org_id],
    queryFn: async () => (await getQuestionBankCategories(org_id, accessToken)).data,
    enabled: !!org_id && !!accessToken,
  })

  const itemsQuery = useQuery({
    queryKey: ['question-bank-items', org_id, search, categoryId, type],
    queryFn: async () => (await getQuestionBankItems({
      org_id,
      q: search,
      category_id: categoryId,
      assignment_type: type,
    }, accessToken)).data,
    enabled: !!org_id && !!accessToken,
  })

  const categories = categoriesQuery.data || []
  const items = itemsQuery.data || []

  const createCategoryMutation = useMutation({
    mutationFn: () => createQuestionBankCategory({
      name: newCategory,
      description: '',
      color: '#111827',
      org_id,
    }, accessToken),
    onSuccess: () => {
      setNewCategory('')
      toast.success('Category created')
      queryClient.invalidateQueries({ queryKey: ['question-bank-categories', org_id] })
    },
  })

  const createItemMutation = useMutation({
    mutationFn: () => {
      const contents = draft.assignment_type === 'NUMBER_ANSWER'
        ? { prompt: draft.description || draft.title, correct_value: Number(draft.answer || 0), tolerance: 0, unit: '', explanation: '' }
        : { prompt: draft.description || draft.title, correct_answers: [draft.answer], match_mode: 'case_insensitive', explanation: '' }
      return createQuestionBankItem({
        title: draft.title,
        description: draft.description,
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
      }, accessToken)
    },
    onSuccess: () => {
      setDraftOpen(false)
      setDraft({ title: '', description: '', assignment_type: 'SHORT_ANSWER', difficulty: 'intermediate', visibility: 'ORG', tags: '', answer: '' })
      toast.success('Question saved')
      queryClient.invalidateQueries({ queryKey: ['question-bank-items', org_id] })
    },
  })

  async function deleteItem(itemUuid: string) {
    const res = await deleteQuestionBankItem(itemUuid, accessToken)
    if (res.success === false) {
      toast.error(res?.data?.detail || 'Delete failed')
      return
    }
    toast.success('Question deleted')
    queryClient.invalidateQueries({ queryKey: ['question-bank-items', org_id] })
  }

  const sharedCount = items.filter((item: any) => item.visibility === 'ORG').length
  const privateCount = items.filter((item: any) => item.visibility === 'PRIVATE').length

  return (
    <div className="min-h-screen bg-[#f7f7f5] px-6 py-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <Breadcrumbs
          items={[
            { label: 'Question bank', href: '/dash/question-bank', icon: <BookOpenCheck size={14} /> },
          ]}
        />

        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-tight text-gray-950">Question bank</h1>
            <p className="mt-2 max-w-2xl text-sm text-gray-600">
              Reusable homework questions for your organization. Classify, share, and insert them into assignments.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setDraftOpen((value) => !value)}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black"
          >
            <Plus size={16} />
            New question
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Metric icon={<Boxes size={18} />} label="Total questions" value={items.length} />
          <Metric icon={<Share2 size={18} />} label="Shared in org" value={sharedCount} />
          <Metric icon={<Pencil size={18} />} label="Private drafts" value={privateCount} />
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
          <aside className="space-y-4">
            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-bold uppercase text-gray-400">Categories</p>
              <div className="mt-3 space-y-2">
                <button
                  onClick={() => setCategoryId('')}
                  className={`w-full rounded-md px-3 py-2 text-left text-sm font-semibold ${!categoryId ? 'bg-gray-950 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
                >
                  All questions
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
                  placeholder="New category"
                  className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-gray-900"
                />
                <button
                  type="button"
                  disabled={!newCategory.trim() || createCategoryMutation.isPending}
                  onClick={() => createCategoryMutation.mutate()}
                  className="rounded-md bg-gray-900 px-3 text-white disabled:bg-gray-300"
                >
                  <FolderPlus size={16} />
                </button>
              </div>
            </div>
          </aside>

          <main className="space-y-4">
            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_180px]">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search title or prompt"
                    className="h-10 w-full rounded-lg border border-gray-200 pl-9 pr-3 text-sm outline-none focus:border-gray-900"
                  />
                </div>
                <select
                  value={type}
                  onChange={(e) => setType(e.target.value)}
                  className="h-10 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
                >
                  <option value="">All types</option>
                  {Object.entries(TYPE_META).map(([key, meta]) => (
                    <option key={key} value={key}>{meta.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {draftOpen && (
              <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="Question title" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
                  <select className="rounded-lg border border-gray-200 px-3 py-2 text-sm" value={draft.assignment_type} onChange={(e) => setDraft({ ...draft, assignment_type: e.target.value })}>
                    <option value="SHORT_ANSWER">Short answer</option>
                    <option value="NUMBER_ANSWER">Number answer</option>
                  </select>
                  <textarea className="md:col-span-2 rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="Prompt" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                  <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="Correct answer" value={draft.answer} onChange={(e) => setDraft({ ...draft, answer: e.target.value })} />
                  <input className="rounded-lg border border-gray-200 px-3 py-2 text-sm" placeholder="Tags, comma separated" value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
                  <select className="rounded-lg border border-gray-200 px-3 py-2 text-sm" value={draft.visibility} onChange={(e) => setDraft({ ...draft, visibility: e.target.value })}>
                    <option value="ORG">Shared with teachers</option>
                    <option value="PRIVATE">Private</option>
                  </select>
                  <button
                    type="button"
                    disabled={!draft.title.trim() || !draft.answer.trim() || createItemMutation.isPending}
                    onClick={() => createItemMutation.mutate()}
                    className="rounded-lg bg-gray-950 px-4 py-2 text-sm font-bold text-white disabled:bg-gray-300"
                  >
                    Save question
                  </button>
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {items.map((item: any) => {
                const meta = TYPE_META[item.assignment_type] || { label: item.assignment_type, Icon: FileQuestion }
                const Icon = meta.Icon
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
                      <button onClick={() => deleteItem(item.item_uuid)} className="rounded-md p-2 text-gray-400 hover:bg-rose-50 hover:text-rose-600">
                        <Trash2 size={15} />
                      </button>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{meta.label}</span>
                      <span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600">{item.difficulty}</span>
                      <span className="rounded-full bg-gray-950 px-2 py-1 text-[11px] font-bold text-white">{item.visibility === 'ORG' ? 'Shared' : 'Private'}</span>
                      {(item.tags || []).map((tag: string) => (
                        <span key={tag} className="rounded-full bg-cyan-50 px-2 py-1 text-[11px] font-bold text-cyan-700">{tag}</span>
                      ))}
                    </div>
                  </article>
                )
              })}
            </div>

            {!itemsQuery.isLoading && items.length === 0 && (
              <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center">
                <BookOpenCheck className="mx-auto text-gray-300" size={44} />
                <p className="mt-3 text-sm font-bold text-gray-800">No saved questions yet</p>
                <p className="mt-1 text-xs text-gray-500">Generate AI assignment tasks or create questions here to build your shared bank.</p>
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
