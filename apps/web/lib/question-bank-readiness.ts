import {
  getSimplePilotAssignmentTaskSetupIssue,
  isAiFallbackStarterTask,
} from './simple-pilot-assignments'

export const SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT = 3
export const SIMPLE_SELF_TEST_MAX_QUESTION_COUNT = 5
export const SIMPLE_SELF_TEST_TYPES = ['QUIZ', 'FORM', 'SHORT_ANSWER'] as const
export const SIMPLE_SELF_TEST_TYPE_SET = new Set<string>(SIMPLE_SELF_TEST_TYPES)

export type SimpleQuestionBankReadiness = {
  total: number
  counts: Record<(typeof SIMPLE_SELF_TEST_TYPES)[number], number>
  ready: boolean
  canStartPractice: boolean
  enoughForDefaultPractice: boolean
  incompleteSimpleItems: number
  aiFallbackStarterItems: number
}

export function hasQuestionBankText(value: unknown) {
  return Boolean(String(value ?? '').trim())
}

export function coerceQuestionBankAnswerBool(value: unknown) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value === 1
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['true', '1', 'yes', 'y', 'correct', 'right', '是', '對', '正確'].includes(normalized)) return true
    if (['false', '0', 'no', 'n', 'incorrect', 'wrong', '否', '錯', '錯誤', ''].includes(normalized)) return false
  }
  return false
}

export function isSelfTestReadyQuestionBankItem(item: any) {
  if (!item || !SIMPLE_SELF_TEST_TYPE_SET.has(item.assignment_type)) return false
  if (isAiFallbackStarterTask(item)) return false
  if (getSimplePilotAssignmentTaskSetupIssue(item)) return false

  const contents = item.contents || {}
  if (!contents || typeof contents !== 'object') return false

  if (item.assignment_type === 'QUIZ') {
    const questions = Array.isArray(contents.questions) ? contents.questions : []
    if (questions.length === 0) return false
    return questions.every((question: any) => {
      if (!question || typeof question !== 'object') return false
      const options = Array.isArray(question.options) ? question.options : []
      const correctCount = options.filter((option: any) => coerceQuestionBankAnswerBool(option?.assigned_right_answer)).length
      return (
        hasQuestionBankText(question.questionText || question.question) &&
        hasQuestionBankText(question.questionUUID) &&
        options.length >= 2 &&
        correctCount === 1 &&
        options.every((option: any) => (
          hasQuestionBankText(option?.optionUUID) &&
          hasQuestionBankText(option?.text || option?.option || option?.label)
        ))
      )
    })
  }

  if (item.assignment_type === 'FORM') {
    const questions = Array.isArray(contents.questions) ? contents.questions : []
    if (questions.length === 0) return false
    return questions.every((question: any) => {
      if (!question || typeof question !== 'object') return false
      const blanks = Array.isArray(question.blanks) ? question.blanks : []
      return (
        hasQuestionBankText(question.questionText || question.question) &&
        hasQuestionBankText(question.questionUUID) &&
        blanks.length > 0 &&
        blanks.every((blank: any) => (
          blank &&
          typeof blank === 'object' &&
          hasQuestionBankText(blank.blankUUID) &&
          hasQuestionBankText(blank.correctAnswer || blank.correct_answer || blank.answer)
        ))
      )
    })
  }

  if (item.assignment_type === 'SHORT_ANSWER') {
    const matchMode = contents.match_mode || 'case_insensitive'
    if (!['exact', 'case_insensitive'].includes(matchMode)) return false
    const rawAnswers = Array.isArray(contents.correct_answers) && contents.correct_answers.length > 0
      ? contents.correct_answers
      : contents.accepted_answers
    const answers = Array.isArray(rawAnswers)
      ? rawAnswers
      : typeof rawAnswers === 'string'
        ? [rawAnswers]
        : []
    return hasQuestionBankText(contents.prompt || contents.question) && answers.some(hasQuestionBankText)
  }

  return false
}

export function buildQuestionBankReadiness(items: any[]): SimpleQuestionBankReadiness {
  const counts: SimpleQuestionBankReadiness['counts'] = {
    QUIZ: 0,
    FORM: 0,
    SHORT_ANSWER: 0,
  }
  let simpleItemCount = 0
  let aiFallbackStarterItems = 0

  for (const item of items) {
    if (item?.visibility !== 'ORG') continue
    if (!SIMPLE_SELF_TEST_TYPE_SET.has(item?.assignment_type)) continue
    simpleItemCount += 1
    if (isAiFallbackStarterTask(item)) aiFallbackStarterItems += 1
    if (!isSelfTestReadyQuestionBankItem(item)) continue
    counts[item.assignment_type as keyof typeof counts] += 1
  }

  const total = counts.QUIZ + counts.FORM + counts.SHORT_ANSWER
  const enoughForDefaultPractice = total >= SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT
  return {
    total,
    counts,
    ready: enoughForDefaultPractice,
    canStartPractice: total > 0,
    enoughForDefaultPractice,
    incompleteSimpleItems: Math.max(0, simpleItemCount - total - aiFallbackStarterItems),
    aiFallbackStarterItems,
  }
}
