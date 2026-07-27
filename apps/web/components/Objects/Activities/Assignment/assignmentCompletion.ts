export function hasSubmittedText(value: any) {
  return Boolean(String(value ?? '').trim())
}

function submittedChoiceOrBlankValue(
  submissionData: any,
  questionUUID: string,
  optionUUID?: string,
  blankUUID?: string
) {
  const submissions = Array.isArray(submissionData?.submissions) ? submissionData.submissions : []
  const match = submissions.find((submission: any) => {
    if (submission?.questionUUID !== questionUUID) return false
    if (optionUUID && submission?.optionUUID !== optionUUID) return false
    if (blankUUID && submission?.blankUUID !== blankUUID) return false
    return true
  })
  return match?.answer
}

function isSubmittedChoiceSelected(value: any) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value === 1
  if (typeof value === 'string') {
    return ['true', '1', 'yes', 'y', 'correct', 'right', '是', '對', '正確'].includes(
      value.trim().toLowerCase()
    )
  }
  return false
}

export function isAssignmentTaskAnswerComplete(task: any, taskSubmission: any) {
  if (!taskSubmission) return false
  const submissionData = taskSubmission.task_submission || {}
  const contents = task.contents || {}

  if (task.assignment_type === 'SHORT_ANSWER' || task.assignment_type === 'NUMBER_ANSWER') {
    return hasSubmittedText(submissionData.answer)
  }

  if (task.assignment_type === 'ESSAY') {
    return hasSubmittedText(
      submissionData.essay || submissionData.answer || submissionData.text || submissionData.content
    )
  }

  if (task.assignment_type === 'QUIZ') {
    const questions = Array.isArray(contents.questions) ? contents.questions : []
    if (questions.length === 0) return false
    return questions.every((question: any) => {
      const options = Array.isArray(question?.options) ? question.options : []
      if (!question?.questionUUID || options.length === 0) return false
      return options.some((option: any) => (
        option?.optionUUID &&
        isSubmittedChoiceSelected(
          submittedChoiceOrBlankValue(submissionData, question.questionUUID, option.optionUUID)
        )
      ))
    })
  }

  if (task.assignment_type === 'FORM') {
    const questions = Array.isArray(contents.questions) ? contents.questions : []
    if (questions.length === 0) return false
    return questions.every((question: any) => {
      const blanks = Array.isArray(question?.blanks) ? question.blanks : []
      if (!question?.questionUUID || blanks.length === 0) return false
      return blanks.every((blank: any) => (
        blank?.blankUUID &&
        hasSubmittedText(submittedChoiceOrBlankValue(submissionData, question.questionUUID, undefined, blank.blankUUID))
      ))
    })
  }

  if (task.assignment_type === 'CODE') {
    if (contents.mode === 'web_preview' || submissionData.mode === 'web_preview') {
      return ['html_code', 'css_code', 'js_code', 'source_code'].some((field) => hasSubmittedText(submissionData[field]))
    }
    return hasSubmittedText(submissionData.source_code)
  }

  if (task.assignment_type === 'FILE_SUBMISSION') {
    return hasSubmittedText(submissionData.fileUUID)
  }

  return true
}

export function assignmentTaskDisplayName(
  task: any,
  index: number,
  fallbackLabel: string,
  maxLength = 32
) {
  return String(task?.title || task?.description || `${fallbackLabel} ${index + 1}`)
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maxLength)
}
