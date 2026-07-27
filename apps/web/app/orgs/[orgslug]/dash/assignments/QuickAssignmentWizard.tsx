'use client'

import React from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  FileText,
  Loader2,
  Sparkles,
} from 'lucide-react'
import { queryKeys } from '@/lib/query/keys'
import { getUriWithOrg } from '@services/config/config'
import { createActivity, deleteActivity, updateActivity } from '@services/courses/activities'
import { createChapter } from '@services/courses/chapters'
import { getCourseMetadata, updateCourse } from '@services/courses/courses'
import {
  createAssignment,
  createAssignmentTask,
  deleteAssignment,
  generateAssignmentTasks,
  getAssignmentAiStatus,
  updateAssignment,
} from '@services/courses/assignments'
import {
  addQuestionBankItemToAssignment,
  getQuestionBankItems,
} from '@services/question-bank/question-bank'
import {
  SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET,
  SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES,
  SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT,
  SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS,
  getMissingSimplePilotGeneratedTaskTypes,
  getSimplePilotAssignmentTaskSetupIssue,
  getSimplePilotQuestionBankGradeSetupIssue,
  isCompleteSimplePilotGeneratedTaskSet,
  repairSimplePilotGeneratedTaskSet,
  simplePilotQuestionBankMetadataMatches,
} from '@lib/simple-pilot-assignments'

type QuickAssignmentWizardProps = {
  courses: any[]
  usergroups: any[]
  org: any
  accessToken: string
  onClose: () => void
}

type TaskMode = 'ai' | 'bank' | 'essay'

const AI_TEACHER_FALLBACK_MESSAGE = 'AI 暫時不可用，請先改用題庫或作文題；如需恢復 AI，請由管理員檢查 AI 設定。'

function responseErrorMessage(response: any, fallback: string) {
  const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  if (response instanceof Error && response.message) return response.message
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；')
  return fallback
}

function safeAiTeacherMessage(response?: any) {
  const raw = responseErrorMessage(response, AI_TEACHER_FALLBACK_MESSAGE)
  const sensitivePattern = /(api[_ -]?key|token|bearer|authorization|secret|traceback|stack|https?:\/\/|LEARNHOUSE_|OPENAI_|GEMINI_)/i
  if (!raw || sensitivePattern.test(raw)) return AI_TEACHER_FALLBACK_MESSAGE
  if (raw.includes('AI 出題尚未配置完整')) return AI_TEACHER_FALLBACK_MESSAGE
  return raw
}

function isAiFallbackError(error: any) {
  const message = responseErrorMessage(error, '')
  return message === AI_TEACHER_FALLBACK_MESSAGE || message.includes('AI 暫時不可用')
}

