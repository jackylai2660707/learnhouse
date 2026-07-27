export type CitationToken =
  | { type: 'text'; value: string }
  | { type: 'citation'; number: number }

const CITATION_GROUP_RE = /\[(\d{1,3}(?:\s*,\s*\d{1,3}){0,9})\]/g
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function citationLabel(number: number): string {
  return `[${number}]`
}

export function parseCitationText(text: string, sourceCount: number): CitationToken[] {
  if (!text || sourceCount < 1) return [{ type: 'text', value: text }]

  const tokens: CitationToken[] = []
  let lastIndex = 0
  for (const match of text.matchAll(CITATION_GROUP_RE)) {
    const matchIndex = match.index ?? 0
    const numbers = match[1].split(',').map((value) => Number.parseInt(value.trim(), 10))
    const valid = numbers.length > 0 && numbers.every((number) => number >= 1 && number <= sourceCount)
    if (!valid) continue

    if (matchIndex > lastIndex) {
      tokens.push({ type: 'text', value: text.slice(lastIndex, matchIndex) })
    }
    for (const number of [...new Set(numbers)]) {
      tokens.push({ type: 'citation', number })
    }
    lastIndex = matchIndex + match[0].length
  }
  if (lastIndex < text.length) tokens.push({ type: 'text', value: text.slice(lastIndex) })
  return tokens.length ? tokens : [{ type: 'text', value: text }]
}

function canonicalUuid(value: string | undefined, prefix: string): string | null {
  if (!value?.startsWith(prefix)) return null
  const uuid = value.slice(prefix.length)
  return UUID_RE.test(uuid) ? uuid : null
}

export function sourceActivityPath(
  courseUuid: string | undefined,
  activityUuid: string | undefined,
): string | null {
  const courseId = canonicalUuid(courseUuid, 'course_')
  const activityId = canonicalUuid(activityUuid, 'activity_')
  if (!courseId || !activityId) return null
  return `/course/${courseId}/activity/${activityId}`
}
