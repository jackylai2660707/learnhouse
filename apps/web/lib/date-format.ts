function parseDateForDisplay(value: string | null | undefined) {
  if (!value) return null
  const trimmed = String(value).trim()
  const dateOnlyMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed)
  const date = dateOnlyMatch
    ? new Date(Number(dateOnlyMatch[1]), Number(dateOnlyMatch[2]) - 1, Number(dateOnlyMatch[3]))
    : new Date(trimmed)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatZhHkDate(value: string | null | undefined, fallback = '-') {
  const date = parseDateForDisplay(value)
  if (!date) return value ? String(value) : fallback
  return date.toLocaleDateString('zh-HK', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

export function formatZhHkDateTime(value: string | null | undefined, fallback = '-') {
  const date = parseDateForDisplay(value)
  if (!date) return value ? String(value) : fallback
  return date.toLocaleString('zh-HK', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
