'use client'
import { useCourse, useCourseDispatch } from '@components/Contexts/CourseContext'
import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import SaveState from './SaveState'
import { CourseOverviewParams } from 'app/orgs/[orgslug]/dash/courses/course/[courseuuid]/[subpage]/page'
import { getUriWithOrg } from '@services/config/config'
import { useOrg } from '@components/Contexts/OrgContext'
import { getCourseThumbnailMediaDirectory } from '@services/media/media'
import Link from 'next/link'
import Image from 'next/image'
import EmptyThumbnailImage from '../../../public/empty_thumbnail.png'
import { BookCopy, BrainCircuit, Eye, Globe, GlobeLock, Loader2, Check } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@components/ui/tooltip'
import { useTranslation } from 'react-i18next'
import { updateCourse } from '@services/courses/courses'
import { getAPIUrl } from '@services/config/config'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { revalidateTags } from '@services/utils/ts/requests'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import toast from 'react-hot-toast'
import { useState, useCallback } from 'react'
import { useCourseRights } from '@components/Hooks/useCourseRights'

type IndexStatus =
  | { type: 'idle' }
  | { type: 'loading'; message: string }
  | { type: 'success'; message: string }
  | { type: 'error'; message: string }

export function CourseOverviewTop({
  params,
}: {
  params: CourseOverviewParams
}) {
  const { t } = useTranslation()
  const course = useCourse() as any
  const dispatchCourse = useCourseDispatch() as any
  const org = useOrg() as any
  const session = useLHSession() as any
  const queryClient = useQueryClient()
  const [isPublishing, setIsPublishing] = useState(false)
  const [isIndexing, setIsIndexing] = useState(false)
  const [isIndexed, setIsIndexed] = useState(false)
  const [indexStatus, setIndexStatus] = useState<IndexStatus>({ type: 'idle' })

  const courseStructure = course?.courseStructure
  const { hasPermission: hasCoursePermission } = useCourseRights(
    courseStructure?.course_uuid ?? ''
  )
  const isPublished = courseStructure?.published
  const isAIEnabled = org?.config?.config?.resolved_features?.ai?.enabled ?? org?.config?.config?.features?.ai?.enabled !== false
  const canIndexCourseForAI = isAIEnabled && hasCoursePermission('update_content')

  const indexCourseForAI = useCallback(async () => {
    if (isIndexing || !canIndexCourseForAI || !courseStructure?.course_uuid) return
    setIsIndexing(true)
    setIsIndexed(false)
    setIndexStatus({ type: 'loading', message: '正在為 AI 重建課程索引，請稍候。' })
    let responseStatus: number | undefined

    try {
      const response = await fetch(`${getAPIUrl()}ai/rag/index`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.data?.tokens?.access_token}`,
        },
        body: JSON.stringify({ course_uuid: courseStructure.course_uuid }),
      })
      responseStatus = response.status

      if (!response.ok) {
        throw new Error(String(response.status))
      }

      const data = await response.json()
      setIsIndexed(true)
      setIndexStatus({
        type: 'success',
        message: `已完成 AI 課程索引，共建立 ${data.chunks_indexed} 個內容片段。`,
      })
    } catch {
      const message = responseStatus === 409
        ? '課程內容在建立索引期間已更新，請稍後再試。'
        : responseStatus === 503
          ? 'AI 索引服務暫時不可用，原有索引已保留。請稍後再試。'
          : 'AI 課程索引未能完成，請稍後再試。'
      setIndexStatus({ type: 'error', message })
    } finally {
      setIsIndexing(false)
    }
  }, [isIndexing, canIndexCourseForAI, courseStructure, session.data?.tokens?.access_token])

  const togglePublishStatus = useCallback(async () => {
    if (isPublishing || !courseStructure?.course_uuid) return
    setIsPublishing(true)

    const newPublishedStatus = !isPublished
    const toastMessage = newPublishedStatus
      ? t('dashboard.courses.publishing')
      : t('dashboard.courses.unpublishing')
    const toastId = toast.loading(toastMessage)

    // Optimistically update local state
    const previousState = { ...courseStructure }
    dispatchCourse({
      type: 'mergePendingChanges',
      payload: { published: newPublishedStatus }
    })

    try {
      await updateCourse(
        courseStructure.course_uuid,
        { published: newPublishedStatus },
        session.data?.tokens?.access_token
      )

      // Invalidate course meta cache so other components see the update
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(params.courseuuid) })

      // Revalidate server-side cache
      await revalidateTags(['courses'], params.orgslug)

      toast.dismiss(toastId)
      toast.success(
        newPublishedStatus
          ? t('dashboard.courses.published_success')
          : t('dashboard.courses.unpublished_success')
      )
    } catch {
      // Rollback on error
      dispatchCourse({
        type: 'mergePendingChanges',
        payload: { published: previousState.published }
      })

      toast.dismiss(toastId)
      toast.error(t('dashboard.courses.publish_error'))
    } finally {
      setIsPublishing(false)
    }
  }, [
    isPublishing,
    isPublished,
    courseStructure,
    queryClient,
    session.data?.tokens?.access_token,
    dispatchCourse,
    params.orgslug,
    params.courseuuid,
    t
  ])

  if (!courseStructure) {
    return null
  }

  return (
    <>
      <div className="pt-6 pb-4">
        <Breadcrumbs items={[
          { label: t('courses.courses'), href: '/dash/courses', icon: <BookCopy size={14} /> },
          { label: courseStructure.name }
        ]} />
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <div className="flex py-2 grow min-w-0 items-center">
          <Link
            href={getUriWithOrg(org?.slug, '') + `/course/${params.courseuuid}`}
            className="shrink-0"
          >
            {courseStructure?.thumbnail_image ? (
              <img
                className="w-[72px] sm:w-[100px] h-[41px] sm:h-[57px] rounded-md drop-shadow-md object-cover"
                src={`${getCourseThumbnailMediaDirectory(
                  org?.org_uuid,
                  'course_' + params.courseuuid,
                  courseStructure.thumbnail_image
                )}`}
                alt={courseStructure.name}
              />
            ) : (
              <Image
                width={100}
                className="w-[72px] sm:w-[100px] h-[41px] sm:h-[57px] rounded-md drop-shadow-md"
                src={EmptyThumbnailImage}
                alt={courseStructure?.name || ''}
              />
            )}
          </Link>
          <div className="flex flex-col course_metadata justify-center pl-3 sm:pl-5 min-w-0">
            <div className="text-gray-400 font-semibold text-xs sm:text-sm">{t('dashboard.courses.overview_top.course_label')}</div>
            <div className="text-black font-bold text-base sm:text-xl -mt-0.5 first-letter:uppercase truncate">
              {courseStructure.name}
            </div>
          </div>
        </div>
        <div className="flex items-center self-center rounded-lg shadow-sm shadow-neutral-300/40 ring-1 ring-neutral-200/60 overflow-hidden shrink-0">
          <SaveState orgslug={params.orgslug} />
          <div className="w-px self-stretch bg-neutral-200/80" />
          <button
            onClick={togglePublishStatus}
            disabled={isPublishing}
            className={`group px-2.5 sm:px-3.5 py-2 text-sm font-semibold flex items-center space-x-1.5 transition-colors ${
              isPublished
                ? 'bg-green-50/70 text-green-700 hover:bg-green-100/70'
                : 'bg-yellow-50/70 text-yellow-700 hover:bg-yellow-100/70'
            } ${isPublishing ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
          >
            {isPublishing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : isPublished ? (
              <Globe className="w-4 h-4" />
            ) : (
              <GlobeLock className="w-4 h-4" />
            )}
            <span className="hidden sm:inline">
              {isPublishing
                ? t('dashboard.courses.processing')
                : isPublished
                  ? t('dashboard.courses.published')
                  : t('dashboard.courses.unpublished')
              }
            </span>
            {!isPublishing && (
              <span className={`hidden sm:inline-flex overflow-hidden max-w-0 group-hover:max-w-[150px] opacity-0 group-hover:opacity-100 transition-all duration-300 ease-in-out`}>
                <span className={`px-1.5 py-0.5 text-[10px] font-semibold rounded whitespace-nowrap ${
                  isPublished
                    ? 'bg-green-200/80 text-green-800'
                    : 'bg-yellow-200/80 text-yellow-800'
                }`}>
                  {isPublished ? t('dashboard.courses.click_to_unpublish') : t('dashboard.courses.click_to_publish')}
                </span>
              </span>
            )}
          </button>
          {canIndexCourseForAI && (
            <>
              <div className="w-px self-stretch bg-neutral-200/80" />
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={indexCourseForAI}
                      disabled={isIndexing}
                      className={`group px-2.5 sm:px-3.5 py-2 text-sm font-semibold flex items-center space-x-1.5 transition-colors ${
                        isIndexed
                          ? 'bg-blue-50/70 text-blue-700'
                          : 'bg-purple-50/70 text-purple-700 hover:bg-purple-100/70'
                      } ${isIndexing ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                    >
                      {isIndexing ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : isIndexed ? (
                        <Check className="w-4 h-4" />
                      ) : (
                        <BrainCircuit className="w-4 h-4" />
                      )}
                      <span className="hidden sm:inline">
                        {isIndexing ? '建立索引中…' : isIndexed ? '索引完成' : '建立 AI 索引'}
                      </span>
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-xs text-xs">
                    <p>建立課程內容索引，讓 AI 助教能在回答問題時搜尋及引用教材。活動更新後系統會自動重新建立索引。</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </>
          )}
          <div className="w-px self-stretch bg-neutral-200/80" />
          <Link
            href={getUriWithOrg(org?.slug, '') + `/course/${params.courseuuid}`}
            target="_blank"
            className="px-2.5 sm:px-3.5 py-2 text-sm font-semibold text-neutral-600 bg-neutral-50/70 hover:bg-neutral-100/70 transition-colors flex items-center space-x-1.5"
          >
            <Eye className="w-4 h-4" />
            <span className="hidden sm:inline">{t('dashboard.courses.preview')}</span>
          </Link>
        </div>
      </div>
      {canIndexCourseForAI && indexStatus.type !== 'idle' && (
        <div
          role={indexStatus.type === 'error' ? 'alert' : 'status'}
          aria-live="polite"
          className={`mt-3 rounded-lg px-3 py-2 text-sm ${
            indexStatus.type === 'error'
              ? 'bg-red-50 text-red-800'
              : indexStatus.type === 'success'
                ? 'bg-green-50 text-green-800'
                : 'bg-blue-50 text-blue-800'
          }`}
        >
          {indexStatus.message}
        </div>
      )}
    </>
  )
}
