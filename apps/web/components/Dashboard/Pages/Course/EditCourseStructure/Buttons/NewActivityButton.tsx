import { useCourse } from '@components/Contexts/CourseContext'
import NewActivityModal from '@components/Objects/Modals/Activities/Create/NewActivity'
import Modal from '@components/Objects/StyledElements/Modal/Modal'
import {
  createActivity,
  createExternalVideoActivity,
  createFileActivity,
} from '@services/courses/activities'
import { getOrganizationContextInfoWithoutCredentials } from '@services/organizations/orgs'
import { revalidateTags } from '@services/utils/ts/requests'
import { Backpack, Layers } from 'lucide-react'
import { ArrowLeft } from '@phosphor-icons/react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useRouter } from 'next/navigation'
import React, { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@lib/query/keys'
import toast from 'react-hot-toast'
import { useTranslation } from 'react-i18next'

type NewActivityButtonProps = {
  chapterId: string
  orgslug: string
}

function NewActivityButton(props: NewActivityButtonProps) {
  const { t } = useTranslation()
  const [newActivityModal, setNewActivityModal] = React.useState(false)
  const [selectedView, setSelectedView] = React.useState('home')
  const router = useRouter()
  const course = useCourse() as any
  const session = useLHSession() as any;
  const access_token = session?.data?.tokens?.access_token;
  const withUnpublishedActivities = course ? course.withUnpublishedActivities : false
  const queryClient = useQueryClient()
  const cleanCourseUuid = (id: string) => id?.replace(/^course_/, '') ?? id

  const openNewActivityModal = async (chapterId: any) => {
    setSelectedView('home')
    setNewActivityModal(true)
  }

  const openAssignmentModal = async () => {
    setSelectedView('assignments')
    setNewActivityModal(true)
  }

  const closeNewActivityModal = async () => {
    setNewActivityModal(false)
  }

  // Submit new activity
  const submitActivity = async (activity: any) => {
    let org = await getOrganizationContextInfoWithoutCredentials(
      props.orgslug,
      { revalidate: 1800 }
    )
    const toast_loading = toast.loading(t('dashboard.courses.structure.activity.toasts.creating'))
    await createActivity(activity, props.chapterId, org.id, access_token)
    queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(cleanCourseUuid(course.courseStructure.course_uuid)) })
    toast.dismiss(toast_loading)
    toast.success(t('dashboard.courses.structure.activity.toasts.create_success'))
    setNewActivityModal(false)
    await revalidateTags(['courses'], props.orgslug)
    router.refresh()
  }

  // Submit File Upload
  const submitFileActivity = async (
    file: any,
    type: any,
    activity: any,
    chapterId: string
  ) => {
    toast.loading(t('dashboard.courses.structure.activity.toasts.uploading'))
    await createFileActivity(file, type, activity, chapterId, access_token)
    queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(cleanCourseUuid(course.courseStructure.course_uuid)) })
    setNewActivityModal(false)
    toast.dismiss()
    toast.success(t('dashboard.courses.structure.activity.toasts.upload_success'))
    toast.success(t('dashboard.courses.structure.activity.toasts.create_success'))
    await revalidateTags(['courses'], props.orgslug)
    router.refresh()
  }

  // Submit YouTube Video Upload
  const submitExternalVideo = async (
    external_video_data: any,
    activity: any,
    chapterId: string
  ) => {
    const toast_loading = toast.loading(t('dashboard.courses.structure.activity.toasts.creating_uploading'))
    await createExternalVideoActivity(
      external_video_data,
      activity,
      props.chapterId, access_token
    )
    queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(cleanCourseUuid(course.courseStructure.course_uuid)) })
    setNewActivityModal(false)
    toast.dismiss(toast_loading)
    toast.success(t('dashboard.courses.structure.activity.toasts.create_success'))
    await revalidateTags(['courses'], props.orgslug)
    router.refresh()
  }

  useEffect(() => { }, [course])

  const dialogTitle = selectedView !== 'home' ? (
    <div className="flex items-center gap-3">
      <button
        onClick={() => setSelectedView('home')}
        className="flex items-center justify-center h-7 w-7 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
      >
        <ArrowLeft size={18} />
      </button>
      <span>{t('dashboard.courses.structure.modals.new_activity.title')}</span>
    </div>
  ) : (
    t('dashboard.courses.structure.modals.new_activity.title')
  )

  return (
    <div className="flex flex-col items-center justify-center gap-2">
      <Modal
        isDialogOpen={newActivityModal}
        onOpenChange={setNewActivityModal}
        minHeight="no-min"
        minWidth='md'
        addDefCloseButton={false}
        noPadding
        dialogContent={
          <NewActivityModal
            closeModal={closeNewActivityModal}
            submitFileActivity={submitFileActivity}
            submitExternalVideo={submitExternalVideo}
            submitActivity={submitActivity}
            chapterId={props.chapterId}
            course={course}
            selectedView={selectedView}
            setSelectedView={setSelectedView}
          ></NewActivityModal>
        }
        dialogTitle={dialogTitle}
        dialogDescription={selectedView === 'home' ? t('dashboard.courses.structure.modals.new_activity.description') : undefined}
      />
      <div className="my-3 flex flex-col items-center gap-2 sm:flex-row">
        <button
          type="button"
          onClick={openAssignmentModal}
          className="flex h-10 w-44 items-center justify-center rounded-xl bg-gray-950 px-3 py-2 text-white hover:bg-black"
        >
          <Backpack size={17} />
          <span className="ml-2 text-sm font-bold">新增簡單作業</span>
        </button>
        <button
          type="button"
          onClick={() => {
            openNewActivityModal(props.chapterId)
          }}
          className="flex h-10 w-36 items-center justify-center rounded-xl border border-gray-200 bg-white px-3 py-2 text-gray-700 hover:bg-gray-50"
        >
          <Layers size={17} />
          <span className="ml-2 text-sm font-bold">
            {t('dashboard.courses.structure.actions.add_activity')}
          </span>
        </button>
      </div>
      <div className="max-w-sm px-3 text-center text-[11px] font-semibold leading-relaxed text-gray-400">
        校內試行建議先建立 3 題選擇、填空、短問答，學生提交後即可自動批改。
      </div>
    </div>
  )
}

export default NewActivityButton
