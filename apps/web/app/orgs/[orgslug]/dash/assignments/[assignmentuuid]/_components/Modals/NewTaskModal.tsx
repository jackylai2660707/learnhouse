import { useAssignmentsTaskDispatch } from '@components/Contexts/Assignments/AssignmentsTaskContext';
import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext';
import { useLHSession } from '@components/Contexts/LHSessionContext';
import { useOrg } from '@components/Contexts/OrgContext';
import { createAssignmentTask, generateAssignmentTasks, getAssignmentAiStatus } from '@services/courses/assignments'
import { addQuestionBankItemToAssignment, getQuestionBankItems } from '@services/question-bank/question-bank'
import {
  ListChecks,
  NotePencil,
  PencilSimple,
  TextAa,
} from '@phosphor-icons/react'
import { Loader2, Sparkles } from 'lucide-react'
import React from 'react'
import toast from 'react-hot-toast';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { useTranslation } from 'react-i18next';
import { v4 as uuidv4 } from 'uuid';
import {
  SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES,
  SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET,
  SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT,
  SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS,
  getSimplePilotAssignmentTaskSetupIssue,
  getSimplePilotQuestionBankGradeSetupIssue,
} from '@lib/simple-pilot-assignments'

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

type AssignmentAiStatus = {
  ready?: boolean
  provider?: string
  model?: string
  message?: string
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
    value: 'ESSAY',
    Icon: NotePencil,
    labelKey: 'dashboard.assignments.editor.task_types.essay.title',
    descKey: 'dashboard.assignments.editor.task_types.essay.description',
    iconColor: 'text-violet-500',
    titleColor: 'text-violet-900',
    bgClass: 'bg-violet-50',
    stripeRgb: '221, 214, 254',
  },
]

const SIMPLE_AI_TASK_TYPES = [...SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES]
const SIMPLE_MANUAL_TASK_TYPES = SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET
const SIMPLE_AI_MAX_TASK_COUNT = SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS
const SIMPLE_AI_DEFAULT_TASK_COUNT = SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT
const AI_PROMPT_PRESETS = [
  {
    labelKey: 'dashboard.assignments.editor.ai_generator.presets.basic_practice',
    fallback: '基礎練習',
    build: (context: string, count: number) => `${context}，生成 ${count} 題基礎練習，包含選擇題、填空題和短問答。答案要短、清楚、可自動批改，適合零基礎或剛學完的學生。`,
  },
  {
    labelKey: 'dashboard.assignments.editor.ai_generator.presets.review',
    fallback: '課後複習',
    build: (context: string, count: number) => `${context}，生成 ${count} 題課後複習題，先檢查基本概念，再用關鍵詞或一句短答案檢查學生是否掌握重點。`,
  },
  {
    labelKey: 'dashboard.assignments.editor.ai_generator.presets.mini_quiz',
    fallback: '小測驗',
    build: (context: string, count: number) => `${context}，生成 ${count} 題簡短小測驗，題目不要太難，選項要清楚，方便系統自動批改。`,
  },
]
const AI_TOPIC_EXAMPLES = [
  {
    label: '小四數學：分數比較',
    prompt: '小四數學，單元：分數比較。重點是同分母分數、簡單異分母分數和大小比較符號。',
  },
  {
    label: '小五科學：水循環',
    prompt: '小五科學，單元：水循環。重點是蒸發、凝結、降雨和水循環的基本次序。',
  },
  {
    label: '初一地理：澳門位置',
    prompt: '初一地理，單元：澳門地理位置。重點是澳門所在區域、鄰近城市和基本地圖閱讀。',
  },
  {
    label: '中一英文：現在式',
    prompt: '中一英文，單元：Simple Present Tense。重點是主詞、動詞變化和日常句子填空。',
  },
]

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  if (response instanceof Error && response.message) return response.message
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；')
  return fallback
}

const GENERIC_AI_CONTEXT_VALUES = new Set([
  '作業',
  '新作業',
  '未命名作業',
  '練習',
  '課後練習',
  '小測驗',
  '測驗',
  '本課內容',
  '根據本課內容',
  'homework',
  'assignment',
  'test assignment',
])

