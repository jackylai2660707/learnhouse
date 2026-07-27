export const SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES = ['QUIZ', 'FORM', 'SHORT_ANSWER'] as const
export const SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET = new Set<string>(SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES)
export const SIMPLE_PILOT_PUBLISH_TASK_TYPES = ['QUIZ', 'FORM', 'SHORT_ANSWER', 'ESSAY'] as const
export const SIMPLE_PILOT_PUBLISH_TASK_TYPE_SET = new Set<string>(SIMPLE_PILOT_PUBLISH_TASK_TYPES)
export const SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT = 3
export const SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS = 3
export const SIMPLE_PILOT_MAX_FILL_BLANK_ANSWER_LENGTH = 40
export const SIMPLE_PILOT_MAX_SHORT_ANSWER_LENGTH = 40

const SIMPLE_PILOT_AUTO_ANSWER_EXPLANATION_MARKERS = ['\n', '。', '！', '？', ';', '；']
const SIMPLE_PILOT_SHORT_ANSWER_SUBJECTIVE_MARKERS = [
  '分析',
  '討論',
  '評價',
  '感想',
  '作文',
  '短文',
  '段落',
  '總結',
  '解釋',
  '請說明',
  '說明原因',
  '主要原因',
  '為甚麼',
  '為什麼',
  '為何',
  '如何',
  '怎樣',
  '請比較',
  '比較一下',
  '請舉例',
  '舉例說明',
  '請列出',
  '請描述',
  '談談',
  '分享',
  '看法',
  '觀點',
  '建議',
  '證明',
  '推論',
  '反思',
  '創作',
  '設計',
]

type MissingSettingsOptions = {
  requireTargets?: boolean
  targetLabel?: string
  autoGradingLabel?: string
  retryLabel?: string
  answersLabel?: string
  scorePolicyLabel?: string
  booleanMode?: 'falseOnly' | 'truthy'
}

const DEFAULT_MISSING_SETTINGS_OPTIONS: Required<MissingSettingsOptions> = {
  requireTargets: true,
  targetLabel: '指定班級',
  autoGradingLabel: '自動批改',
  retryLabel: '可重做',
  answersLabel: '顯示答案',
  scorePolicyLabel: '最高分計分',
  booleanMode: 'falseOnly',
}

function searchableTaskValue(value: unknown) {
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function optionValue(options: MissingSettingsOptions | undefined) {
  return { ...DEFAULT_MISSING_SETTINGS_OPTIONS, ...(options || {}) }
}

function isMissingBoolean(value: unknown, mode: MissingSettingsOptions['booleanMode']) {
  if (value == null) return mode === 'truthy'
  const enabled = coerceSimplePilotBoolean(value)
  return mode === 'truthy' ? !enabled : !enabled
}

function hasText(value: unknown) {
  return typeof value === 'string' ? value.trim().length > 0 : String(value || '').trim().length > 0
}

export function coerceSimplePilotBoolean(value: unknown) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value === 1
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['true', '1', 'yes', 'y', 'correct', 'right', '是', '對', '正確'].includes(normalized)) return true
    if (['false', '0', 'no', 'n', 'incorrect', 'wrong', '否', '錯', '錯誤', ''].includes(normalized)) return false
  }
  return false
}

function taskTitle(task: any) {
  return String(task?.title || '未命名題目').trim() || '未命名題目'
}

function normalizedSchoolMeta(value: unknown) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '')
}

function optionText(option: any) {
  return String(option?.text || option?.option || option?.label || '').trim()
}

function blankAnswer(blank: any) {
  return String(blank?.correctAnswer || blank?.correct_answer || blank?.answer || '').trim()
}

function shortAnswerKeys(contents: any) {
  const answers = contents?.correct_answers || contents?.accepted_answers || []
  if (typeof answers === 'string') return [answers]
  return Array.isArray(answers) ? answers : []
}

export function getSimplePilotAssignmentTargetUsergroupIds(assignment: any) {
  return Array.isArray(assignment?.target_usergroup_ids)
    ? assignment.target_usergroup_ids.filter((id: any) => Number.isFinite(Number(id)))
    : []
}

export function getSimplePilotAssignmentTargetUsergroupCount(assignment: any) {
  return getSimplePilotAssignmentTargetUsergroupIds(assignment).length
}

export function isSimplePilotAssignmentTaskType(taskType: unknown) {
  return SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET.has(String(taskType || '').toUpperCase())
}

