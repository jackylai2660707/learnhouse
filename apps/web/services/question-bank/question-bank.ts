import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, getResponseMetadata } from '@services/utils/ts/requests'

export async function getQuestionBankCategories(orgId: number, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/categories?org_id=${orgId}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  return getResponseMetadata(result)
}
export async function createQuestionBankCategory(body: any, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/categories`,
    RequestBodyWithAuthHeader('POST', body, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function getQuestionBankItems(params: any, accessToken: string) {
  const qs = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      qs.set(key, String(value))
    }
  })
  const result = await fetch(
    `${getAPIUrl()}question-bank/items?${qs.toString()}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function createQuestionBankItem(body: any, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/items`,
    RequestBodyWithAuthHeader('POST', body, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function updateQuestionBankItem(itemUuid: string, body: any, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/items/${itemUuid}`,
    RequestBodyWithAuthHeader('PUT', body, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function deleteQuestionBankItem(itemUuid: string, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/items/${itemUuid}`,
    RequestBodyWithAuthHeader('DELETE', null, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function saveAssignmentTaskToQuestionBank(body: any, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/from-assignment-task`,
    RequestBodyWithAuthHeader('POST', body, null, accessToken)
  )
  return getResponseMetadata(result)
}

export async function addQuestionBankItemToAssignment(itemUuid: string, assignmentUuid: string, accessToken: string) {
  const result = await fetch(
    `${getAPIUrl()}question-bank/items/${itemUuid}/add-to-assignment`,
    RequestBodyWithAuthHeader('POST', { assignment_uuid: assignmentUuid }, null, accessToken)
  )
  return getResponseMetadata(result)
}
