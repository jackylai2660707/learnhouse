import { useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { useOrg } from '@components/Contexts/OrgContext';
import { createAssignmentTask, generateAssignmentTasks } from '@services/courses/assignments'
import { addQuestionBankItemToAssignment, getQuestionBankItems } from '@services/question-bank/question-bank'
import {
  Code,
  FileArrowUp,
  Hash,
  ListChecks,
  PencilSimple,
  TextAa,
} from '@phosphor-icons/react'
import { Loader2, Sparkles } from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { useTranslation } from 'react-i18next';

// Light color themes for each task type. `stripeRgb` is used to build the
// repeating-linear-gradient pattern that gives each card its subtle wallpaper
// effect (same technique as AssignmentActivityModal).
type TaskTypeConfig = {
  value: string
  Icon: React.ComponentType<{ size?: number; weight?: any; className?: string }>
  labelKey: string
  descKey: string
  iconColor: string
  titleColor: string
  bgClass: string
  stripeRgb: string
}

const TASK_TYPES: TaskTypeConfig[] = [
  {
    value: 'QUIZ',
    Icon: ListChecks,
    labelKey: 'dashboard.assignments.editor.task_types.quiz.title',
    descKey: 'dashboard.assignments.editor.task_types.quiz.description',
    iconColor: 'text-sky-500',
    titleColor: 'text-sky-900',
    bgClass: 'bg-sky-50',
    stripeRgb: '186, 230, 253',
  },
  {
    value: 'FILE_SUBMISSION',
    Icon: FileArrowUp,
    labelKey: 'dashboard.assignments.editor.task_types.file_submission.title',
    descKey: 'dashboard.assignments.editor.task_types.file_submission.description',
    iconColor: 'text-violet-500',
    titleColor: 'text-violet-900',
    bgClass: 'bg-violet-50',
    stripeRgb: '221, 214, 254',
  },
  {
    value: 'FORM',
    Icon: TextAa,
    labelKey: 'dashboard.assignments.editor.task_types.form.title',
    descKey: 'dashboard.assignments.editor.task_types.form.description',
    iconColor: 'text-rose-500',
    titleColor: 'text-rose-900',
    bgClass: 'bg-rose-50',
    stripeRgb: '254, 205, 211',
  },
  {
    value: 'CODE',
    Icon: Code,
    labelKey: 'dashboard.assignments.editor.task_types.code.title',
    descKey: 'dashboard.assignments.editor.task_types.code.description',
    iconColor: 'text-emerald-500',
    titleColor: 'text-emerald-900',
    bgClass: 'bg-emerald-50',
    stripeRgb: '187, 247, 208',
  },
  {
    value: 'SHORT_ANSWER',
    Icon: PencilSimple,
    labelKey: 'dashboard.assignments.editor.task_types.short_answer.title',
    descKey: 'dashboard.assignments.editor.task_types.short_answer.description',
    iconColor: 'text-cyan-500',
    titleColor: 'text-cyan-900',
    bgClass: 'bg-cyan-50',
    stripeRgb: '207, 250, 254',
  },
  {
    value: 'NUMBER_ANSWER',
    Icon: Hash,
    labelKey: 'dashboard.assignments.editor.task_types.number_answer.title',
    descKey: 'dashboard.assignments.editor.task_types.number_answer.description',
    iconColor: 'text-amber-500',
    titleColor: 'text-amber-900',
    bgClass: 'bg-amber-50',
    stripeRgb: '253, 230, 138',
  },
]

const AI_TASK_TYPES = [
  { value: 'QUIZ', labelKey: 'dashboard.assignments.editor.ai_generator.types.quiz', fallback: 'Quiz' },
  { value: 'SHORT_ANSWER', labelKey: 'dashboard.assignments.editor.ai_generator.types.short_answer', fallback: 'Short answer' },
  { value: 'NUMBER_ANSWER', labelKey: 'dashboard.assignments.editor.ai_generator.types.number_answer', fallback: 'Number answer' },
  { value: 'CODE', labelKey: 'dashboard.assignments.editor.ai_generator.types.code', fallback: 'Code' },
  { value: 'FORM', labelKey: 'dashboard.assignments.editor.ai_generator.types.form', fallback: 'Fill blanks' },
]

function NewTaskModal({ closeModal, assignment_uuid }: any) {
  const { t } = useTranslation()
  const session = useLHSession() as any;
  const access_token = session?.data?.tokens?.access_token;
  const org = useOrg() as any
  const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
  const queryClient = useQueryClient()
  const [aiPrompt, setAiPrompt] = React.useState('')
  const [aiCount, setAiCount] = React.useState(5)
  const [aiDifficulty, setAiDifficulty] = React.useState('intermediate')
  const [aiTaskTypes, setAiTaskTypes] = React.useState<string[]>(['QUIZ', 'SHORT_ANSWER', 'NUMBER_ANSWER', 'CODE'])
  const [includeImages, setIncludeImages] = React.useState(false)
  const [saveToQuestionBank, setSaveToQuestionBank] = React.useState(true)
  const [questionBankTags, setQuestionBankTags] = React.useState('')
  const [bankSearch, setBankSearch] = React.useState('')
  const [isAddingBankItem, setIsAddingBankItem] = React.useState<string | null>(null)
  const [isGenerating, setIsGenerating] = React.useState(false)

  const tr = (key: string, fallback: string) => t(key, { defaultValue: fallback })
  const bankItemsQuery = useQuery({
    queryKey: ['question-bank-items', org?.id, bankSearch],
    queryFn: async () => (await getQuestionBankItems({ org_id: org.id, q: bankSearch }, access_token)).data,
    enabled: !!org?.id && !!access_token,
    staleTime: 30_000,
  })

  function showReminderToast() {
    // Check if the reminder has already been shown using sessionStorage
    if (sessionStorage.getItem("TasksReminderShown") !== "true") {
      setTimeout(() => {
        toast(t('dashboard.assignments.editor.toasts.reminder'),
          { icon: '✋', duration: 10000, style: { minWidth: 600 } });
        // Mark the reminder as shown in sessionStorage
        sessionStorage.setItem("TasksReminderShown", "true");
      }, 3000);
    }
  }

  async function createTask(type: string) {
    const task_object = {
      title: "Untitled Task",
      description: "",
      hint: "",
      reference_file: "",
      assignment_type: type,
      contents: {},
      max_grade_value: 100,
    }
    const res = await createAssignmentTask(task_object, assignment_uuid, access_token)
    toast.success(t('dashboard.assignments.editor.toasts.task_created'))
    showReminderToast()
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignment_uuid) })
    assignmentTaskStateHook({ type: 'setSelectedAssignmentTaskUUID', payload: res.data.assignment_task_uuid })
    closeModal(false)
  }

  function toggleAiTaskType(type: string) {
    setAiTaskTypes((current) => {
      if (current.includes(type)) {
        return current.length === 1 ? current : current.filter((item) => item !== type)
      }
      return [...current, type]
    })
  }

  async function generateWithAI() {
    if (!aiPrompt.trim()) {
      toast.error(tr('dashboard.assignments.editor.ai_generator.prompt_required', 'Describe what the homework should cover.'))
      return
    }

    setIsGenerating(true)
    let res: any
    try {
      res = await generateAssignmentTasks(
        {
          assignment_uuid,
          prompt: aiPrompt,
          count: aiCount,
          difficulty: aiDifficulty,
          question_types: aiTaskTypes,
          include_images: includeImages,
          save_to_question_bank: saveToQuestionBank,
          question_bank_tags: questionBankTags.split(',').map((tag) => tag.trim()).filter(Boolean),
          language: 'zh',
        },
        access_token
      )
    } catch {
      toast.error(tr('dashboard.assignments.editor.ai_generator.error', 'AI question generation failed.'))
      setIsGenerating(false)
      return
    }
    setIsGenerating(false)

    if (res.success === false) {
      toast.error(res?.data?.detail || tr('dashboard.assignments.editor.ai_generator.error', 'AI question generation failed.'))
      return
    }

    const tasks = res?.data?.tasks ?? []
    toast.success(tr('dashboard.assignments.editor.ai_generator.success', 'AI questions generated.'))
    if (Array.isArray(res?.data?.warnings) && res.data.warnings.length > 0) {
      toast(res.data.warnings[0], { duration: 6000 })
    }
    showReminderToast()
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignment_uuid) })
    if (tasks[0]?.assignment_task_uuid) {
      assignmentTaskStateHook({ type: 'setSelectedAssignmentTaskUUID', payload: tasks[0].assignment_task_uuid })
    }
    closeModal(false)
  }

  async function addBankItemToAssignment(item: any) {
    setIsAddingBankItem(item.item_uuid)
    const res = await addQuestionBankItemToAssignment(item.item_uuid, assignment_uuid, access_token)
    setIsAddingBankItem(null)
    if (res.success === false) {
      toast.error(res?.data?.detail || 'Failed to add question')
      return
    }
    toast.success('Question added from bank')
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignment_uuid) })
    if (res?.data?.task?.assignment_task_uuid) {
      assignmentTaskStateHook({ type: 'setSelectedAssignmentTaskUUID', payload: res.data.task.assignment_task_uuid })
    }
    closeModal(false)
  }

  return (
    <div className="space-y-5 py-1">
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-100 bg-gray-50">
          <div className="h-8 w-8 rounded-lg bg-gray-900 text-white flex items-center justify-center">
            <Sparkles size={16} />
          </div>
          <div>
            <p className="text-sm font-bold text-gray-900">
              {tr('dashboard.assignments.editor.ai_generator.title', 'AI question generator')}
            </p>
            <p className="text-xs text-gray-500">
              {tr('dashboard.assignments.editor.ai_generator.subtitle', 'Create auto-gradable homework tasks from a teacher brief.')}
            </p>
          </div>
        </div>

        <div className="p-4 grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4">
          <div className="space-y-3">
            <textarea
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              rows={5}
              className="w-full resize-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-gray-900 focus:ring-2 focus:ring-gray-900/10"
              placeholder={tr('dashboard.assignments.editor.ai_generator.prompt_placeholder', 'Example: Generate Python loop practice for beginners. Include multiple choice, numeric answers, and one coding challenge with test cases.')}
            />
            <div className="flex flex-wrap gap-2">
              {AI_TASK_TYPES.map((type) => {
                const checked = aiTaskTypes.includes(type.value)
                return (
                  <button
                    key={type.value}
                    type="button"
                    onClick={() => toggleAiTaskType(type.value)}
                    className={`h-8 rounded-lg border px-3 text-xs font-semibold transition-colors ${checked
                      ? 'border-gray-900 bg-gray-900 text-white'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'}`}
                  >
                    {tr(type.labelKey, type.fallback)}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="space-y-3">
            <label className="block">
              <span className="text-[11px] font-semibold uppercase text-gray-500">
                {tr('dashboard.assignments.editor.ai_generator.count', 'Count')}
              </span>
              <select
                value={aiCount}
                onChange={(e) => setAiCount(Number(e.target.value))}
                className="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-white px-2 text-sm outline-none focus:border-gray-900"
              >
                {[1, 2, 3, 4, 5, 6, 8, 10].map((count) => (
                  <option key={count} value={count}>{count}</option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="text-[11px] font-semibold uppercase text-gray-500">
                {tr('dashboard.assignments.editor.ai_generator.difficulty', 'Difficulty')}
              </span>
              <select
                value={aiDifficulty}
                onChange={(e) => setAiDifficulty(e.target.value)}
                className="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-white px-2 text-sm outline-none focus:border-gray-900"
              >
                <option value="beginner">{tr('dashboard.assignments.editor.ai_generator.difficulties.beginner', 'Beginner')}</option>
                <option value="intermediate">{tr('dashboard.assignments.editor.ai_generator.difficulties.intermediate', 'Intermediate')}</option>
                <option value="advanced">{tr('dashboard.assignments.editor.ai_generator.difficulties.advanced', 'Advanced')}</option>
              </select>
            </label>

            <label className="flex items-start gap-2 rounded-lg border border-gray-200 px-3 py-2">
              <input
                type="checkbox"
                checked={includeImages}
                onChange={(e) => setIncludeImages(e.target.checked)}
                className="mt-0.5"
              />
              <span className="text-xs text-gray-600">
                {tr('dashboard.assignments.editor.ai_generator.include_images', 'Attach AI-generated reference images when useful')}
              </span>
            </label>

            <label className="flex items-start gap-2 rounded-lg border border-gray-200 px-3 py-2">
              <input
                type="checkbox"
                checked={saveToQuestionBank}
                onChange={(e) => setSaveToQuestionBank(e.target.checked)}
                className="mt-0.5"
              />
              <span className="text-xs text-gray-600">
                {tr('dashboard.assignments.editor.ai_generator.save_to_bank', 'Save generated questions to the question bank')}
              </span>
            </label>

            {saveToQuestionBank && (
              <input
                value={questionBankTags}
                onChange={(e) => setQuestionBankTags(e.target.value)}
                placeholder={tr('dashboard.assignments.editor.ai_generator.tags_placeholder', 'Tags, comma separated')}
                className="h-9 w-full rounded-lg border border-gray-200 px-2 text-xs outline-none focus:border-gray-900"
              />
            )}

            <button
              type="button"
              onClick={generateWithAI}
              disabled={isGenerating}
              className="flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-gray-900 px-3 text-sm font-bold text-white transition-colors hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isGenerating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
              <span>
                {isGenerating
                  ? tr('dashboard.assignments.editor.ai_generator.generating', 'Generating')
                  : tr('dashboard.assignments.editor.ai_generator.generate', 'Generate tasks')}
              </span>
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50">
          <div>
            <p className="text-sm font-bold text-gray-900">
              {tr('dashboard.assignments.editor.question_bank.title', 'Question bank')}
            </p>
            <p className="text-xs text-gray-500">
              {tr('dashboard.assignments.editor.question_bank.subtitle', 'Reuse shared teacher questions in this assignment.')}
            </p>
          </div>
          <input
            value={bankSearch}
            onChange={(e) => setBankSearch(e.target.value)}
            placeholder={tr('dashboard.assignments.editor.question_bank.search', 'Search bank')}
            className="h-9 w-52 rounded-lg border border-gray-200 px-3 text-xs outline-none focus:border-gray-900"
          />
        </div>
        <div className="max-h-56 overflow-y-auto p-3">
          {(bankItemsQuery.data || []).length === 0 ? (
            <p className="px-2 py-4 text-center text-xs font-medium text-gray-400">
              {tr('dashboard.assignments.editor.question_bank.empty', 'No saved questions yet.')}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              {(bankItemsQuery.data || []).slice(0, 8).map((item: any) => (
                <button
                  key={item.item_uuid}
                  type="button"
                  onClick={() => addBankItemToAssignment(item)}
                  disabled={isAddingBankItem === item.item_uuid}
                  className="rounded-lg border border-gray-200 p-3 text-left hover:border-gray-900 disabled:opacity-50"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-xs font-black text-gray-900">{item.title}</p>
                    <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500">
                      {item.assignment_type}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] text-gray-500">{item.description || item.contents?.prompt}</p>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-bold uppercase text-gray-400">
            {tr('dashboard.assignments.editor.ai_generator.manual_title', 'Create manually')}
          </p>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          {TASK_TYPES.map((type) => {
            const IconComponent = type.Icon
            return (
              <button
                key={type.value}
                type="button"
                onClick={() => createTask(type.value)}
                className={`relative flex flex-col items-center text-center p-5 rounded-xl nice-shadow cursor-pointer transition-all hover:scale-[1.02] active:scale-[0.98] overflow-hidden ${type.bgClass}`}
                style={{
                  backgroundImage: `repeating-linear-gradient(135deg, transparent, transparent 5px, rgba(${type.stripeRgb},0.5) 5px, rgba(${type.stripeRgb},0.5) 6px)`,
                }}
              >
                <div className={`w-14 h-14 rounded-full bg-white nice-shadow flex items-center justify-center mb-3 ${type.iconColor}`}>
                  <IconComponent size={28} weight="duotone" />
                </div>
                <p className={`text-sm font-bold ${type.titleColor}`}>
                  {t(type.labelKey)}
                </p>
                <p className="text-[11px] text-gray-600 leading-tight mt-1 max-w-[180px]">
                  {t(type.descKey)}
                </p>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default NewTaskModal
