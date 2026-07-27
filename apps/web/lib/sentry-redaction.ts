const REDACTED = '[REDACTED]'

const SENSITIVE_KEY_PARTS = [
  'apikey',
  'authorization',
  'clientsecret',
  'connectionstring',
  'cookie',
  'databaseurl',
  'dsn',
  'password',
  'passwd',
  'privatekey',
  'refreshtoken',
  'secret',
  'sessioncookie',
  'token',
]

const normalizeKey = (key: string) => key.toLowerCase().replace(/[^a-z0-9]/g, '')

const isSensitiveKey = (key: string) => {
  const normalized = normalizeKey(key)
  return SENSITIVE_KEY_PARTS.some((part) => normalized.includes(part))
}

export function redactSentryText(value: string): string {
  return value
    .replace(
      /\b(?:postgres(?:ql)?|mysql|mariadb|redis|rediss|mongodb(?:\+srv)?|amqp|amqps):\/\/[^\s"'<>]+/gi,
      REDACTED,
    )
    .replace(/([a-z][a-z0-9+.-]*:\/\/)[^/\s@]+@/gi, `$1${REDACTED}@`)
    .replace(
      /\b(password|passwd|pwd|secret|token|api[-_ ]?key|authorization|cookie|dsn)\b(\s*[:=]\s*)("[^"]*"|'[^']*'|Bearer\s+[^\s,;&]+|[^\s,;&]+)/gi,
      `$1$2${REDACTED}`,
    )
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, `Bearer ${REDACTED}`)
    .replace(/\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b/g, REDACTED)
}

function redactValue(value: unknown, seen: WeakSet<object>): unknown {
  if (typeof value === 'string') return redactSentryText(value)
  if (value === null || typeof value !== 'object') return value
  if (seen.has(value)) return '[CIRCULAR]'
  seen.add(value)

  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      value[index] = redactValue(item, seen)
    })
    seen.delete(value)
    return value
  }

  const record = value as Record<string, unknown>
  Object.entries(record).forEach(([key, item]) => {
    record[key] = isSensitiveKey(key) ? REDACTED : redactValue(item, seen)
  })
  seen.delete(value)
  return value
}

export function redactSentryEvent<T>(event: T): T {
  const sanitized = redactValue(event, new WeakSet()) as T
  if (!sanitized || typeof sanitized !== 'object') return sanitized

  const root = sanitized as Record<string, unknown>
  const request = root.request
  if (request && typeof request === 'object' && !Array.isArray(request)) {
    const requestRecord = request as Record<string, unknown>
    if (typeof requestRecord.url === 'string') {
      requestRecord.url = requestRecord.url.split(/[?#]/, 1)[0]
    }
    for (const field of ['cookies', 'data', 'query_string']) {
      if (field in requestRecord) requestRecord[field] = REDACTED
    }
  }

  const user = root.user
  if (user && typeof user === 'object' && !Array.isArray(user)) {
    const userRecord = user as Record<string, unknown>
    for (const field of ['email', 'ip_address', 'username']) {
      if (field in userRecord) userRecord[field] = REDACTED
    }
  }
  return sanitized
}