function meaningfulAiContextResidue(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text || GENERIC_AI_CONTEXT_VALUES.has(text.toLocaleLowerCase())) return ''
  const genericPhrases = [
    '根據本課內容',
    '根據以上內容',
    '本課內容',
    '生成',
    '出題',
    '題目',
    '作業',
    '練習',
    '基礎',
    '簡單',
    '選擇題',
    '填空題',
    '短問答',
    '問答題',
    '自動批改',
    '自動評分',
    '學生',
    '請',
    '為',
    '包含',
    '只包含',
    '答案',
    '清楚',
    '課後複習',
    '小測驗',
    '基本概念',
    '關鍵詞',
    '一句短答案',
    '零基礎',
    '剛學完',
    '題目不要太難',
    '選項要清楚',
    '方便系統',
    '方便提交',
    '批改',
  ]
  const withoutGenericWords = genericPhrases.reduce(
    (current, phrase) => current.replaceAll(phrase, ''),
    text
  )
  return withoutGenericWords
    .replace(/[0-9０-９一二三四五六七八九十]+\s*題/g, '')
    .replace(/[0-9０-９一二三四五六七八九十]+/g, '')
    .replace(/[\s，。；：！？、,.!?;:()（）【】《》「」『』"'`]+/g, '')
}

function hasSpecificAiContextValue(value: unknown) {
  return meaningfulAiContextResidue(value).length >= 2
}

function prefixedUuid(prefix: string) {
  return `${prefix}_${uuidv4()}`
}

function starterTaskContents(type: string) {
  if (type === 'QUIZ') {
    return {
      questions: [
        {
          questionText: '請修改題目：澳門特別行政區位於哪個國家？',
          questionUUID: prefixedUuid('question'),
          options: [
            {
              optionUUID: prefixedUuid('option'),
              text: '選項 A',
              fileID: '',
              type: 'text',
              assigned_right_answer: false,
            },
            {
              optionUUID: prefixedUuid('option'),
              text: '選項 B',
              fileID: '',
              type: 'text',
              assigned_right_answer: false,
            },
          ],
        },
      ],
    }
  }

  if (type === 'FORM') {
    return {
      questions: [
        {
          questionText: '請修改題目：澳門特別行政區位於____。只留一個空格，答案要短。',
          questionUUID: prefixedUuid('question'),
          blanks: [
            {
              blankUUID: prefixedUuid('blank'),
              placeholder: '填寫答案',
              correctAnswer: '',
              hint: '請填入簡短答案',
            },
          ],
        },
      ],
    }
  }

  if (type === 'SHORT_ANSWER') {
    return {
      prompt: '請修改題目：澳門特別行政區位於哪個國家？',
      correct_answers: [''],
      match_mode: 'case_insensitive',
      explanation: '',
    }
  }

  if (type === 'ESSAY') {
    return {
      prompt: '請修改題目：以「一次難忘的校園活動」為題，寫一篇短文。',
      min_words: '150',
      max_words: '400',
      rubric: [
        { key: 'content', label: '內容切題', description: '回應題目，觀點清楚，例子合適。' },
        { key: 'structure', label: '結構組織', description: '開頭、段落、承接和結尾清晰。' },
        { key: 'language', label: '語言表達', description: '用詞、句式和語氣準確。' },
        { key: 'mechanics', label: '錯別字與標點', description: '錯別字、標點和基本語法。' },
        { key: 'creativity', label: '創意與思考', description: '有個人思考、細節和吸引力。' },
      ],
    }
  }

  return {}
}

function starterTaskText(type: string, tr: (key: string, fallback: string) => string) {
  if (type === 'QUIZ') {
    return {
      title: tr('dashboard.assignments.editor.starter_tasks.quiz.title', '請修改：選擇題'),
      description: tr('dashboard.assignments.editor.starter_tasks.quiz.description', '把題目和選項改成課堂內容，並勾選一個正確答案。'),
      hint: tr('dashboard.assignments.editor.starter_tasks.quiz.hint', '選擇題只保留一個正確答案，學生提交後可自動批改。'),
    }
  }

  if (type === 'FORM') {
    return {
      title: tr('dashboard.assignments.editor.starter_tasks.form.title', '請修改：填空題'),
      description: tr('dashboard.assignments.editor.starter_tasks.form.description', '把題目改成課堂內容，並填入一個簡短正確答案。'),
      hint: tr('dashboard.assignments.editor.starter_tasks.form.hint', '填空答案建議用名詞、數字、符號或短詞。'),
    }
  }

  if (type === 'SHORT_ANSWER') {
    return {
      title: tr('dashboard.assignments.editor.starter_tasks.short_answer.title', '請修改：短問答'),
      description: tr('dashboard.assignments.editor.starter_tasks.short_answer.description', '把問題改成可以用關鍵詞或一句短答案回答的題目。'),
      hint: tr('dashboard.assignments.editor.starter_tasks.short_answer.hint', '避免感想、作文、解釋原因等需要老師主觀批改的問題。'),
    }
  }

  if (type === 'ESSAY') {
    return {
      title: '請修改：作文題',
      description: '讓學生輸入一篇短文；提交後 AI 會產生初步分數、分項評語和改善建議。',
      hint: '作文 AI 批改是建議分，老師仍可在提交記錄中覆核確認。',
    }
  }

  return {
    title: tr('dashboard.assignments.editor.default_task_title', '未命名題目'),
    description: '',
    hint: '',
  }
}

function NewTaskModal({ closeModal, assignment_uuid }: any) {
  const { t } = useTranslation()
  const session = useLHSession() as any;
  const access_token = session?.data?.tokens?.access_token;
  const org = useOrg() as any
  const assignment = useAssignments() as any
  const assignmentObject = assignment?.assignment_object || {}
  const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
  const queryClient = useQueryClient()
  const [aiPrompt, setAiPrompt] = React.useState('')
  const [bankSearch, setBankSearch] = React.useState('')
  const [isAddingBankItem, setIsAddingBankItem] = React.useState<string | null>(null)
  const [isGenerating, setIsGenerating] = React.useState(false)
  const [creatingTaskType, setCreatingTaskType] = React.useState<string | null>(null)
  const [showOtherWays, setShowOtherWays] = React.useState(false)
  const [showAiPromptOptions, setShowAiPromptOptions] = React.useState(false)
  const [aiGenerationError, setAiGenerationError] = React.useState('')
  const otherWaysRef = React.useRef<HTMLDivElement | null>(null)
  const hasAutoOpenedFallbackRef = React.useRef(false)
  const manualTaskTypes = TASK_TYPES.filter((type) => (
    SIMPLE_MANUAL_TASK_TYPES.has(type.value) || type.value === 'ESSAY'
  ))
  const existingTaskCount = Array.isArray(assignment?.assignment_tasks) ? assignment.assignment_tasks.length : 0
  const remainingTaskSlots = Math.max(0, SIMPLE_AI_MAX_TASK_COUNT - existingTaskCount)
  const aiCount = remainingTaskSlots > 0
    ? Math.min(SIMPLE_AI_DEFAULT_TASK_COUNT, remainingTaskSlots)
    : 0
  const aiCountHelperText = remainingTaskSlots <= 0
    ? `這份作業已達 ${SIMPLE_AI_MAX_TASK_COUNT} 題上限，請建立另一份簡單作業。`
    : remainingTaskSlots < SIMPLE_AI_DEFAULT_TASK_COUNT
      ? `這份作業接近上限，本次只會補 ${aiCount} 題，保持每份作業最多 ${SIMPLE_AI_MAX_TASK_COUNT} 題。`
      : `固定出 ${SIMPLE_AI_DEFAULT_TASK_COUNT} 題，方便老師檢查和學生完成；建議一堂課一份短練習。這份作業還可新增 ${remainingTaskSlots} 題。`

  const tr = (key: string, fallback: string) => t(key, { defaultValue: fallback })
  const generateButtonLabel = t(
    'dashboard.assignments.editor.ai_generator.generate_with_count',
    {
      count: aiCount,
      defaultValue: `生成 ${aiCount} 題並儲存答案`,
    }
  )
  const assignmentTitleForAI = React.useMemo(
    () => String(assignmentObject.title || '').trim().slice(0, 500),
    [assignmentObject.title]
  )
  const isPublishedAssignment = Boolean(assignmentObject.published)
  const assignmentDescriptionForAI = React.useMemo(
    () => String(assignmentObject.description || '').trim().slice(0, 2000),
    [assignmentObject.description]
  )
  const assignmentContext = React.useMemo(() => {
    const parts = [
      assignmentTitleForAI ? `作業：${assignmentTitleForAI}` : '',
      assignmentDescriptionForAI ? `說明：${assignmentDescriptionForAI}` : '',
      assignmentObject.subject ? `科目：${assignmentObject.subject}` : '',
      assignmentObject.grade_level ? `年級：${assignmentObject.grade_level}` : '',
      assignmentObject.unit ? `單元：${assignmentObject.unit}` : '',
    ].filter(Boolean)
    return parts.length > 0 ? parts.join('，') : '根據本課內容'
  }, [
    assignmentDescriptionForAI,
    assignmentObject.grade_level,
    assignmentObject.subject,
    assignmentTitleForAI,
    assignmentObject.unit,
  ])
  const defaultAiPrompt = React.useMemo(
    () => `${assignmentContext}，生成 ${aiCount} 題簡單作業，只包含選擇題、填空題和短問答。每題只問一個重點，題目要短、答案要明確，短問答只用關鍵詞或一句短答案，方便學生提交後自動批改。`,
    [aiCount, assignmentContext]
  )
  const hasSpecificAiGenerationContext = React.useMemo(() => {
    const learningObjectives = Array.isArray(assignmentObject.learning_objectives)
      ? assignmentObject.learning_objectives
      : []
    return [
      assignmentTitleForAI,
      assignmentDescriptionForAI,
      assignmentObject.unit,
      aiPrompt,
      ...learningObjectives,
    ].some(hasSpecificAiContextValue)
  }, [
    aiPrompt,
    assignmentDescriptionForAI,
    assignmentObject.learning_objectives,
    assignmentObject.unit,
    assignmentTitleForAI,
  ])
  const shouldShowAiPromptOptions = showAiPromptOptions || !hasSpecificAiGenerationContext
  const aiPromptPlaceholder = hasSpecificAiGenerationContext
    ? tr(
      'dashboard.assignments.editor.ai_generator.prompt_placeholder',
      '可留空直接出題。也可以補充：題目要更基礎、只考本節課關鍵詞。'
    )
    : '請輸入真實課題或單元，例如：小四分數比較、澳門世界文化遺產、水循環。'
  const taskTypeLabel = (value: string) => {
    const taskType = TASK_TYPES.find((type) => type.value === value)
    return taskType ? tr(taskType.labelKey, value) : value
  }
  const bankItemsQuery = useQuery({
    queryKey: queryKeys.questionBank.itemsSearch(
      org?.id ?? 0,
      JSON.stringify({
        bankSearch,
        subject: assignmentObject.subject,
        education_stage: assignmentObject.education_stage,
        grade_level: assignmentObject.grade_level,
        unit: assignmentObject.unit,
      })
    ),
    queryFn: async () => {
      const res = await getQuestionBankItems({
        org_id: org.id,
        q: bankSearch,
        subject: assignmentObject.subject || undefined,
        education_stage: assignmentObject.education_stage || undefined,
        grade_level: assignmentObject.grade_level || undefined,
        unit: assignmentObject.unit || undefined,
      }, access_token)
      if (res.success === false) {
        throw new Error(res?.data?.detail || '讀取題庫失敗')
      }
      return Array.isArray(res.data) ? res.data : []
    },
    enabled: showOtherWays && !!org?.id && !!access_token,
    staleTime: 30_000,
  })
  const questionBankItemSetupIssue = React.useCallback((item: any) => (
    getSimplePilotAssignmentTaskSetupIssue(item)
    || getSimplePilotQuestionBankGradeSetupIssue(item, assignmentObject.grade_level)
  ), [assignmentObject.grade_level])
  const simpleBankItems = React.useMemo(
    () => (Array.isArray(bankItemsQuery.data) ? bankItemsQuery.data : []).filter((item) =>
      SIMPLE_MANUAL_TASK_TYPES.has(item.assignment_type)
      && !questionBankItemSetupIssue(item)
    ),
    [bankItemsQuery.data, questionBankItemSetupIssue]
  )
  const hiddenBankItemCount = React.useMemo(
    () => Math.max(0, (Array.isArray(bankItemsQuery.data) ? bankItemsQuery.data.length : 0) - simpleBankItems.length),
    [bankItemsQuery.data, simpleBankItems.length]
  )
  const hiddenBankItemSetupIssue = React.useMemo(
    () => {
      const bankItems = Array.isArray(bankItemsQuery.data) ? bankItemsQuery.data : []
      const hiddenItem = bankItems.find((item) => (
        SIMPLE_MANUAL_TASK_TYPES.has(item.assignment_type)
        && questionBankItemSetupIssue(item)
      ))
      return hiddenItem ? questionBankItemSetupIssue(hiddenItem) : ''
    },
    [bankItemsQuery.data, questionBankItemSetupIssue]
  )
  const authLoadingMessage = tr('dashboard.assignments.editor.auth_loading', '登入狀態載入中，請稍後再試。')
  const assignmentAiStatusQuery = useQuery({
    queryKey: queryKeys.ai.assignmentGenerationStatus(),
    queryFn: async () => {
      const res = await getAssignmentAiStatus(access_token)
      if (res.success === false) {
        throw new Error(responseErrorMessage(res, tr('dashboard.assignments.editor.ai_generator.status_error', '檢查 AI 出題狀態失敗。')))
      }
      return (res.data || res) as AssignmentAiStatus
    },
    enabled: !!access_token,
    staleTime: 60_000,
    retry: 1,
  })
  const assignmentAiStatus = assignmentAiStatusQuery.data as AssignmentAiStatus | undefined
  const assignmentAiStatusMessage = typeof assignmentAiStatus?.message === 'string' ? assignmentAiStatus.message.trim() : ''
  const assignmentAiStatusErrorMessage = assignmentAiStatusQuery.isError
    ? ((assignmentAiStatusQuery.error as Error)?.message || tr('dashboard.assignments.editor.ai_generator.status_error', '檢查 AI 出題狀態失敗。'))
    : assignmentAiStatusMessage
  const assignmentAiUnavailableMessage = assignmentAiStatusErrorMessage
    || tr('dashboard.assignments.editor.ai_generator.not_ready_body', 'AI 出題尚未配置完整。可先用題庫或手動建立選擇、填空、短問答；AI 之後再配置。')
  const isAssignmentAiReady = assignmentAiStatus?.ready === true
  const hasKnownAssignmentAiStatus = !!assignmentAiStatus || assignmentAiStatusQuery.isError
  const isCheckingAssignmentAiStatus = !!access_token && assignmentAiStatusQuery.isFetching && !hasKnownAssignmentAiStatus
  const isRetryingAssignmentAiStatus = !!access_token && assignmentAiStatusQuery.isFetching && hasKnownAssignmentAiStatus
  const isAssignmentAiUnavailable = !!access_token && hasKnownAssignmentAiStatus && !isAssignmentAiReady
  const generateButtonText = (() => {
    if (isGenerating) {
      return tr('dashboard.assignments.editor.ai_generator.generating', '生成中')
    }
    if (!access_token) {
      return tr('dashboard.assignments.editor.ai_generator.auth_loading_short', '登入中')
    }
    if (isPublishedAssignment) {
      return tr('dashboard.assignments.editor.ai_generator.published_locked_button', '已發布不能新增')
    }
    if (remainingTaskSlots <= 0) {
      return tr('dashboard.assignments.editor.ai_generator.limit_reached_button', '已達題目上限')
    }
    if (!hasSpecificAiGenerationContext) {
      return tr('dashboard.assignments.editor.ai_generator.context_required_button', '先輸入課題')
    }
    if (isCheckingAssignmentAiStatus) {
      return tr('dashboard.assignments.editor.ai_generator.status_loading_short', '檢查 AI 中')
    }
    if (!isAssignmentAiReady) {
      return tr('dashboard.assignments.editor.ai_generator.not_ready_button', 'AI 尚未就緒')
    }
    return generateButtonLabel
  })()

  function refreshAssignmentDashboardState() {
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignment_uuid) })
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
    if (!org?.id) return
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org.id) })
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(org.id) })
  }

  function refreshQuestionBankItems() {
    if (!org?.id) return
    queryClient.invalidateQueries({ queryKey: queryKeys.questionBank.items(org.id) })
    queryClient.invalidateQueries({ queryKey: queryKeys.questionBank.selfTestReadiness(org.id) })
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org.id) })
  }

  function showFallbackMethods() {
    setShowOtherWays(true)
    window.setTimeout(() => {
      otherWaysRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 0)
  }

  React.useEffect(() => {
    if (isAssignmentAiReady) {
      hasAutoOpenedFallbackRef.current = false
      return
    }
    if (isAssignmentAiUnavailable && !showOtherWays && !hasAutoOpenedFallbackRef.current) {
      hasAutoOpenedFallbackRef.current = true
      setShowOtherWays(true)
    }
  }, [isAssignmentAiReady, isAssignmentAiUnavailable, showOtherWays])

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
    if (creatingTaskType) return
    if (!access_token) {
      toast.error(authLoadingMessage)
      return
    }
    if (remainingTaskSlots <= 0) {
      toast.error(`這份作業已有 ${SIMPLE_AI_MAX_TASK_COUNT} 題。請建立另一份簡單作業。`)
      return
    }
    if (isPublishedAssignment) {
      toast.error('已發布作業不能新增題目。請先取消發布，或建立新作業。')
      return
    }
    const starterText = starterTaskText(type, tr)
    const task_object = {
      title: starterText.title,
      description: starterText.description,
      hint: starterText.hint,
      reference_file: "",
      assignment_type: type,
      contents: starterTaskContents(type),
      max_grade_value: 100,
    }
    setCreatingTaskType(type)
    try {
      const res = await createAssignmentTask(task_object, assignment_uuid, access_token)
      if (res.success === false) {
        toast.error(responseErrorMessage(res, t('dashboard.assignments.editor.toasts.task_save_error')))
        return
      }
      toast.success(t('dashboard.assignments.editor.toasts.task_created'))
      showReminderToast()
      refreshAssignmentDashboardState()
      assignmentTaskStateHook({ type: 'setSelectedAssignmentTaskUUID', payload: res.data.assignment_task_uuid })
      closeModal(false)
    } catch (error) {
      toast.error(responseErrorMessage(error, t('dashboard.assignments.editor.toasts.task_save_error')))
    } finally {
      setCreatingTaskType(null)
    }
  }

  async function generateWithAI() {
    setAiGenerationError('')
    if (!access_token) {
      toast.error(authLoadingMessage)
      return
    }
    if (remainingTaskSlots <= 0) {
      toast.error(`這份作業已有 ${SIMPLE_AI_MAX_TASK_COUNT} 題。請建立另一份簡單作業。`)
      return
    }
    if (isPublishedAssignment) {
      toast.error('已發布作業不能新增題目。請先取消發布，或建立新作業。')
      return
    }
    if (!hasSpecificAiGenerationContext) {
      setShowAiPromptOptions(true)
      toast.error('請先輸入課題、單元、作業說明或學習目標，再用 AI 一鍵出題。')
      return
    }
    if (isCheckingAssignmentAiStatus) {
      toast(tr('dashboard.assignments.editor.ai_generator.status_loading', '正在檢查 AI 出題狀態，請稍候。'))
      return
    }
    if (!isAssignmentAiReady) {
      showFallbackMethods()
      toast.error(assignmentAiUnavailableMessage)
      return
    }
    const promptForAI = aiPrompt.trim() || defaultAiPrompt
    setIsGenerating(true)
    let res: any
    try {
      res = await generateAssignmentTasks(
        {
          assignment_uuid,
          prompt: promptForAI,
          count: aiCount,
          difficulty: 'beginner',
          question_types: SIMPLE_AI_TASK_TYPES,
          include_images: false,
          save_to_question_bank: true,
          question_bank_tags: [
            assignmentObject.subject,
            assignmentObject.grade_level,
            assignmentObject.unit,
          ].map((tag) => String(tag || '').trim()).filter(Boolean),
          subject: assignmentObject.subject || '',
          assignment_title: assignmentTitleForAI,
          assignment_description: assignmentDescriptionForAI,
          education_stage: assignmentObject.education_stage || '',
          grade_level: assignmentObject.grade_level || '',
          unit: assignmentObject.unit || '',
          learning_objectives: assignmentObject.learning_objectives || [],
          language: 'zh',
        },
        access_token
      )
    } catch (error) {
      const message = responseErrorMessage(error, tr('dashboard.assignments.editor.ai_generator.error', 'AI 出題失敗。'))
      setAiGenerationError(message)
      showFallbackMethods()
      toast.error(message)
      return
    } finally {
      setIsGenerating(false)
    }

    if (res.success === false) {
      const message = responseErrorMessage(res, tr('dashboard.assignments.editor.ai_generator.error', 'AI 出題失敗。'))
      setAiGenerationError(message)
      showFallbackMethods()
      toast.error(message)
      return
    }

    const warnings = Array.isArray(res?.data?.warnings)
      ? res.data.warnings.filter((warning: any) => typeof warning === 'string' && warning.trim())
      : []
    const tasks = res?.data?.tasks ?? []
    toast.success(
      warnings.length > 0
        ? tr('dashboard.assignments.editor.ai_generator.success_with_warnings', 'AI 題目已生成。請先檢查提示和題目內容，再發布給學生。')
        : tr('dashboard.assignments.editor.ai_generator.success', 'AI 題目已生成。下一步按右上角「發布給學生」。')
    )
    if (warnings.length > 0) {
      const visibleWarnings = warnings.slice(0, 3)
      const warningText = visibleWarnings.map((warning: string) => `• ${warning}`).join('\n')
      toast(
        warnings.length > visibleWarnings.length
          ? `${warningText}\n還有 ${warnings.length - visibleWarnings.length} 條提示。`
          : warningText,
        { duration: 12000, icon: '!' }
      )
    }
    showReminderToast()
    refreshAssignmentDashboardState()
    refreshQuestionBankItems()
    if (tasks[0]?.assignment_task_uuid) {
      assignmentTaskStateHook({ type: 'setSelectedAssignmentTaskUUID', payload: tasks[0].assignment_task_uuid })
    }
    closeModal(false)
  }

  async function addBankItemToAssignment(item: any) {
    if (!access_token) {
      toast.error(authLoadingMessage)
      return
    }
    if (remainingTaskSlots <= 0) {
      toast.error(`這份作業已有 ${SIMPLE_AI_MAX_TASK_COUNT} 題。請建立另一份簡單作業。`)
      return
    }
    if (isPublishedAssignment) {
      toast.error('已發布作業不能新增題目。請先取消發布，或建立新作業。')
      return
    }
    setIsAddingBankItem(item.item_uuid)
    try {
      const res = await addQuestionBankItemToAssignment(item.item_uuid, assignment_uuid, access_token)
      if (res.success === false) {
        toast.error(responseErrorMessage(res, '加入題目失敗'))
        return
      }
      toast.success('已從題庫加入題目')
      refreshAssignmentDashboardState()
      if (res?.data?.task?.assignment_task_uuid) {
        assignmentTaskStateHook({ type: 'setSelectedAssignmentTaskUUID', payload: res.data.task.assignment_task_uuid })
      }
      closeModal(false)
    } catch (error) {
      toast.error(responseErrorMessage(error, '加入題目失敗'))
    } finally {
      setIsAddingBankItem(null)
    }
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
              {tr('dashboard.assignments.editor.ai_generator.title', 'AI 一鍵出簡單題')}
            </p>
            <p className="text-xs text-gray-500">
              {tr('dashboard.assignments.editor.ai_generator.subtitle', '輸入課題後，AI 生成選擇、填空、短問答並儲存答案；學生提交後系統自動批改。')}
            </p>
          </div>
        </div>

        <div className="p-4 grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-[11px] font-semibold text-gray-600">
              <span>科目：{assignmentObject.subject || '未設定'}</span>
              <span>年級：{assignmentObject.grade_level || '未設定'}</span>
              <span>單元：{assignmentObject.unit || '未設定'}</span>
              <span>題目：{existingTaskCount}/{SIMPLE_AI_MAX_TASK_COUNT}</span>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-3">
              <p className="text-sm font-bold text-gray-900">
                {tr('dashboard.assignments.editor.ai_generator.one_click_note_title', '老師只要輸入課題，AI 直接出簡單作業。')}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-gray-500">
                {tr('dashboard.assignments.editor.ai_generator.one_click_note_body', '系統會建立基礎選擇題、填空題和短問答，並把答案一併儲存。老師不用寫 AI 指令，也不用逐題設定答案；學生提交後會即時出分。')}
              </p>
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                {[
                  ['1', '填課題'],
                  ['2', '生成題目'],
                  ['3', '發布給學生'],
                ].map(([step, label]) => (
                  <div key={step} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[11px] font-bold text-gray-700">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-950 text-[10px] text-white">
                      {step}
                    </span>
                    <span>{label}</span>
                  </div>
                ))}
              </div>
              {hasSpecificAiGenerationContext ? (
                <button
                  type="button"
                  onClick={() => setShowAiPromptOptions((value) => !value)}
                  className="mt-3 inline-flex h-8 items-center rounded-lg border border-gray-200 bg-white px-3 text-xs font-bold text-gray-700 hover:border-gray-900 hover:text-gray-900"
                >
                  {showAiPromptOptions ? '收起補充內容' : '補充課題或要求（可選）'}
                </button>
              ) : (
                <p className="mt-3 text-[11px] font-semibold text-amber-700">
                  請在下方填寫課題或單元，例如「分數比較」。
                </p>
              )}
            </div>
            {shouldShowAiPromptOptions && (
              <div className="space-y-2">
                <label className="block text-[11px] font-semibold uppercase text-gray-500">
                  {hasSpecificAiGenerationContext ? '補充內容（可選）' : '課題 / 單元'}
                </label>
                <textarea
                  value={aiPrompt}
                  onChange={(e) => setAiPrompt(e.target.value)}
                  rows={hasSpecificAiGenerationContext ? 3 : 2}
                  className="w-full resize-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-gray-900 focus:ring-2 focus:ring-gray-900/10"
                  placeholder={aiPromptPlaceholder}
                />
                <div className="space-y-1.5">
                  <p className="text-[11px] font-semibold text-gray-500">
                    {hasSpecificAiGenerationContext ? '出題方式（可選）' : '快速填入示例課題'}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {hasSpecificAiGenerationContext
                      ? AI_PROMPT_PRESETS.map((preset) => (
                        <button
                          key={preset.labelKey}
                          type="button"
                          onClick={() => setAiPrompt(preset.build(assignmentContext, aiCount))}
                          className="inline-flex h-8 items-center rounded-full border border-gray-200 bg-white px-3 text-xs font-bold text-gray-600 hover:border-gray-900 hover:text-gray-900"
                        >
                          {tr(preset.labelKey, preset.fallback)}
                        </button>
                      ))
                      : AI_TOPIC_EXAMPLES.map((example) => (
                        <button
                          key={example.label}
                          type="button"
                          onClick={() => setAiPrompt(example.prompt)}
                          className="inline-flex h-8 items-center rounded-full border border-cyan-200 bg-cyan-50 px-3 text-xs font-bold text-cyan-800 hover:border-cyan-700 hover:bg-white"
                        >
                          {example.label}
                        </button>
                      ))}
                  </div>
                </div>
              </div>
            )}
            <p className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-[11px] font-semibold leading-relaxed text-gray-600">
              {tr('dashboard.assignments.editor.ai_generator.simple_note', '系統固定生成選擇題、填空題和短問答；短問答只接受關鍵詞或一句短答案，不生成附圖、程式、作文、長篇問答或需要老師主觀批改的題目。數學或科學的短數字答案會放入填空/短問答，仍可自動批改。')}
            </p>
            {isPublishedAssignment && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
                已發布作業不能新增題目，避免學生作答期間題目變動。請先取消發布，或建立新作業。
              </p>
            )}
            {isCheckingAssignmentAiStatus && (
              <p className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[11px] font-semibold text-blue-800">
                {tr('dashboard.assignments.editor.ai_generator.status_loading', '正在檢查 AI 出題狀態，請稍候。')}
              </p>
            )}
            {isAssignmentAiReady && (
              <p className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] font-semibold text-emerald-800">
                {tr('dashboard.assignments.editor.ai_generator.ready', 'AI 出題已就緒，可以生成簡單自動批改題。')}
              </p>
            )}
            {isAssignmentAiUnavailable && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                <p className="text-[11px] font-black text-amber-900">
                  {tr('dashboard.assignments.editor.ai_generator.not_ready_title', 'AI 出題尚未準備好')}
                </p>
                <p className="mt-1 text-[11px] font-semibold leading-relaxed text-amber-800">
                  {assignmentAiUnavailableMessage}
                </p>
                {assignmentAiStatusErrorMessage && assignmentAiStatusErrorMessage !== assignmentAiUnavailableMessage && (
                  <p className="mt-1 text-[10px] font-medium leading-relaxed text-amber-700">
                    {tr('dashboard.assignments.editor.ai_generator.status_detail_prefix', '狀態')}：{assignmentAiStatusErrorMessage}
                  </p>
                )}
                <button
                  type="button"
                  onClick={showFallbackMethods}
                  className="mt-2 inline-flex h-8 items-center rounded-lg border border-amber-300 bg-white px-3 text-xs font-bold text-amber-900 hover:border-amber-700"
                >
                  {tr('dashboard.assignments.editor.ai_generator.use_manual_or_bank', '改用題庫或手動出題')}
                </button>
                <button
                  type="button"
                  onClick={() => assignmentAiStatusQuery.refetch()}
                  disabled={isRetryingAssignmentAiStatus}
                  className="ml-2 mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 text-xs font-bold text-amber-900 hover:border-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isRetryingAssignmentAiStatus && <Loader2 size={13} className="animate-spin" />}
                  {isRetryingAssignmentAiStatus ? '檢查中' : '重新檢查 AI'}
                </button>
              </div>
            )}
            {aiGenerationError && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2">
                <p className="text-[11px] font-black text-rose-900">
                  {tr('dashboard.assignments.editor.ai_generator.failed_title', 'AI 本次未能出題')}
                </p>
                <p className="mt-1 text-[11px] font-semibold leading-relaxed text-rose-800">
                  {aiGenerationError}
                </p>
                <p className="mt-1 text-[11px] font-medium leading-relaxed text-rose-700">
                  {tr('dashboard.assignments.editor.ai_generator.failed_fallback', '不用等待 AI，也可以先用題庫或手動建立選擇、填空、短問答，學生仍可自動批改。')}
                </p>
                <button
                  type="button"
                  onClick={showFallbackMethods}
                  className="mt-2 inline-flex h-8 items-center rounded-lg border border-rose-300 bg-white px-3 text-xs font-bold text-rose-900 hover:border-rose-700"
                >
                  {tr('dashboard.assignments.editor.ai_generator.failed_fallback_cta', '立即用題庫或手動出題')}
                </button>
              </div>
            )}
            {!hasSpecificAiGenerationContext && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-semibold leading-relaxed text-amber-800">
                先輸入具體課題再生成，例如「分數比較」、「澳門世界文化遺產」或「水循環」。可在作業標題、單元、說明或補充要求中填寫。
              </p>
            )}
          </div>

          <div className="space-y-3">
            <label className="block">
              <span className="text-[11px] font-semibold uppercase text-gray-500">
                {tr('dashboard.assignments.editor.ai_generator.count', '本次生成')}
              </span>
              {remainingTaskSlots <= 0 ? (
                <div className="mt-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
                  已達 {SIMPLE_AI_MAX_TASK_COUNT} 題上限
                </div>
              ) : (
                <div className="mt-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-black text-gray-900">
                  {aiCount} 題簡單練習
                </div>
              )}
              <span className="mt-1 block text-[11px] font-medium leading-snug text-gray-500">
                {aiCountHelperText}
              </span>
            </label>

            <button
              type="button"
              onClick={generateWithAI}
              disabled={!access_token || isGenerating || remainingTaskSlots <= 0 || isPublishedAssignment || !hasSpecificAiGenerationContext || isCheckingAssignmentAiStatus || !isAssignmentAiReady}
              className="flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-gray-900 px-3 text-sm font-bold text-white transition-colors hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isGenerating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
              <span>
                {generateButtonText}
              </span>
            </button>
            {!access_token && (
              <p className="text-[11px] font-semibold text-amber-700">
                {authLoadingMessage}
              </p>
            )}
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={() => {
          if (showOtherWays) {
            setShowOtherWays(false)
            return
          }
          showFallbackMethods()
        }}
        className="flex h-9 w-full items-center justify-center rounded-lg border border-gray-200 bg-white px-3 text-xs font-bold text-gray-700 hover:bg-gray-50"
      >
        {showOtherWays ? '收起題庫和手動出題' : '展開題庫或手動出題'}
      </button>

      {showOtherWays && (
        <div ref={otherWaysRef} className="space-y-5">
          <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50">
              <div>
                <p className="text-sm font-bold text-gray-900">
                  {tr('dashboard.assignments.editor.question_bank.title', '題庫')}
                </p>
                <p className="text-xs text-gray-500">
                  {tr('dashboard.assignments.editor.question_bank.subtitle', '重用老師已儲存的校本題目。')}
                </p>
              </div>
              <input
                value={bankSearch}
                onChange={(e) => setBankSearch(e.target.value)}
                placeholder={tr('dashboard.assignments.editor.question_bank.search', '搜尋題庫')}
                className="h-9 w-52 rounded-lg border border-gray-200 px-3 text-xs outline-none focus:border-gray-900"
              />
            </div>
            <div className="max-h-56 overflow-y-auto p-3">
              {hiddenBankItemCount > 0 && (
                <p className="mb-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] font-semibold leading-relaxed text-amber-800">
                  已隱藏 {hiddenBankItemCount} 題不適合直接發布的題目：只顯示內容完整、可自動批改的選擇題、填空題和短問答。
                  {hiddenBankItemSetupIssue ? ` ${hiddenBankItemSetupIssue}` : ''}
                </p>
              )}
              {bankItemsQuery.isError ? (
                <div className="px-2 py-4 text-center">
                  <p className="text-xs font-bold text-rose-600">
                    {(bankItemsQuery.error as Error)?.message || '讀取題庫失敗'}
                  </p>
                  <button
                    type="button"
                    onClick={() => bankItemsQuery.refetch()}
                    disabled={bankItemsQuery.isFetching}
                    className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 text-xs font-bold text-rose-700 hover:border-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {bankItemsQuery.isFetching && <Loader2 size={13} className="animate-spin" />}
                    {bankItemsQuery.isFetching ? '載入中' : '重新載入題庫'}
                  </button>
                </div>
              ) : simpleBankItems.length === 0 ? (
                <div className="px-2 py-4 text-center">
                  <p className="text-xs font-bold text-gray-500">
                    {tr('dashboard.assignments.editor.question_bank.empty', '暫時沒有符合條件的題目。')}
                  </p>
                  <p className="mt-1 text-[11px] font-medium leading-relaxed text-gray-400">
                    {isAssignmentAiReady
                      ? '可先用上方 AI 生成 3 題，或用下方常用題型手動新增選擇、填空、短問答。'
                      : 'AI 尚未就緒時，可先用下方常用題型手動新增選擇、填空、短問答。'}
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                  {simpleBankItems.slice(0, 8).map((item: any) => (
                    <button
                      key={item.item_uuid}
                      type="button"
                      onClick={() => addBankItemToAssignment(item)}
                      disabled={!access_token || remainingTaskSlots <= 0 || isAddingBankItem === item.item_uuid}
                      className="rounded-lg border border-gray-200 p-3 text-left hover:border-gray-900 disabled:opacity-50"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-xs font-black text-gray-900">{item.title}</p>
                        <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500">
                          {taskTypeLabel(item.assignment_type)}
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
                {tr('dashboard.assignments.editor.ai_generator.manual_title', '常用題型')}
              </p>
            </div>
            {isPublishedAssignment && (
              <p className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
                已發布作業不能新增題目，避免學生作答期間題目變動。請先取消發布，或建立新作業。
              </p>
            )}
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
              {manualTaskTypes.map((type) => {
                const IconComponent = type.Icon
                return (
                  <button
                    key={type.value}
                    type="button"
                    onClick={() => createTask(type.value)}
                    disabled={!access_token || remainingTaskSlots <= 0 || !!creatingTaskType || isPublishedAssignment}
                    className={`relative flex flex-col items-center text-center p-5 rounded-xl nice-shadow cursor-pointer transition-all hover:scale-[1.02] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100 overflow-hidden ${type.bgClass}`}
                    style={{
                      backgroundImage: `repeating-linear-gradient(135deg, transparent, transparent 5px, rgba(${type.stripeRgb},0.5) 5px, rgba(${type.stripeRgb},0.5) 6px)`,
                    }}
                  >
                    <div className={`w-14 h-14 rounded-full bg-white nice-shadow flex items-center justify-center mb-3 ${type.iconColor}`}>
                      {creatingTaskType === type.value ? <Loader2 size={24} className="animate-spin" /> : <IconComponent size={28} weight="duotone" />}
                    </div>
                    <p className={`text-sm font-bold ${type.titleColor}`}>
                      {type.value === 'ESSAY' ? '作文題' : t(type.labelKey)}
                    </p>
                    <p className="text-[11px] text-gray-600 leading-tight mt-1 max-w-[180px]">
                      {type.value === 'ESSAY'
                        ? '學生寫短文，AI 產生分項評分與改善建議。'
                        : t(type.descKey)}
                    </p>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default NewTaskModal
