import assert from 'node:assert/strict'
import test from 'node:test'

import { redactSentryEvent, redactSentryText } from '../lib/sentry-redaction.ts'

test('redacts nested secrets and credential-bearing text', () => {
  const result = redactSentryEvent({
    password: 'plain-password',
    nested: {
      authorization: 'Bearer plain-token',
      message: 'database=postgresql://user:plain-db-password@db.example/app',
    },
  })

  const serialized = JSON.stringify(result)
  assert.equal(result.password, '[REDACTED]')
  assert.equal(result.nested.authorization, '[REDACTED]')
  assert.equal(serialized.includes('plain-'), false)
})

test('removes request payload, query and school user PII', () => {
  const result = redactSentryEvent({
    request: {
      url: 'https://learn.example/assignment?token=plain-token',
      query_string: 'token=plain-token',
      data: { answer: 'student answer' },
      cookies: { session: 'plain-cookie' },
    },
    user: {
      id: 'internal-id',
      email: 'student@example.test',
      username: 'student',
      ip_address: '192.0.2.1',
    },
  })

  assert.equal(result.request.url, 'https://learn.example/assignment')
  assert.equal(result.request.query_string, '[REDACTED]')
  assert.equal(result.request.data, '[REDACTED]')
  assert.equal(result.request.cookies, '[REDACTED]')
  assert.equal(result.user.id, 'internal-id')
  assert.equal(result.user.email, '[REDACTED]')
  assert.equal(result.user.username, '[REDACTED]')
  assert.equal(result.user.ip_address, '[REDACTED]')
})

test('redacts bearer tokens without changing ordinary text', () => {
  assert.equal(redactSentryText('course ready'), 'course ready')
  assert.equal(
    redactSentryText('Authorization: Bearer plain-token'),
    'Authorization: [REDACTED]',
  )
})
