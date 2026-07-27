import React from 'react'
import * as Form from '@radix-ui/react-form'
import { BarLoader } from 'react-spinners'
import { Backpack } from '@phosphor-icons/react'
import { useOrg } from '@components/Contexts/OrgContext'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { createAssignment } from '@services/courses/assignments'
import { getUriWithOrg } from '@services/config/config'
import { getUserGroups } from '@services/usergroups/usergroups'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { createActivity, deleteActivity } from '@services/courses/activities'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import toast from 'react-hot-toast'
import { useTranslation } from 'react-i18next'
import {
  School,
  UsersRound,
} from 'lucide-react'

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  if (response instanceof Error && response.message) return response.message
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；')
  return fallback
}

function dateInputValue(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function NewAssignment({ submitActivity, chapterId, course, closeModal }: any) {
  const { t } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const queryClient = useQueryClient()
  const router = useRouter()
  const todayDate = React.useMemo(() => dateInputValue(new Date()), [])
  const defaultDueDate = React.useMemo(() => {
    const date = new Date()
    date.setDate(date.getDate() + 7)
    return dateInputValue(date)
  }, [])
  const cleanCourseUuid = (id: string) => id?.replace(/^course_/, '') ?? id
  const cleanAssignmentUuid = (id: string) => id?.replace(/^assignment_/, '') ?? id
  const withUnpublishedActivities = course
    ? course.withUnpublishedActivities
    : false
  const [activityName, setActivityName] = React.useState('')
  const [isSubmitting, setIsSubmitting] = React.useState(false)
  const [activityDescription, setActivityDescription] = React.useState('')
  const [dueDate, setDueDate] = React.useState(defaultDueDate)
  const [subject, setSubject] = React.useState('')
  const [educationStage, setEducationStage] = React.useState('')
  const [gradeLevel, setGradeLevel] = React.useState('')
  const [schoolYear, setSchoolYear] = React.useState('')
  const [term, setTerm] = React.useState('')
  const [unit, setUnit] = React.useState('')
  const [learningObjectives, setLearningObjectives] = React.useState('')
  const [targetUsergroupIds, setTargetUsergroupIds] = React.useState<number[]>([])
  const quickTitleOptions = [
    '今日課堂 3 題練習',
    '課後快速小測',
    '單元基礎檢查',
  ]

  const usergroupsQuery = useQuery({
    queryKey: queryKeys.usergroups.list(org?.id),
    queryFn: async () => {
      const res = await getUserGroups(org.id, session.data?.tokens?.access_token)
      if (res.success === false) {
        throw new Error(res?.data?.detail || '讀取班級/群組失敗')
      }
      return Array.isArray(res.data) ? res.data : []
    },
    enabled: !!org?.id && !!session.data?.tokens?.access_token,
    staleTime: 60_000,
  })
  const usergroups = Array.isArray(usergroupsQuery.data) ? usergroupsQuery.data : []
  const importStudentsHref = getUriWithOrg(org?.slug || '', '/dash/users/settings/add')
  const manageUsergroupsHref = getUriWithOrg(org?.slug || '', '/dash/users/settings/usergroups')
  const usergroupSelectionTouchedRef = React.useRef(false)

  React.useEffect(() => {
    if (
      usergroupSelectionTouchedRef.current ||
      targetUsergroupIds.length > 0 ||
      usergroups.length !== 1
    ) {
      return
    }
    const onlyGroupId = Number(usergroups[0]?.id)
    if (Number.isFinite(onlyGroupId)) {
      setTargetUsergroupIds([onlyGroupId])
    }
  }, [targetUsergroupIds.length, usergroups])

  const toggleUsergroup = (groupId: number) => {
    usergroupSelectionTouchedRef.current = true
    setTargetUsergroupIds((current) =>
      current.includes(groupId)
        ? current.filter((id) => id !== groupId)
        : [...current, groupId]
    )
  }

  const handleSubmit = async (e: any) => {
    e.preventDefault()
    if (isSubmitting) return
    const cleanedActivityName = activityName.trim()
    if (!cleanedActivityName) {
      toast.error('請輸入作業名稱。')
      return
    }
    if (dueDate && dueDate < todayDate) {
      toast.error('截止日期不能早於今天。')
      return
    }
    setActivityName(cleanedActivityName)
    setIsSubmitting(true)
    const toast_loading = toast.loading(
      t('dashboard.assignments.modals.create.toasts.creating')
    )
    let activity_res: any = null
    const activity = {
      name: cleanedActivityName,
      chapter_id: chapterId,
      activity_type: 'TYPE_ASSIGNMENT',
      activity_sub_type: 'SUBTYPE_ASSIGNMENT_ANY',
      published: false,
      course_id: course?.courseStructure.id,
    }

    try {
      activity_res = await createActivity(
        activity,
        chapterId,
        org?.id,
        session.data?.tokens?.access_token
      )
      if (!activity_res?.id || !activity_res?.activity_uuid) {
        throw new Error(activity_res?.detail || '建立作業活動失敗')
      }

      const res = await createAssignment(
        {
          title: cleanedActivityName,
          description: activityDescription.trim(),
          due_date: dueDate,
          grading_type: 'PERCENTAGE',
          auto_grading: true,
          anti_copy_paste: false,
          show_correct_answers: true,
          allow_retries: true,
          max_retries: 0,
          subject,
          education_stage: educationStage,
          grade_level: gradeLevel,
          school_year: schoolYear,
          term,
          unit,
          learning_objectives: learningObjectives
            .split('\n')
            .map((item) => item.trim())
            .filter(Boolean),
          target_usergroup_ids: targetUsergroupIds,
          score_policy: 'highest',
          teacher_review_required: false,
          teacher_review_status: 'not_required',
          course_id: course?.courseStructure.id,
          org_id: org?.id,
          chapter_id: chapterId,
          activity_id: activity_res.id,
        },
        session.data?.tokens?.access_token
      )

      if (res.success === false) {
        throw new Error(responseErrorMessage(res, '建立作業失敗'))
      }

      queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(cleanCourseUuid(course.courseStructure.course_uuid)) })
      queryClient.invalidateQueries({ queryKey: ['courses'] })
      queryClient.invalidateQueries({ queryKey: ['assignments'] })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
      if (org?.id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org.id) })
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(org.id) })
      }
      const assignmentUuid = res?.data?.assignment_uuid
      toast.success('已建立作業草稿，正在打開作業出題頁。')
      closeModal()
      if (assignmentUuid && org?.slug) {
        router.push(getUriWithOrg(org.slug, `/dash/assignments/${cleanAssignmentUuid(assignmentUuid)}?newTask=1`))
      }
    } catch (error: any) {
      if (activity_res?.activity_uuid) {
        try {
          await deleteActivity(
            activity_res.activity_uuid,
            session.data?.tokens?.access_token
          )
        } catch {
          // Keep the original creation error visible to the teacher.
        }
      }
      toast.error(responseErrorMessage(error, t('dashboard.assignments.modals.create.toasts.error')))
    } finally {
      toast.dismiss(toast_loading)
      setIsSubmitting(false)
    }
  }

  const inputClass =
    'w-full h-9 px-3 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors'

  return (
    <Form.Root onSubmit={handleSubmit} className="space-y-4">
      <div
        className="relative flex items-center justify-center h-20 rounded-xl overflow-hidden"
        style={{
          backgroundImage:
            'repeating-linear-gradient(90deg, transparent, transparent 5px, rgba(253,230,138,0.25) 5px, rgba(253,230,138,0.25) 6px)',
        }}
      >
        <span className="flex items-center gap-2 bg-white nice-shadow rounded-full px-4 py-1.5 text-sm font-medium text-gray-600">
          <Backpack size={18} weight="duotone" className="text-amber-400" />
          {t('dashboard.courses.structure.activity.types.assignments')}
        </span>
      </div>

      {/* Basic info */}
      <div className="rounded-xl nice-shadow p-4 space-y-4">
        <Form.Field name="assignment-activity-title" className="space-y-1.5">
          <Form.Label className="text-sm font-medium text-gray-700">
            {t('dashboard.assignments.modals.create.form.title_label')}
          </Form.Label>
          <Form.Message match="valueMissing" className="text-xs text-red-500">
            {t('dashboard.assignments.modals.create.form.title_required')}
          </Form.Message>
          <Form.Control asChild>
            <input
              value={activityName}
              onChange={(e) => setActivityName(e.target.value)}
              type="text"
              required
              placeholder="例如：今日課堂 3 題練習"
              className={inputClass}
            />
          </Form.Control>
          <div className="space-y-2">
            <p className="text-[11px] font-semibold text-gray-500">
              不想命名？先用常用名稱，之後仍可修改。
            </p>
            <div className="flex flex-wrap gap-2">
              {quickTitleOptions.map((title) => (
                <button
                  key={title}
                  type="button"
                  onClick={() => setActivityName(title)}
                  className={`rounded-full border px-3 py-1 text-[11px] font-bold transition-colors ${
                    activityName === title
                      ? 'border-gray-900 bg-gray-900 text-white'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-900 hover:text-gray-900'
                  }`}
                >
                  {title}
                </button>
              ))}
            </div>
          </div>
        </Form.Field>

        <label className="space-y-1.5 block">
          <span className="text-sm font-medium text-gray-700">課題 / 單元</span>
          <input
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            type="text"
            placeholder="例如：分數比較、水循環、澳門世界文化遺產"
            className={inputClass}
          />
          <p className="text-[11px] font-semibold text-gray-500">
            建議填一個真實課題，AI、題庫或手動出題時都更容易貼近課堂。
          </p>
        </label>

        <Form.Field
          name="assignment-activity-description"
          className="space-y-1.5"
        >
          <Form.Label className="text-sm font-medium text-gray-700">
            {t('dashboard.assignments.modals.create.form.description_label')}
          </Form.Label>
          <Form.Control asChild>
            <textarea
              onChange={(e) => setActivityDescription(e.target.value)}
              rows={3}
              placeholder={t('dashboard.assignments.modals.create.form.description_placeholder')}
              className="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors resize-none"
            />
          </Form.Control>
        </Form.Field>

        <Form.Field
          name="assignment-activity-due-date"
          className="space-y-1.5"
        >
          <Form.Label className="text-sm font-medium text-gray-700">
            {t('dashboard.assignments.modals.create.form.due_date_label')}
          </Form.Label>
          <Form.Message match="valueMissing" className="text-xs text-red-500">
            {t('dashboard.assignments.modals.create.form.due_date_required')}
          </Form.Message>
          <Form.Control asChild>
            <input
              onChange={(e) => setDueDate(e.target.value)}
              type="date"
              required
              min={todayDate}
              value={dueDate}
              className={inputClass}
            />
          </Form.Control>
          <p className="text-[11px] font-semibold text-gray-500">
            已預設 7 天後截止，適合一節課或一週內完成的小練習。
          </p>
        </Form.Field>
      </div>

      <div className="rounded-xl nice-shadow p-4 space-y-3">
        <div className="flex items-center gap-2">
          <UsersRound size={16} className="text-gray-500" />
          <p className="text-sm font-medium text-gray-700">發布對象</p>
        </div>
        <div className="space-y-2">
          <p className="text-xs font-semibold text-gray-600">班級/群組</p>
          <div className="flex flex-wrap gap-2">
            {usergroupsQuery.isError && (
              <span className="text-xs text-rose-500">
                {(usergroupsQuery.error as Error)?.message || '讀取班級/群組失敗'}
              </span>
            )}
            {!usergroupsQuery.isError && usergroups.map((group: any) => (
              <button
                key={group.id}
                type="button"
                onClick={() => toggleUsergroup(Number(group.id))}
                className={`rounded-full px-3 py-1.5 text-xs font-bold border ${
                  targetUsergroupIds.includes(Number(group.id))
                    ? 'bg-gray-900 text-white border-gray-900'
                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                }`}
              >
                {group.name}
              </button>
            ))}
            {!usergroupsQuery.isError && usergroups.length === 0 && (
              <div className="w-full rounded-lg border border-amber-100 bg-amber-50 px-3 py-2">
                <p className="text-xs font-semibold text-amber-800">
                  未建立班級/群組時可先存草稿；正式試行前請先匯入學生並建立班級。
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Link
                    href={importStudentsHref}
                    className="inline-flex h-8 items-center rounded-lg bg-gray-900 px-3 text-xs font-bold text-white hover:bg-black"
                  >
                    批量匯入學生
                  </Link>
                  <Link
                    href={manageUsergroupsHref}
                    className="inline-flex h-8 items-center rounded-lg border border-amber-200 bg-white px-3 text-xs font-bold text-amber-800 hover:bg-amber-100"
                  >
                    管理班級
                  </Link>
                </div>
              </div>
            )}
          </div>
          {!usergroupsQuery.isError && usergroups.length > 0 && targetUsergroupIds.length === 0 && (
            <p className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
              發布前請先指定班級/群組；現在會先儲存為草稿，稍後仍可補上。
            </p>
          )}
        </div>
        <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] font-semibold text-emerald-800">
          簡單模式已啟用：只用選擇題、填空題和短問答；可用 AI、題庫或手動出題；學生提交後自動批改；答錯可看答案再重做，成績取最高分。
        </div>
      </div>

      <div className="rounded-xl nice-shadow p-4 space-y-3">
        <div className="flex items-center gap-2">
          <School size={16} className="text-gray-500" />
          <div>
            <p className="text-sm font-medium text-gray-700">AI 出題資料（可選）</p>
            <p className="text-[11px] font-semibold text-gray-500">填得越清楚，AI 生成的選擇、填空、短問答越貼近課堂。</p>
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <FieldInput label="科目" value={subject} onChange={setSubject} placeholder="例如：中文、數學、常識、Python" />
          <label className="space-y-1.5">
            <span className="text-xs font-semibold text-gray-600">學段</span>
            <select value={educationStage} onChange={(e) => setEducationStage(e.target.value)} className={inputClass}>
              <option value="">未設定</option>
              <option value="primary">小學</option>
              <option value="secondary">中學</option>
            </select>
          </label>
          <FieldInput label="年級" value={gradeLevel} onChange={setGradeLevel} placeholder="例如：小四 / 中一" />
          <FieldInput label="學年" value={schoolYear} onChange={setSchoolYear} placeholder="例如：2026-2027" />
          <FieldInput label="學期" value={term} onChange={setTerm} placeholder="例如：第一學期" />
          <FieldInput label="單元" value={unit} onChange={setUnit} placeholder="例如：分數比較 / 水循環" />
        </div>
        <label className="space-y-1.5 block">
          <span className="text-xs font-semibold text-gray-600">學習目標（可選，每行一項）</span>
          <textarea
            value={learningObjectives}
            onChange={(e) => setLearningObjectives(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors resize-none"
            placeholder={'學生能理解基本概念\n學生能完成一個小練習'}
          />
        </label>
      </div>

      <div className="flex justify-end">
        <Form.Submit asChild>
          <button
            type="submit"
            disabled={isSubmitting}
            className="inline-flex items-center justify-center h-9 px-5 text-sm font-medium text-white bg-black rounded-lg hover:bg-gray-800 transition-colors disabled:opacity-50"
          >
            {isSubmitting ? (
              <BarLoader
                cssOverride={{ borderRadius: 60 }}
                width={60}
                color="#ffffff"
              />
            ) : (
              '建立草稿，下一步出題'
            )}
          </button>
        </Form.Submit>
      </div>
    </Form.Root>
  )
}

function FieldInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <label className="space-y-1.5">
      <span className="text-xs font-semibold text-gray-600">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="w-full h-9 px-3 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors"
      />
    </label>
  )
}

export default NewAssignment