export function getSimplePilotGeneratedTaskType(task: any) {
  return String(task?.assignment_type?.value || task?.assignment_type || '').toUpperCase()
}

export function getMissingSimplePilotGeneratedTaskTypes(tasks: any[]) {
  const generatedTypes = new Set(tasks.map(getSimplePilotGeneratedTaskType))
  return SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES.filter((taskType) => !generatedTypes.has(taskType))
}

export function getSimplePilotGeneratedTaskRepairTypes(tasks: any[]) {
  const remainingSlots = Math.max(0, SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT - tasks.length)
  return getMissingSimplePilotGeneratedTaskTypes(tasks).slice(0, remainingSlots)
}

export async function repairSimplePilotGeneratedTaskSet(
  tasks: any[],
  generateRepairTasks: (taskTypes: string[]) => Promise<any[]>
) {
  const repairTypes = getSimplePilotGeneratedTaskRepairTypes(tasks)
  if (repairTypes.length === 0) return tasks
  return [...tasks, ...await generateRepairTasks(repairTypes)]
}

export function isCompleteSimplePilotGeneratedTaskSet(tasks: any[]) {
  return (
    tasks.length === SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT
    && getMissingSimplePilotGeneratedTaskTypes(tasks).length === 0
  )
}

export function isSimplePilotPublishTaskType(taskType: unknown) {
  return SIMPLE_PILOT_PUBLISH_TASK_TYPE_SET.has(String(taskType || '').toUpperCase())
}

export function countNonSimplePilotAssignmentTasks(tasks: any[]) {
  return tasks.filter((task) => !isSimplePilotPublishTaskType(task?.assignment_type)).length
}

export function isAiFallbackStarterTask(task: any) {
  const text = [
    task?.title,
    task?.description,
    task?.hint,
    task?.contents,
  ].map(searchableTaskValue).join(' ')
  return (
    text.includes('AI 備用題')
    || text.includes('AI 備用')
    || text.includes('請老師檢查後再發布')
    || text.includes('這份練習的主題是')
    || text.includes('這份練習主要學習哪個主題')
    || text.includes('這份練習主要圍繞哪個主題')
  )
}

export function countAiFallbackStarterTasks(tasks: any[]) {
  return tasks.filter(isAiFallbackStarterTask).length
}

export function getSimplePilotAssignmentTaskSetupIssue(task: any) {
  const title = taskTitle(task)
  const taskType = String(task?.assignment_type || '').toUpperCase()
  const contents = task?.contents || {}
  if (!isSimplePilotPublishTaskType(taskType)) {
    return `${title} 不是校內試行支援的簡單題型。校內試行發布主要支援選擇題、填空題、短問答和作文題。`
  }
  if (!contents || typeof contents !== 'object' || Array.isArray(contents)) {
    return `${title} 的題目內容格式不完整。`
  }

  if (taskType === 'QUIZ') {
    const questions = Array.isArray(contents.questions) ? contents.questions : []
    if (questions.length <= 0) return `${title} 尚未設定選擇題題目和答案。`
    for (const question of questions) {
      if (!question || typeof question !== 'object') return `${title} 的選擇題內容格式不完整。`
      if (!hasText(question.questionText || question.question)) return `${title} 尚未設定選擇題題目。`
      const options = Array.isArray(question.options) ? question.options : []
      if (options.length < 2) return `${title} 每題至少需要 2 個選項。`
      if (options.some((option: any) => !option || typeof option !== 'object' || !hasText(optionText(option)))) {
        return `${title} 每個選項都需要文字。`
      }
      const correctCount = options.filter((option: any) => coerceSimplePilotBoolean(option?.assigned_right_answer) && hasText(optionText(option))).length
      if (correctCount !== 1) return `${title} 每題需要剛好 1 個正確答案。`
    }
    return ''
  }

  if (taskType === 'FORM') {
    const questions = Array.isArray(contents.questions) ? contents.questions : []
    if (questions.length <= 0) return `${title} 尚未設定填空題題目和答案。`
    for (const question of questions) {
      if (!question || typeof question !== 'object') return `${title} 的填空題內容格式不完整。`
      if (!hasText(question.questionText || question.question)) return `${title} 尚未設定填空題題目。`
      const blanks = Array.isArray(question.blanks) ? question.blanks : []
      if (blanks.length <= 0) return `${title} 至少需要 1 個填空。`
      for (const blank of blanks) {
        if (!blank || typeof blank !== 'object') return `${title} 每個填空都需要正確答案。`
        const answer = blankAnswer(blank)
        if (!hasText(answer)) return `${title} 每個填空都需要正確答案。`
        if (
          answer.length > SIMPLE_PILOT_MAX_FILL_BLANK_ANSWER_LENGTH
          || SIMPLE_PILOT_AUTO_ANSWER_EXPLANATION_MARKERS.some((marker) => answer.includes(marker))
        ) {
          return `${title} 的填空答案太長，請改成一個詞語、數字或很短的標準答案。`
        }
      }
    }
    return ''
  }

  if (taskType === 'SHORT_ANSWER') {
    const answers = shortAnswerKeys(contents)
    if (!hasText(contents.prompt)) return `${title} 尚未設定短問答題目。`
    if (!answers.some(hasText)) return `${title} 尚未設定短問答可接受答案。`
    const prompt = String(contents.prompt || '')
    if (SIMPLE_PILOT_SHORT_ANSWER_SUBJECTIVE_MARKERS.some((marker) => prompt.includes(marker))) {
      return `${title} 像主觀問答題。校內簡單模式請改成可用關鍵詞、名詞或一句短答案直接自動批改的題目。`
    }
    if (answers.some((answer: any) => {
      const answerText = String(answer || '').trim()
      return (
        answerText.length > SIMPLE_PILOT_MAX_SHORT_ANSWER_LENGTH
        || SIMPLE_PILOT_AUTO_ANSWER_EXPLANATION_MARKERS.some((marker) => answerText.includes(marker))
      )
    })) {
      return `${title} 的短問答答案太長，請改成關鍵詞、名詞或一句短答案。`
    }
    const matchMode = contents.match_mode || 'case_insensitive'
    if (!['exact', 'case_insensitive'].includes(matchMode)) {
      return `${title} 的短問答批改方式太容易誤判，請使用精確或忽略大小寫。`
    }
  }

  if (taskType === 'ESSAY') {
    const prompt = contents.prompt || contents.question || contents.title || task?.description || title
    if (!hasText(prompt)) return `${title} 尚未設定作文題目。`
    return ''
  }

  return ''
}

