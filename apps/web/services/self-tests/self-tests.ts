import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, getResponseMetadata } from '@services/utils/ts/requests'

export async function startSelfTest(body: any, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}self-tests/start`,
    RequestBodyWithAuthHeader('POST', body, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function submitSelfTest(attemptUuid: string, answers: any[], accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}self-tests/attempts/${attemptUuid}/submit`,
    RequestBodyWithAuthHeader('POST', { answers }, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function getMySelfTestAttempts(orgId: number, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}self-tests/attempts/me?org_id=${orgId}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function getOrgSelfTestAttempts(orgId: number, accessToken: string, userId?: number) {
  const qs = new URLSearchParams({ org_id: String(orgId) })
  if (userId) qs.set('user_id', String(userId))
  const result = await fetch(
    `${getAPIUrl()}self-tests/attempts?${qs.toString()}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function reviewSelfTestAttempt(attemptUuid: string, body: any, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}self-tests/attempts/${attemptUuid}/review`,
    RequestBodyWithAuthHeader('PATCH', body, null, accessToken)
  )
  return getResponseMetadata(result)
}
