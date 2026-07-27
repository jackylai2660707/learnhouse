import { getAPIUrl } from '@services/config/config'
import {
  RequestBodyFormWithAuthHeader,
  RequestBodyWithAuthHeader,
} from '@services/utils/ts/requests'

export async function uploadNewImageFile(
  file: any,
  activity_uuid: string,
  access_token: string
) {
  // Send file thumbnail as form data
  const formData = new FormData()
  formData.append('file_object', file)
  formData.append('activity_uuid', activity_uuid)

  const result = await fetch(
    `${getAPIUrl()}blocks/image`,
    RequestBodyFormWithAuthHeader('POST', formData, null, access_token)
  )

  const data = await result.json()

  if (!result.ok) {
    const errorMessage = typeof data?.detail === 'string'
      ? data.detail
      : Array.isArray(data?.detail)
        ? data.detail.map((e: any) => e.msg).join(', ')
        : 'Upload failed'
    throw new Error(errorMessage)
  }

  return data
}

export async function getImageFile(file_id: string, access_token: string) {
  // todo : add course id to url
  return fetch(
    `${getAPIUrl()}blocks/image?file_id=${file_id}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
    .then((result) => result.json())
    .catch((error) => console.log('error', error))
}

export async function generateAIImageBlock(
  prompt: string,
  activity_uuid: string,
  org_id: number,
  access_token: string,
  options?: {
    size?: '1024x1024' | '1024x1536' | '1536x1024'
    quality?: 'low' | 'medium' | 'high' | 'auto'
  }
) {
  const response = await fetch(
    `${getAPIUrl()}ai/images/generate`,
    RequestBodyWithAuthHeader(
      'POST',
      {
        org_id,
        activity_uuid,
        prompt,
        size: options?.size || '1024x1024',
        quality: options?.quality || 'medium',
      },
      null,
      access_token
    )
  )

  const data = await response.json()

  if (!response.ok) {
    const errorMessage = typeof data?.detail === 'string'
      ? data.detail
      : Array.isArray(data?.detail)
        ? data.detail.map((e: any) => e.msg).join(', ')
        : typeof data?.detail?.message === 'string'
          ? data.detail.message
          : 'AI 生圖暫時不可用，請稍後重試或手動上傳圖片。'
    throw new Error(errorMessage)
  }

  return data
}