export function getSimplePilotAssignmentTaskSetupIssues(tasks: any[]) {
  return tasks.map(getSimplePilotAssignmentTaskSetupIssue).filter(Boolean)
}

export function getSimplePilotQuestionBankGradeSetupIssue(item: any, targetGradeLevel: unknown) {
  const itemGrade = normalizedSchoolMeta(item?.grade_level)
  const assignmentGrade = normalizedSchoolMeta(targetGradeLevel)
  if (!itemGrade || !assignmentGrade || itemGrade === assignmentGrade) return ''
  return `${taskTitle(item)} 適合「${item.grade_level}」，但這份作業年級是「${targetGradeLevel}」。請改選同年級題目，或先修正題庫/作業年級。`
}

export function simplePilotQuestionBankMetadataMatches(
  item: any,
  target: { subject?: unknown; gradeLevel?: unknown; unit?: unknown }
) {
  const matches = (itemValue: unknown, targetValue: unknown) => {
    const normalizedItem = normalizedSchoolMeta(itemValue)
    const normalizedTarget = normalizedSchoolMeta(targetValue)
    return !normalizedTarget || !normalizedItem || normalizedItem === normalizedTarget
  }
  return (
    matches(item?.subject, target.subject)
    && matches(item?.grade_level, target.gradeLevel)
    && matches(item?.unit, target.unit)
  )
}

export function getSimplePilotAssignmentMissingSettings(
  assignment: any,
  options?: MissingSettingsOptions
) {
  const settings = optionValue(options)
  const targetUsergroupIds = getSimplePilotAssignmentTargetUsergroupIds(assignment)
  return [
    settings.requireTargets && targetUsergroupIds.length <= 0 ? settings.targetLabel : '',
    isMissingBoolean(assignment?.auto_grading, settings.booleanMode) ? settings.autoGradingLabel : '',
    isMissingBoolean(assignment?.allow_retries, settings.booleanMode) ? settings.retryLabel : '',
    isMissingBoolean(assignment?.show_correct_answers, settings.booleanMode) ? settings.answersLabel : '',
    String(assignment?.score_policy || '') !== 'highest' ? settings.scorePolicyLabel : '',
  ].filter(Boolean)
}

export function isSimplePilotAssignmentSettingsReady(
  assignment: any,
  options?: MissingSettingsOptions
) {
  return getSimplePilotAssignmentMissingSettings(assignment, options).length === 0
}