function dateInputValue(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function cleanUuid(value: string, prefix: string) {
  return String(value || '').replace(new RegExp(`^${prefix}_`), '')
}

function normalizeIds(value: any) {
  if (!Array.isArray(value)) return []
  const ids: number[] = []
  value.forEach((item) => {
    const id = Number(item)
    if (Number.isFinite(id) && !ids.includes(id)) ids.push(id)
  })
  return ids
}

function essayStarterTask(topic: string) {
  const prompt = topic.trim()
    ? `以「${topic.trim()}」為題，寫一篇短文。`
    : '以「一次難忘的校園活動」為題，寫一篇短文。'
  return {
    title: '作文題',
    description: '學生提交作文後，AI 會產生建議分數、分項評語和改善建議；老師仍可覆核確認。',
    hint: '先列提綱，再分段寫作；注意錯別字和標點。',
    reference_file: '',
    assignment_type: 'ESSAY',
    contents: {
      prompt,
      min_words: '150',
      max_words: '400',
      rubric: [
        { key: 'content', label: '內容切題', description: '回應題目，觀點清楚，例子合適。' },
        { key: 'structure', label: '結構組織', description: '開頭、段落、承接和結尾清晰。' },
        { key: 'language', label: '語言表達', description: '用詞、句式和語氣準確。' },
        { key: 'mechanics', label: '錯別字與標點', description: '錯別字、標點和基本語法。' },
        { key: 'creativity', label: '創意與思考', description: '有個人思考、細節和吸引力。' },
      ],
    },
    max_grade_value: 100,
  }
}

export default function QuickAssignmentWizard({
  courses,
  usergroups,
  org,
  accessToken,
  onClose,
}: QuickAssignmentWizardProps) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const todayDate = React.useMemo(() => dateInputValue(new Date()), [])
  const defaultDueDate = React.useMemo(() => {
    const date = new Date()
    date.setDate(date.getDate() + 7)
    return dateInputValue(date)
  }, [])
  const firstCourseUuid = courses[0]?.course_uuid || ''
  const [step, setStep] = React.useState(1)
  const [courseUuid, setCourseUuid] = React.useState(firstCourseUuid)
  const [topic, setTopic] = React.useState('')
  const [subject, setSubject] = React.useState('')
  const [gradeLevel, setGradeLevel] = React.useState('')
  const [unit, setUnit] = React.useState('')
  const [dueDate, setDueDate] = React.useState(defaultDueDate)
  const [taskMode, setTaskMode] = React.useState<TaskMode>('ai')
  const [bankSearch, setBankSearch] = React.useState('')
  const [selectedBankItemUuids, setSelectedBankItemUuids] = React.useState<string[]>([])
  const [targetUsergroupIds, setTargetUsergroupIds] = React.useState<number[]>([])
  const [isSubmitting, setIsSubmitting] = React.useState(false)

  React.useEffect(() => {
    if (targetUsergroupIds.length > 0 || usergroups.length !== 1) return
    const onlyGroupId = Number(usergroups[0]?.id)
    if (Number.isFinite(onlyGroupId)) setTargetUsergroupIds([onlyGroupId])
  }, [targetUsergroupIds.length, usergroups])

  const cleanCourseUuid = cleanUuid(courseUuid, 'course')
  const courseMetaQuery = useQuery({
    queryKey: ['course', cleanCourseUuid, 'meta', 'quickAssignmentWizard'],
    queryFn: () => getCourseMetadata(
      cleanCourseUuid,
      {},
      accessToken,
      { withUnpublishedActivities: true }
    ),
    enabled: Boolean(cleanCourseUuid && accessToken),
    staleTime: 60_000,
  })
  const courseStructure = courseMetaQuery.data as any
  const chapters = Array.isArray(courseStructure?.chapters) ? courseStructure.chapters : []

  const aiStatusQuery = useQuery({
    queryKey: queryKeys.ai.assignmentGenerationStatus(),
    queryFn: async () => {
      const response = await getAssignmentAiStatus(accessToken)
      if (response?.success === false) {
        throw new Error(responseErrorMessage(response, '檢查 AI 出題狀態失敗'))
      }
      return response?.data || response
    },
    enabled: Boolean(accessToken),
    staleTime: 60_000,
    retry: 1,
  })
  const aiReady = aiStatusQuery.data?.ready === true
  const aiUnavailable = (aiStatusQuery.data || aiStatusQuery.isError) && !aiReady

  const bankItemsQuery = useQuery({
    queryKey: queryKeys.questionBank.itemsSearch(
      org?.id || 0,
      JSON.stringify({ bankSearch, subject, gradeLevel, unit })
    ),
    queryFn: async () => {
      const response = await getQuestionBankItems({
        org_id: org.id,
        q: bankSearch,
      }, accessToken)
      if (response?.success === false) {
        throw new Error(responseErrorMessage(response, '讀取題庫失敗'))
      }
      return Array.isArray(response?.data) ? response.data : []
    },
    enabled: taskMode === 'bank' && Boolean(org?.id && accessToken),
    staleTime: 30_000,
  })
  const bankItems = Array.isArray(bankItemsQuery.data) ? bankItemsQuery.data : []
  const questionBankItemSetupIssue = React.useCallback((item: any) => (
    getSimplePilotAssignmentTaskSetupIssue(item)
    || getSimplePilotQuestionBankGradeSetupIssue(item, gradeLevel)
  ), [gradeLevel])
  const simpleBankItems = bankItems.filter((item: any) => (
    SIMPLE_PILOT_ASSIGNMENT_TASK_TYPE_SET.has(item.assignment_type)
    && simplePilotQuestionBankMetadataMatches(item, { subject, gradeLevel, unit })
    && !questionBankItemSetupIssue(item)
  ))
  const bankItemSetupIssues = bankItems
    .map((item: any) => questionBankItemSetupIssue(item))
    .filter(Boolean)
  const hiddenBankItemCount = Math.max(0, bankItemSetupIssues.length)
  const firstBankItemSetupIssue = bankItemSetupIssues[0] || ''

  const canUseBank = selectedBankItemUuids.length > 0
  const canContinueStep1 = Boolean(
    courseUuid
    && topic.trim()
    && dueDate
    && dueDate >= todayDate
  )
  const canContinueStep2 = (
    (taskMode === 'ai' && aiReady)
    || (taskMode === 'bank' && canUseBank)
    || taskMode === 'essay'
  )
  const canPublish = canContinueStep1 && canContinueStep2 && targetUsergroupIds.length > 0
  const createCourseHref = getUriWithOrg(org?.slug || '', '/dash/courses?new=true')
  const importStudentsHref = getUriWithOrg(org?.slug || '', '/dash/users/settings/add')

  function toggleUsergroup(groupId: number) {
    setTargetUsergroupIds((current) =>
      current.includes(groupId)
        ? current.filter((id) => id !== groupId)
        : [...current, groupId]
    )
  }

  function toggleBankItem(itemUuid: string) {
    setSelectedBankItemUuids((current) => {
      if (current.includes(itemUuid)) return current.filter((uuid) => uuid !== itemUuid)
      return [...current, itemUuid].slice(0, SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS)
    })
  }

  async function ensureChapter() {
    if (!courseStructure?.id) {
      throw new Error('課程資料尚未載入，請稍後再試。')
    }
    const firstChapter = chapters.find((chapter: any) => Number.isFinite(Number(chapter?.id)))
    if (firstChapter?.id) return firstChapter.id

    const chapter = await createChapter({
      name: '作業',
      description: '校內作業與短練習',
      thumbnail_image: '',
      course_id: courseStructure.id,
      org_id: org.id,
    }, accessToken)
    const chapterId = chapter?.id || chapter?.data?.id
    if (!chapterId) {
      throw new Error(responseErrorMessage(chapter, '建立作業章節失敗'))
    }
    return chapterId
  }

  function invalidateAssignmentData(assignmentUuid?: string) {
    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
    queryClient.invalidateQueries({ queryKey: queryKeys.courses.list(org?.slug || '') })
    if (cleanCourseUuid) {
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.meta(cleanCourseUuid) })
      queryClient.invalidateQueries({ queryKey: ['course', cleanCourseUuid, 'meta', 'withUnpublished'] })
      queryClient.invalidateQueries({ queryKey: ['course', cleanCourseUuid, 'meta', 'quickAssignmentWizard'] })
    }
    if (assignmentUuid) {
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.detail(assignmentUuid) })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.tasks(assignmentUuid) })
    }
    if (org?.id) {
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org.id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(org.id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.studentQueue(org.id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.questionBank.items(org.id) })
    }
  }

  async function createTasks(assignmentUuid: string) {
    if (taskMode === 'ai') {
      const generationContext = {
        assignment_uuid: assignmentUuid,
        difficulty: 'beginner',
        include_images: false,
        save_to_question_bank: true,
        question_bank_tags: [subject, gradeLevel, unit].map((tag) => String(tag || '').trim()).filter(Boolean),
        subject,
        assignment_title: topic,
        assignment_description: `3 題簡單練習：${topic}`,
        grade_level: gradeLevel,
        unit,
        learning_objectives: unit ? [unit] : [],
        language: 'zh',
      }
      const response = await generateAssignmentTasks({
        ...generationContext,
        prompt: `${subject ? `科目：${subject}。` : ''}${gradeLevel ? `年級：${gradeLevel}。` : ''}${unit ? `單元：${unit}。` : ''}課題：${topic}。生成 ${SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT} 題簡單作業，只包含選擇題、填空題和短問答；每題只問一個重點，答案要短且可自動批改。`,
        count: SIMPLE_PILOT_DEFAULT_AI_TASK_COUNT,
        question_types: SIMPLE_PILOT_ASSIGNMENT_TASK_TYPES,
      }, accessToken)
      if (response?.success === false) {
        throw new Error(safeAiTeacherMessage(response))
      }
      let tasks = Array.isArray(response?.data?.tasks) ? response.data.tasks : []
      if (tasks.length <= 0) {
        throw new Error(AI_TEACHER_FALLBACK_MESSAGE)
      }

      tasks = await repairSimplePilotGeneratedTaskSet(tasks, async (repairTypes) => {
        const repairResponse = await generateAssignmentTasks({
          ...generationContext,
          prompt: `${subject ? `科目：${subject}。` : ''}${gradeLevel ? `年級：${gradeLevel}。` : ''}${unit ? `單元：${unit}。` : ''}課題：${topic}。只補生成缺少的 ${repairTypes.join('、')} 題型；每題只問一個重點，答案要短且可自動批改。`,
          count: repairTypes.length,
          question_types: repairTypes,
        }, accessToken)
        if (repairResponse?.success === false) {
          throw new Error(safeAiTeacherMessage(repairResponse))
        }
        const repairTasks = Array.isArray(repairResponse?.data?.tasks)
          ? repairResponse.data.tasks
          : []
        return repairTasks
      })

      const finalMissingTypes = getMissingSimplePilotGeneratedTaskTypes(tasks)
      if (!isCompleteSimplePilotGeneratedTaskSet(tasks)) {
        throw new Error(
          `AI 出題不完整：需要選擇題、填空題和短問答各 1 題，本次仍缺少 ${finalMissingTypes.join('、') || '有效題目'}。系統未發布作業，請再試一次或改用題庫。`
        )
      }
      return
    }

    if (taskMode === 'bank') {
      const selectedSetupIssue = selectedBankItemUuids
        .map((itemUuid) => bankItems.find((item: any) => item.item_uuid === itemUuid))
        .map((item) => item ? questionBankItemSetupIssue(item) : '')
        .find(Boolean)
      if (selectedSetupIssue) {
        throw new Error(selectedSetupIssue)
      }
      for (const itemUuid of selectedBankItemUuids) {
        const response = await addQuestionBankItemToAssignment(itemUuid, assignmentUuid, accessToken)
        if (response?.success === false) {
          throw new Error(responseErrorMessage(response, '從題庫加入題目失敗'))
        }
      }
      return
    }

    const response = await createAssignmentTask(
      essayStarterTask(topic),
      assignmentUuid,
      accessToken
    )
    if (response?.success === false) {
      throw new Error(responseErrorMessage(response, '建立作文題失敗'))
    }
  }

  async function publishAssignment(assignmentUuid: string, activityUuid: string) {
    const assignmentResponse = await updateAssignment({ published: true }, assignmentUuid, accessToken)
    if (assignmentResponse?.success === false) {
      throw new Error(responseErrorMessage(assignmentResponse, '發布作業失敗'))
    }
    const activityResponse = await updateActivity({ published: true }, activityUuid, accessToken)
    if (!activityResponse || activityResponse?.success === false) {
      await updateAssignment({ published: false }, assignmentUuid, accessToken)
      throw new Error(responseErrorMessage(activityResponse, '發布活動失敗，作業已回復為草稿。'))
    }
    try {
      await updateCourse(courseStructure.course_uuid, { published: true }, accessToken)
    } catch (error) {
      await updateActivity({ published: false }, activityUuid, accessToken)
      await updateAssignment({ published: false }, assignmentUuid, accessToken)
      throw new Error(responseErrorMessage(error, '發布課程失敗，作業已回復為草稿。'))
    }
  }

  async function submitWizard() {
    if (isSubmitting) return
    if (!canPublish) {
      toast.error('請先完成三步資料，再發布給學生。')
      return
    }
    setIsSubmitting(true)
    const toastId = toast.loading('正在建立並發布作業')
    let activityUuid = ''
    let assignmentUuid = ''
    try {
      const chapterId = await ensureChapter()
      const activityResponse = await createActivity({
        name: topic.trim(),
        chapter_id: chapterId,
        activity_type: 'TYPE_ASSIGNMENT',
        activity_sub_type: 'SUBTYPE_ASSIGNMENT_ANY',
        published: false,
        course_id: courseStructure.id,
      }, chapterId, org.id, accessToken)
      if (!activityResponse?.id || !activityResponse?.activity_uuid) {
        throw new Error(responseErrorMessage(activityResponse, '建立作業活動失敗'))
      }
      activityUuid = activityResponse.activity_uuid

      const assignmentResponse = await createAssignment({
        title: topic.trim(),
        description: taskMode === 'essay'
          ? `作文作業：${topic.trim()}`
          : `3 題簡單練習：${topic.trim()}`,
        due_date: dueDate,
        grading_type: 'PERCENTAGE',
        auto_grading: true,
        anti_copy_paste: false,
        show_correct_answers: taskMode !== 'essay',
        allow_retries: true,
        max_retries: 0,
        subject,
        education_stage: '',
        grade_level: gradeLevel,
        school_year: '',
        term: '',
        unit,
        learning_objectives: unit ? [unit] : [],
        target_usergroup_ids: normalizeIds(targetUsergroupIds),
        score_policy: 'highest',
        teacher_review_required: taskMode === 'essay',
        teacher_review_status: taskMode === 'essay' ? 'pending' : 'not_required',
        course_id: courseStructure.id,
        org_id: org.id,
        chapter_id: chapterId,
        activity_id: activityResponse.id,
      }, accessToken)
      if (assignmentResponse?.success === false) {
        throw new Error(responseErrorMessage(assignmentResponse, '建立作業失敗'))
      }
      assignmentUuid = assignmentResponse?.data?.assignment_uuid
      if (!assignmentUuid) throw new Error('建立作業後未取得作業 ID。')

      await createTasks(assignmentUuid)
      await publishAssignment(assignmentUuid, activityUuid)
      invalidateAssignmentData(assignmentUuid)
      toast.success('作業已發布給學生')
      onClose()
      router.push(getUriWithOrg(org.slug, `/dash/assignments/${cleanUuid(assignmentUuid, 'assignment')}?subpage=submissions`))
    } catch (error) {
      if (assignmentUuid) {
        try {
          await deleteAssignment(assignmentUuid, accessToken)
        } catch {
          // Keep the original error visible.
        }
      }
      if (activityUuid) {
        try {
          await deleteActivity(activityUuid, accessToken)
        } catch {
          // Keep the original error visible.
        }
      }
      if (isAiFallbackError(error)) {
        setTaskMode('essay')
        setStep(2)
        toast.error(`${AI_TEACHER_FALLBACK_MESSAGE} 已切換到作文題，可檢查後重新發布。`)
      } else {
        toast.error(responseErrorMessage(error, '建立作業失敗，請稍後再試。'))
      }
    } finally {
      toast.dismiss(toastId)
      setIsSubmitting(false)
    }
  }

  if (!courses.length) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-4">
          <p className="text-sm font-black text-amber-900">還沒有課程</p>
          <p className="mt-1 text-sm text-amber-800">請先建立一個課程，之後就可以用 3 步建立作業。</p>
        </div>
        <Link
          href={createCourseHref}
          className="inline-flex h-10 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-bold text-white"
        >
          先建立課程
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-3 gap-2">
        {['基本資料', '題目來源', '發布班級'].map((label, index) => {
          const active = step === index + 1
          const done = step > index + 1
          return (
            <button
              key={label}
              type="button"
              onClick={() => setStep(index + 1)}
              className={`rounded-lg border px-3 py-2 text-left ${
                active
                  ? 'border-gray-950 bg-gray-950 text-white'
                  : done
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                    : 'border-gray-200 bg-white text-gray-600'
              }`}
            >
              <p className="text-[11px] font-black">第 {index + 1} 步</p>
              <p className="text-sm font-bold">{label}</p>
            </button>
          )
        })}
      </div>

      {step === 1 && (
        <div className="space-y-4">
          <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
            <p className="text-sm font-black text-gray-900">輸入課題和截止日期</p>
            <p className="mt-1 text-xs font-semibold text-gray-500">老師只要填必要資料，進階設定會自動套用校內試行預設。</p>
          </div>
          <label className="block space-y-1.5">
            <span className="text-xs font-bold text-gray-600">放在哪個課程</span>
            <select
              value={courseUuid}
              onChange={(event) => setCourseUuid(event.target.value)}
              className="h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
            >
              {courses.map((course) => (
                <option key={course.course_uuid} value={course.course_uuid}>
                  {course.name || '未命名課程'}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1.5">
            <span className="text-xs font-bold text-gray-600">課題 / 作業名稱</span>
            <input
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="例如：小四分數比較、澳門世界文化遺產、水循環"
              className="h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
            />
          </label>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="block space-y-1.5">
              <span className="text-xs font-bold text-gray-600">科目</span>
              <input
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                placeholder="數學"
                className="h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-bold text-gray-600">年級</span>
              <input
                value={gradeLevel}
                onChange={(event) => setGradeLevel(event.target.value)}
                placeholder="小四"
                className="h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-bold text-gray-600">截止日期</span>
              <input
                value={dueDate}
                min={todayDate}
                onChange={(event) => setDueDate(event.target.value)}
                type="date"
                className="h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
              />
            </label>
          </div>
          <label className="block space-y-1.5">
            <span className="text-xs font-bold text-gray-600">單元 / 學習重點（可選）</span>
            <input
              value={unit}
              onChange={(event) => setUnit(event.target.value)}
              placeholder="例如：同分母分數大小比較"
              className="h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm outline-none focus:border-gray-900"
            />
          </label>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <TaskModeCard
              active={taskMode === 'ai'}
              icon={<Sparkles size={18} />}
              title="AI 生成 3 題"
              detail="選擇、填空、短問答，自動儲存答案。"
              onClick={() => setTaskMode('ai')}
            />
            <TaskModeCard
              active={taskMode === 'bank'}
              icon={<BookOpen size={18} />}
              title="從題庫加入"
              detail="重用已審核題目，適合穩定校本題。"
              onClick={() => setTaskMode('bank')}
            />
            <TaskModeCard
              active={taskMode === 'essay'}
              icon={<FileText size={18} />}
              title="作文題"
              detail="AI 初評，老師最後覆核確認。"
              onClick={() => setTaskMode('essay')}
            />
          </div>

          {taskMode === 'ai' && (
            <div className={`rounded-xl border px-4 py-3 ${aiReady ? 'border-emerald-100 bg-emerald-50' : 'border-amber-100 bg-amber-50'}`}>
              <p className={`text-sm font-black ${aiReady ? 'text-emerald-900' : 'text-amber-900'}`}>
                {aiReady ? 'AI 出題已就緒' : 'AI 暫時不可用'}
              </p>
              <p className={`mt-1 text-xs font-semibold leading-relaxed ${aiReady ? 'text-emerald-800' : 'text-amber-800'}`}>
                {aiReady
                  ? '系統會生成 3 題簡單自動批改題，並保存到題庫。'
                  : safeAiTeacherMessage(aiStatusQuery.error || aiStatusQuery.data)}
              </p>
              {aiUnavailable && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setTaskMode('bank')}
                    className="inline-flex h-8 items-center rounded-lg bg-white px-3 text-xs font-black text-amber-900 ring-1 ring-amber-200"
                  >
                    改用題庫
                  </button>
                  <button
                    type="button"
                    onClick={() => setTaskMode('essay')}
                    className="inline-flex h-8 items-center rounded-lg bg-amber-900 px-3 text-xs font-black text-white"
                  >
                    改作文題
                  </button>
                </div>
              )}
            </div>
          )}

          {taskMode === 'bank' && (
            <div className="rounded-xl border border-gray-100 bg-white px-4 py-4">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-black text-gray-900">選擇題庫題目</p>
                  <p className="mt-1 text-xs text-gray-500">最多選 {SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題，只顯示可自動批改的簡單題。</p>
                </div>
                <input
                  value={bankSearch}
                  onChange={(event) => setBankSearch(event.target.value)}
                  placeholder="搜尋題庫"
                  className="h-9 rounded-lg border border-gray-200 px-3 text-sm outline-none focus:border-gray-900"
                />
              </div>
              <div className="mt-3 grid max-h-64 grid-cols-1 gap-2 overflow-y-auto md:grid-cols-2">
                {hiddenBankItemCount > 0 && (
                  <div className="col-span-full rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800">
                    已暫時隱藏 {hiddenBankItemCount} 題不適合直接發布的題目。{firstBankItemSetupIssue}
                  </div>
                )}
                {bankItemsQuery.isFetching && (
                  <div className="col-span-full flex items-center gap-2 rounded-lg border border-gray-100 px-3 py-3 text-sm text-gray-500">
                    <Loader2 size={15} className="animate-spin" />
                    載入題庫中
                  </div>
                )}
                {!bankItemsQuery.isFetching && simpleBankItems.length === 0 && (
                  <p className="col-span-full rounded-lg border border-gray-100 bg-gray-50 px-3 py-4 text-center text-sm font-semibold text-gray-500">
                    暫時沒有可直接使用的題庫題目，可改用 AI 或作文題。
                  </p>
                )}
                {simpleBankItems.slice(0, 12).map((item: any) => {
                  const active = selectedBankItemUuids.includes(item.item_uuid)
                  return (
                    <button
                      key={item.item_uuid}
                      type="button"
                      onClick={() => toggleBankItem(item.item_uuid)}
                      className={`rounded-lg border px-3 py-3 text-left ${
                        active
                          ? 'border-gray-950 bg-gray-950 text-white'
                          : 'border-gray-200 bg-white text-gray-800 hover:border-gray-900'
                      }`}
                    >
                      <p className="truncate text-sm font-black">{item.title}</p>
                      <p className={`mt-1 line-clamp-2 text-xs ${active ? 'text-white/70' : 'text-gray-500'}`}>
                        {item.description || item.contents?.prompt || item.assignment_type}
                      </p>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {taskMode === 'essay' && (
            <div className="rounded-xl border border-violet-100 bg-violet-50 px-4 py-3">
              <p className="text-sm font-black text-violet-900">作文會自動建立專業評分規準</p>
              <p className="mt-1 text-xs font-semibold leading-relaxed text-violet-800">
                學生提交後會有 AI 初評、分項評分、改善建議；老師仍可覆核和修改最終分數。
              </p>
            </div>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
            <p className="text-sm font-black text-gray-900">選班級並發布</p>
            <p className="mt-1 text-xs font-semibold text-gray-500">作業會立即發布到選中的班級；學生首頁會看到今日任務。</p>
          </div>
          {usergroups.length === 0 ? (
            <div className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-4">
              <p className="text-sm font-black text-amber-900">還沒有班級/群組</p>
              <p className="mt-1 text-sm text-amber-800">請先匯入學生並建立班級，再發布作業。</p>
              <Link
                href={importStudentsHref}
                className="mt-3 inline-flex h-9 items-center rounded-lg bg-white px-3 text-xs font-black text-amber-900 ring-1 ring-amber-200"
              >
                匯入學生
              </Link>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {usergroups.map((group: any) => {
                const groupId = Number(group.id)
                const active = targetUsergroupIds.includes(groupId)
                return (
                  <button
                    key={group.id}
                    type="button"
                    onClick={() => toggleUsergroup(groupId)}
                    className={`rounded-full border px-3 py-2 text-xs font-black ${
                      active
                        ? 'border-gray-950 bg-gray-950 text-white'
                        : 'border-gray-200 bg-white text-gray-700 hover:border-gray-950'
                    }`}
                  >
                    {group.name}
                    {Object.prototype.hasOwnProperty.call(group, 'member_count') && (
                      <span className={active ? 'text-white/70' : 'text-gray-400'}>
                        {' '}· {Number(group.member_count || 0)} 人
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
          <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3">
            <p className="flex items-center gap-2 text-sm font-black text-emerald-900">
              <CheckCircle2 size={16} />
              發布摘要
            </p>
            <p className="mt-1 text-xs font-semibold leading-relaxed text-emerald-800">
              「{topic || '未命名作業'}」會發布到 {targetUsergroupIds.length} 個班級，截止日期 {dueDate || '未設定'}。
              題目來源：{taskMode === 'ai' ? 'AI 生成 3 題簡單題' : taskMode === 'bank' ? `題庫 ${selectedBankItemUuids.length} 題` : '作文題'}。
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-col-reverse gap-2 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={onClose}
          className="h-10 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
        >
          取消
        </button>
        <div className="flex gap-2">
          {step > 1 && (
            <button
              type="button"
              onClick={() => setStep((value) => Math.max(1, value - 1))}
              className="h-10 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-50"
            >
              上一步
            </button>
          )}
          {step < 3 ? (
            <button
              type="button"
              onClick={() => setStep((value) => Math.min(3, value + 1))}
              disabled={step === 1 ? !canContinueStep1 : !canContinueStep2}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              下一步
              <ArrowRight size={15} />
            </button>
          ) : (
            <button
              type="button"
              onClick={submitWizard}
              disabled={!canPublish || isSubmitting || courseMetaQuery.isLoading}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-gray-950 px-4 text-sm font-bold text-white hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isSubmitting ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
              發布給學生
            </button>
          )}
        </div>
      </div>

      {courseMetaQuery.isError && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-800">
          <AlertCircle size={14} className="mt-0.5 flex-none" />
          {(courseMetaQuery.error as Error)?.message || '課程資料載入失敗，請重新打開視窗再試。'}
        </div>
      )}
    </div>
  )
}

function TaskModeCard({
  active,
  icon,
  title,
  detail,
  onClick,
}: {
  active: boolean
  icon: React.ReactNode
  title: string
  detail: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-xl border px-4 py-4 text-left transition-colors ${
        active
          ? 'border-gray-950 bg-gray-950 text-white'
          : 'border-gray-200 bg-white text-gray-800 hover:border-gray-900'
      }`}
    >
      <span className={`inline-flex h-9 w-9 items-center justify-center rounded-lg ${active ? 'bg-white/15' : 'bg-gray-100'}`}>
        {icon}
      </span>
      <p className="mt-3 text-sm font-black">{title}</p>
      <p className={`mt-1 text-xs leading-relaxed ${active ? 'text-white/70' : 'text-gray-500'}`}>{detail}</p>
    </button>
  )
}
