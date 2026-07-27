export function isOperationalGradebookRow(row: any): boolean {
  if (!row || typeof row !== 'object') return false
  if (row.source_type === 'self_test') return true
  return row.visible_to_students !== false && row.target_usergroups_valid !== false
}
