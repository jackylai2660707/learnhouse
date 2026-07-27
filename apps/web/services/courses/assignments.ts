import { getAPIUrl } from '@services/config/config'
import {
  RequestBodyFormWithAuthHeader,
  RequestBodyWithAuthHeader,
  getResponseMetadata,
} from '@services/utils/ts/requests'

export async function createAssignment(body: any, access_token: string) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/`,
    RequestBodyWithAuthHeader('POST', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function updateAssignment(
  body: any,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}`,
    RequestBodyWithAuthHeader('PUT', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentFromActivityUUID(
  activityUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/activity/${activityUUID}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

// Delete an assignment
export async function deleteAssignment(
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function deleteAssignmentUsingActivityUUID(
  activityUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/activity/${activityUUID}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

// tasks

export async function createAssignmentTask(
  body: any,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks`,
    RequestBodyWithAuthHeader('POST', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentTasks(
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function generateAssignmentTasks(
  body: any,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}ai/assignments/generate-tasks`,
    RequestBodyWithAuthHeader('POST', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentAiStatus(access_token: string) {
  const result: any = await fetch(
    `${getAPIUrl()}ai/assignments/status`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getMyAssignmentQueue(
  orgId: number,
  access_token: string,
  limit = 5
) {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/my-queue?${params.toString()}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentTask(
  assignmentTaskUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/task/${assignmentTaskUUID}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentTaskSubmissionsMe(
  assignmentTaskUUID: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}/submissions/me`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentTaskSubmissionsUser(
  assignmentTaskUUID: string,
  user_id: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}/submissions/user/${user_id}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function handleAssignmentTaskSubmission(
  body: any,
  assignmentTaskUUID: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}/submissions`,
    RequestBodyWithAuthHeader('PUT', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function updateAssignmentTask(
  body: any,
  assignmentTaskUUID: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}`,
    RequestBodyWithAuthHeader('PUT', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function deleteAssignmentTask(
  assignmentTaskUUID: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function updateReferenceFile(
  file: any,
  assignmentTaskUUID: string,
  assignmentUUID: string,
  access_token: string
) {
  // Send file thumbnail as form data
  const formData = new FormData()

  if (file) {
    formData.append('reference_file', file)
  }
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}/ref_file`,
    RequestBodyFormWithAuthHeader('POST', formData, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function updateSubFile(
  file: any,
  assignmentTaskUUID: string,
  assignmentUUID: string,
  access_token: string
) {
  // Send file thumbnail as form data
  const formData = new FormData()

  if (file) {
    formData.append('sub_file', file)
  }
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/tasks/${assignmentTaskUUID}/sub_file`,
    RequestBodyFormWithAuthHeader('POST', formData, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

// submissions

export async function submitAssignmentForGrading(
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function deleteUserSubmission(
  user_id: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions/${user_id}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function putUserSubmission(
  body: any,
  user_id: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions/${user_id}`,
    RequestBodyWithAuthHeader('PUT', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function putFinalGrade(
  user_id: string,
  assignmentUUID: string,
  access_token: string,
  overall_feedback?: string | null
) {
  // Only send a body when the caller actually passed feedback — otherwise the
  // backend leaves any existing note alone.
  const body =
    overall_feedback !== undefined && overall_feedback !== null
      ? { overall_feedback }
      : null
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions/${user_id}/grade`,
    RequestBodyWithAuthHeader('POST', body, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getFinalGrade(
  user_id: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions/${user_id}/grade`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function retryAssignmentSubmission(
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions/me/retry`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getMyAssignmentRemediation(
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/remediation/my`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function createMyAssignmentRemediation(
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/remediation/my`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function submitMyAssignmentRemediation(
  assignmentUUID: string,
  answers: any[],
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/remediation/my/submit`,
    RequestBodyWithAuthHeader('POST', { answers }, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function markActivityAsDoneForUser(
  user_id: string,
  assignmentUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/${assignmentUUID}/submissions/${user_id}/done`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentsFromACourse(
  courseUUID: string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/course/${courseUUID}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

type GradebookFilters = {
  course_id?: number | string | null
  usergroup_id?: number | string | null
  include_self_tests?: boolean
}

export type SchoolOperationsFilters = {
  start_date?: string | null
  end_date?: string | null
  course_id?: number | string | null
  usergroup_id?: number | string | null
  subject?: string | null
  education_stage?: string | null
  grade_level?: string | null
  school_year?: string | null
  term?: string | null
  include_self_tests?: boolean
}

function schoolOperationsQuery(filters?: SchoolOperationsFilters) {
  const params = new URLSearchParams()
  const scalarKeys: Array<keyof Omit<SchoolOperationsFilters, 'include_self_tests'>> = [
    'start_date',
    'end_date',
    'course_id',
    'usergroup_id',
    'subject',
    'education_stage',
    'grade_level',
    'school_year',
    'term',
  ]
  scalarKeys.forEach((key) => {
    const value = filters?.[key]
    if (value !== null && value !== undefined && String(value).trim()) {
      params.set(key, String(value).trim())
    }
  })
  if (filters?.include_self_tests === false) params.set('include_self_tests', 'false')
  const query = params.toString()
  return query ? `?${query}` : ''
}

function gradebookQuery(filters?: GradebookFilters) {
  const params = new URLSearchParams()
  if (filters?.course_id) params.set('course_id', String(filters.course_id))
  if (filters?.usergroup_id) params.set('usergroup_id', String(filters.usergroup_id))
  if (filters?.include_self_tests) params.set('include_self_tests', 'true')
  const query = params.toString()
  return query ? `?${query}` : ''
}

export async function getTeacherAssignmentWorkbench(
  orgId: number | string,
  access_token: string
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/workbench`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getSchoolOperationsSummary(
  orgId: number | string,
  access_token: string,
  filters?: SchoolOperationsFilters
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/operations-summary${schoolOperationsQuery(filters)}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  return getResponseMetadata(result)
}

export async function downloadSchoolOperationsSummaryCsv(
  orgId: number | string,
  access_token: string,
  filters?: SchoolOperationsFilters
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/operations-summary.csv${schoolOperationsQuery(filters)}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  if (!result.ok) {
    const data = await result.json().catch(() => ({ detail: result.statusText }))
    return {
      success: false,
      data,
      status: result.status,
      HTTPmessage: result.statusText,
    }
  }
  return {
    success: true,
    data: await result.blob(),
    status: result.status,
    HTTPmessage: result.statusText,
  }
}

export async function getAssignmentGradebook(
  orgId: number | string,
  access_token: string,
  filters?: GradebookFilters
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/gradebook${gradebookQuery(filters)}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function getAssignmentGradebookSummary(
  orgId: number | string,
  access_token: string,
  filters?: GradebookFilters
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/gradebook/summary${gradebookQuery(filters)}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  const res = await getResponseMetadata(result)
  return res
}

export async function downloadAssignmentGradebookCsv(
  orgId: number | string,
  access_token: string,
  filters?: GradebookFilters
) {
  const result: any = await fetch(
    `${getAPIUrl()}assignments/org/${orgId}/gradebook.csv${gradebookQuery(filters)}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  if (!result.ok) {
    const data = await result.json().catch(() => ({ detail: result.statusText }))
    return {
      success: false,
      data,
      status: result.status,
      HTTPmessage: result.statusText,
    }
  }
  return {
    success: true,
    data: await result.blob(),
    status: result.status,
    HTTPmessage: result.statusText,
  }
}
