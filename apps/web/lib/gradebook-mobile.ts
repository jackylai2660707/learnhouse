type GradebookMobileRow = {
  assignment_title?: unknown
  source_type?: unknown
  student_name?: unknown
}

/**
 * Gives each compact gradebook record a useful accessible name.  The mobile
 * view does not rely on table headers, so the student and work names need to
 * remain available together to screen-reader users.
 */
export function gradebookMobileRecordLabel(row: GradebookMobileRow | null | undefined) {
  const studentName = String(row?.student_name || '未命名學生').trim() || '未命名學生'
  const fallbackTitle = row?.source_type === 'self_test' ? '自測記錄' : '未命名作業'
  const assignmentTitle = String(row?.assignment_title || fallbackTitle).trim() || fallbackTitle
  return `${studentName}：${assignmentTitle}`
}
