'use client'
import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext'
import { useAssignmentTaskSubmissions } from '@components/Contexts/Assignments/AssignmentSubmissionContext'
import {
  useAssignmentsTask,
  useAssignmentsTaskDispatch,
} from '@components/Contexts/Assignments/AssignmentsTaskContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI'
import {
  getAssignmentTask,
  getAssignmentTaskSubmissionsUser,
  handleAssignmentTaskSubmission,
  updateAssignmentTask,
} from '@services/courses/assignments'
import { getAPIUrl } from '@services/config/config'
import {
  PLAYGROUND_LANGUAGES,
  getLanguageById,
  getLanguageOptionState,
} from '@components/Objects/Editor/Extensions/CodePlayground/languages'
import LivePreview from '@components/Objects/Editor/Extensions/CodePlayground/LivePreview'
import {
  Plus,
  Minus,
  Play,
  Loader2,
  CheckCircle2,
  XCircle,
  Eye,
  EyeOff,
  ChevronDown,
  ChevronRight,
  ShieldCheck,
  Settings2,
} from 'lucide-react'
import React, { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { v4 as uuidv4 } from 'uuid'
import { useTranslation } from 'react-i18next'
import dynamic from 'next/dynamic'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'

const CodeMirror = dynamic(() => import('@uiw/react-codemirror'), {
  ssr: false,
  loading: () => <div className="h-[200px] bg-neutral-900 animate-pulse rounded-md" />,
})

async function getTheme() {
  const { tokyoNight } = await import('@uiw/codemirror-theme-tokyo-night')
  return tokyoNight
}

async function getLanguageExtension(codemirrorLang: string) {
  switch (codemirrorLang) {
    case 'python': { const { python } = await import('@codemirror/lang-python'); return python() }
    case 'javascript': { const { javascript } = await import('@codemirror/lang-javascript'); return javascript() }
    case 'java': { const { java } = await import('@codemirror/lang-java'); return java() }
    case 'cpp': { const { cpp } = await import('@codemirror/lang-cpp'); return cpp() }
    case 'rust': { const { rust } = await import('@codemirror/lang-rust'); return rust() }
    case 'go': { const { go } = await import('@codemirror/lang-go'); return go() }
    case 'php': { const { php } = await import('@codemirror/lang-php'); return php() }
    case 'sql': { const { sql } = await import('@codemirror/lang-sql'); return sql() }
    // Legacy modes can bring a second @codemirror/language copy through a
    // transitive theme dependency. The runtime parser contract is identical,
    // but TypeScript sees the private `StringStream` types as unrelated.
    // Cast at this boundary so Docker's clean, frozen install type-checks too.
    case 'perl': { const { StreamLanguage } = await import('@codemirror/language'); const { perl } = await import('@codemirror/legacy-modes/mode/perl'); return StreamLanguage.define(perl as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'r': { const { StreamLanguage } = await import('@codemirror/language'); const { r } = await import('@codemirror/legacy-modes/mode/r'); return StreamLanguage.define(r as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'haskell': { const { StreamLanguage } = await import('@codemirror/language'); const { haskell } = await import('@codemirror/legacy-modes/mode/haskell'); return StreamLanguage.define(haskell as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'lua': { const { StreamLanguage } = await import('@codemirror/language'); const { lua } = await import('@codemirror/legacy-modes/mode/lua'); return StreamLanguage.define(lua as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'clojure': { const { StreamLanguage } = await import('@codemirror/language'); const { clojure } = await import('@codemirror/legacy-modes/mode/clojure'); return StreamLanguage.define(clojure as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'shell': { const { StreamLanguage } = await import('@codemirror/language'); const { shell } = await import('@codemirror/legacy-modes/mode/shell'); return StreamLanguage.define(shell as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'pascal': { const { StreamLanguage } = await import('@codemirror/language'); const { pascal } = await import('@codemirror/legacy-modes/mode/pascal'); return StreamLanguage.define(pascal as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'fortran': { const { StreamLanguage } = await import('@codemirror/language'); const { fortran } = await import('@codemirror/legacy-modes/mode/fortran'); return StreamLanguage.define(fortran as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    case 'powershell': { const { StreamLanguage } = await import('@codemirror/language'); const { powerShell } = await import('@codemirror/legacy-modes/mode/powershell'); return StreamLanguage.define(powerShell as unknown as Parameters<typeof StreamLanguage.define>[0]) }
    default: { const { javascript } = await import('@codemirror/lang-javascript'); return javascript() }
  }
}

type CodeTestCase = {
  id: string
  label: string
  stdin: string
  expectedStdout: string
  hidden: boolean
  weight: number
}

type WebCheck = {
  id: string
  label: string
  target: 'html' | 'css' | 'js'
  match: 'contains' | 'regex'
  pattern: string
  hidden: boolean
  weight: number
}

type CodeTaskContents = {
  mode?: 'judge0' | 'web_preview'
  language_id: number
  starter_code: string
  solution_code: string
  grading_mode: 'equal_weight' | 'binary' | 'custom_weights'
  test_cases: CodeTestCase[]
  starter_html?: string
  starter_css?: string
  starter_js?: string
  solution_html?: string
  solution_css?: string
  solution_js?: string
  web_checks?: WebCheck[]
  // Student-facing behavior. All default to the classic behavior so existing
  // code tasks keep working unchanged after this upgrade.
  allow_student_run?: boolean             // student can click Run Tests (default true)
  show_test_details_on_fail?: boolean     // reveal Expected/Got on a failed test (default true)
  show_hidden_test_count?: boolean        // show "N hidden tests" badge (default true)
  hidden_test_count?: number              // server-owned count; student payload only
  require_passing_to_submit?: boolean     // student must pass visible tests to save (default false)
}

type CodeTestResult = {
  id: string
  label: string
  passed: boolean
  actual_stdout: string | null
  expected_stdout: string | null
  stderr: string | null
  compile_output: string | null
  status: { id: number; description: string } | null
  time: string | null
  memory: number | null
}

type TaskCodeObjectProps = {
  view: 'teacher' | 'student' | 'grading'
  assignmentTaskUUID?: string
  user_id?: string
}

const cmStyles: React.CSSProperties = {
  fontSize: '14px',
  fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', Menlo, monospace",
}
const cmClassName = [
  '[&_.cm-editor]:!bg-[#1a1b26]',
  '[&_.cm-gutters]:!bg-[#1a1b26]',
  '[&_.cm-gutters]:!border-r-transparent',
  '[&_.cm-activeLineGutter]:!bg-[#24283b]',
  '[&_.cm-activeLine]:!bg-[#24283b]',
  '[&_.cm-editor]:!outline-none',
  '[&_.cm-focused]:!outline-none',
  '[&_.cm-scroller]:!overflow-auto',
  '[&_.cm-line]:!px-4',
].join(' ')

const DEFAULT_CONTENTS: CodeTaskContents = {
  mode: 'judge0',
  language_id: 71,
  starter_code: '# Write your code here\n',
  solution_code: '',
  grading_mode: 'equal_weight',
  test_cases: [
    { id: 'tc_' + uuidv4(), label: 'Test 1', stdin: '', expectedStdout: '', hidden: false, weight: 1 },
  ],
  allow_student_run: true,
  show_test_details_on_fail: true,
  show_hidden_test_count: true,
  require_passing_to_submit: false,
}

// Helper: merge a contents blob from the API with the student-behavior
// defaults so missing fields fall back to the pre-upgrade behavior.
function normalizeCodeContents(raw: any, includePrivateAuthoringFields = true): CodeTaskContents {
  const parsedHiddenCount = Number(raw?.hidden_test_count)
  return {
    mode: raw?.mode ?? 'judge0',
    language_id: raw?.language_id ?? 71,
    starter_code: raw?.starter_code ?? '',
    solution_code: includePrivateAuthoringFields ? raw?.solution_code ?? '' : '',
    grading_mode: raw?.grading_mode ?? 'equal_weight',
    test_cases: raw?.test_cases ?? [],
    starter_html: raw?.starter_html ?? '',
    starter_css: raw?.starter_css ?? '',
    starter_js: raw?.starter_js ?? '',
    solution_html: includePrivateAuthoringFields ? raw?.solution_html ?? '' : '',
    solution_css: includePrivateAuthoringFields ? raw?.solution_css ?? '' : '',
    solution_js: includePrivateAuthoringFields ? raw?.solution_js ?? '' : '',
    web_checks: raw?.web_checks ?? [],
    allow_student_run: raw?.allow_student_run ?? true,
    show_test_details_on_fail: raw?.show_test_details_on_fail ?? true,
    show_hidden_test_count: raw?.show_hidden_test_count ?? true,
    hidden_test_count: includePrivateAuthoringFields
      ? undefined
      : Number.isInteger(parsedHiddenCount) && parsedHiddenCount >= 0
        ? parsedHiddenCount
        : 0,
    require_passing_to_submit: raw?.require_passing_to_submit ?? false,
  }
}

function webSubmissionKey(html: string, css: string, js: string) {
  return JSON.stringify([html, css, js])
}

function runWebPreviewChecks(
  contents: CodeTaskContents,
  html: string,
  css: string,
  js: string,
  includeHidden = false
): CodeTestResult[] {
  return (contents.web_checks || []).filter((check) => includeHidden || !check.hidden).map((check) => {
    const source = check.target === 'html' ? html : check.target === 'css' ? css : js
    let passed = false
    if (check.match === 'regex') {
      try {
        passed = new RegExp(check.pattern, 'im').test(source)
      } catch {
        passed = false
      }
    } else {
      passed = source.toLowerCase().includes(check.pattern.toLowerCase())
    }
    return {
      id: check.id,
      label: check.label,
      passed,
      actual_stdout: passed ? 'matched' : 'not matched',
      expected_stdout: `${check.target} ${check.match} ${check.pattern}`,
      stderr: null,
      compile_output: null,
      status: { id: passed ? 3 : 4, description: passed ? '通過' : '未通過' },
      time: null,
      memory: null,
    }
  })
}

function TaskCodeObject({ view, assignmentTaskUUID, user_id }: TaskCodeObjectProps) {
  const { t } = useTranslation()
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const assignmentTaskState = useAssignmentsTask() as any
  const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
  const assignment = useAssignments() as any
  const assignmentUuid = assignment?.assignment_object?.assignment_uuid
  const taskSubmissionsMap = useAssignmentTaskSubmissions()
  const queryClient = useQueryClient()

  // Editor state
  const [contents, setContents] = useState<CodeTaskContents>(() => ({
    ...DEFAULT_CONTENTS,
    test_cases: DEFAULT_CONTENTS.test_cases.map((testCase, index) => ({
      ...testCase,
      label: t('dashboard.assignments.editor.task_editor.code.test_default_label', {
        count: index + 1,
      }),
    })),
  }))
  const [code, setCode] = useState('')
  const [htmlCode, setHtmlCode] = useState('')
  const [cssCode, setCssCode] = useState('')
  const [jsCode, setJsCode] = useState('')
  const [showSavingDisclaimer, setShowSavingDisclaimer] = useState(false)
  const selectedLang = getLanguageById(contents.language_id)
  const runtimeUnavailable = contents.mode !== 'web_preview' && getLanguageOptionState(
    contents.language_id,
    { previewSupported: false, apiAdaptersSupported: false }
  ).disabled
  const runtimeWarningId = `assignment-code-runtime-warning-${assignmentTaskUUID || view}`

  // CodeMirror extensions
  const [cmExtensions, setCmExtensions] = useState<any[]>([])
  const [cmTheme, setCmTheme] = useState<any>(null)

  // Execution state
  const [isRunning, setIsRunning] = useState(false)
  const [results, setResults] = useState<CodeTestResult[]>([])
  const [showResults, setShowResults] = useState(false)
  const [lastRunSourceKey, setLastRunSourceKey] = useState<string | null>(null)

  // Submission state (student/grading)
  const [userSubmissions, setUserSubmissions] = useState<any>(null)
  const [initialCode, setInitialCode] = useState('')
  const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null)

  // Teacher UI state
  const [showSolution, setShowSolution] = useState(false)

  // Task data from API (student/grading views)
  const [assignmentTaskOutsideProvider, setAssignmentTaskOutsideProvider] = useState<any>(null)

  // Load CodeMirror theme + language
  useEffect(() => {
    const lang = getLanguageById(contents.language_id)
    if (!lang) return
    Promise.all([getLanguageExtension(lang.codemirrorLang), getTheme()]).then(
      ([langExt, theme]) => {
        setCmExtensions([langExt])
        setCmTheme(theme)
      }
    )
  }, [contents.language_id])

  // Anti-copy-paste: if the assignment has anti_copy_paste enabled, inject a
  // CodeMirror extension that blocks paste events and shows a toast. Only
  // applied in the student view — teachers and graders can paste freely.
  const antiPasteEnabled =
    view === 'student' && !!assignment?.assignment_object?.anti_copy_paste

  const [pasteBlockerExt, setPasteBlockerExt] = useState<any[]>([])
  useEffect(() => {
    if (!antiPasteEnabled) {
      setPasteBlockerExt([])
      return
    }
    // Dynamic import keeps the view module out of the bundle unless needed
    import('@codemirror/view').then(({ EditorView }) => {
      setPasteBlockerExt([
        EditorView.domEventHandlers({
          paste: (event: ClipboardEvent) => {
            event.preventDefault()
            event.stopPropagation()
            toast.error(t('dashboard.assignments.editor.task_editor.general.paste_blocked'))
            return true
          },
        }),
      ])
    })
  }, [antiPasteEnabled, t])

  const studentCmExtensions = antiPasteEnabled
    ? [...cmExtensions, ...pasteBlockerExt]
    : cmExtensions

  // --- TEACHER VIEW ---
  useEffect(() => {
    if (view === 'teacher' && assignmentTaskState?.assignmentTask?.contents) {
      const c = assignmentTaskState.assignmentTask.contents
      if (c.language_id !== undefined) {
        setContents(normalizeCodeContents(c))
        setCode(c.starter_code ?? '')
        setHtmlCode(c.starter_html ?? '')
        setCssCode(c.starter_css ?? '')
        setJsCode(c.starter_js ?? '')
      }
    }
  }, [view, assignmentTaskState])

  // --- STUDENT VIEW ---
  // Task data and the user's submission are fetched once at the assignment
  // level (AssignmentContext.assignment_tasks + AssignmentTaskSubmissionsContext).
  // Looking them up here avoids N per-task /assignments/task and
  // /submissions/me round trips on activity load.

  // --- GRADING VIEW ---
  const getAssignmentTaskUI = useCallback(async () => {
    if (assignmentTaskUUID) {
      const res = await getAssignmentTask(assignmentTaskUUID, access_token)
      if (res.success) {
        setAssignmentTaskOutsideProvider(res.data)
        const c = res.data.contents
        if (c) {
          const normalized = normalizeCodeContents(c)
          setContents(normalized)
          setCode(c.starter_code ?? '')
          setInitialCode(
            normalized.mode === 'web_preview'
              ? webSubmissionKey(c.starter_html ?? '', c.starter_css ?? '', c.starter_js ?? '')
              : c.starter_code ?? ''
          )
          setHtmlCode(c.starter_html ?? '')
          setCssCode(c.starter_css ?? '')
          setJsCode(c.starter_js ?? '')
        }
      }
    }
  }, [access_token, assignmentTaskUUID])

  const getAssignmentTaskSubmissionFromIdentifiedUserUI = useCallback(async () => {
    if (assignmentTaskUUID && user_id) {
      const res = await getAssignmentTaskSubmissionsUser(
        assignmentTaskUUID,
        user_id,
        assignmentUuid,
        access_token
      )
      if (res.success && res.data) {
        setUserSubmissions(res.data)
        setUserSubmissionObject(res.data)
        if (res.data.task_submission?.source_code) {
          setCode(res.data.task_submission.source_code)
          setInitialCode(res.data.task_submission.source_code)
        }
        if (res.data.task_submission?.mode === 'web_preview') {
          const submittedHtml = res.data.task_submission.html_code ?? ''
          const submittedCss = res.data.task_submission.css_code ?? ''
          const submittedJs = res.data.task_submission.js_code ?? ''
          setHtmlCode(submittedHtml)
          setCssCode(submittedCss)
          setJsCode(submittedJs)
          setInitialCode(webSubmissionKey(submittedHtml, submittedCss, submittedJs))
        }
      }
    }
  }, [access_token, assignmentTaskUUID, assignmentUuid, user_id])

  // Hydrate student view from already-fetched assignment context + batch
  // submissions map. Re-runs when either context payload arrives.
  useEffect(() => {
    if (view !== 'student' || !assignmentTaskUUID) return
    const task = assignment?.assignment_tasks?.find(
      (t: any) => t.assignment_task_uuid === assignmentTaskUUID
    )
    if (task) {
      setAssignmentTaskOutsideProvider(task)
      const c = task.contents
      if (c) {
        const normalized = normalizeCodeContents(c, false)
        setContents(normalized)
        setCode(c.starter_code ?? '')
        setInitialCode(
          normalized.mode === 'web_preview'
            ? webSubmissionKey(c.starter_html ?? '', c.starter_css ?? '', c.starter_js ?? '')
            : c.starter_code ?? ''
        )
        setHtmlCode(c.starter_html ?? '')
        setCssCode(c.starter_css ?? '')
        setJsCode(c.starter_js ?? '')
      }
    }
    const sub = taskSubmissionsMap?.[assignmentTaskUUID] ?? null
    if (sub) {
      setUserSubmissions(sub)
      if (sub.task_submission?.source_code) {
        setCode(sub.task_submission.source_code)
        setInitialCode(sub.task_submission.source_code)
      }
      if (sub.task_submission?.mode === 'web_preview') {
        const submittedHtml = sub.task_submission.html_code ?? ''
        const submittedCss = sub.task_submission.css_code ?? ''
        const submittedJs = sub.task_submission.js_code ?? ''
        setHtmlCode(submittedHtml)
        setCssCode(submittedCss)
        setJsCode(submittedJs)
        setInitialCode(webSubmissionKey(submittedHtml, submittedCss, submittedJs))
      }
    }
  }, [view, assignmentTaskUUID, assignment?.assignment_tasks, taskSubmissionsMap])

  // Grading view still uses per-task fetches — there's only ever one task
  // open at a time in the grading modal so the N+1 cost doesn't apply.
  useEffect(() => {
    if (view === 'grading') {
      getAssignmentTaskUI()
      getAssignmentTaskSubmissionFromIdentifiedUserUI()
    }
  }, [getAssignmentTaskSubmissionFromIdentifiedUserUI, getAssignmentTaskUI, view])

  // Track changes for save disclaimer
  useEffect(() => {
    if (view === 'student') {
      const webChanged = contents.mode === 'web_preview'
        && webSubmissionKey(htmlCode, cssCode, jsCode) !== initialCode
      setShowSavingDisclaimer(contents.mode === 'web_preview' ? webChanged : code !== initialCode)
    }
  }, [code, htmlCode, cssCode, jsCode, initialCode, contents.mode, view])

  // --- SAVE (teacher) ---
  async function saveFC() {
    if (!assignmentTaskState?.assignmentTask?.assignment_task_uuid) return
    const updatedContents: CodeTaskContents = {
      ...contents,
      starter_code: code,
      starter_html: htmlCode,
      starter_css: cssCode,
      starter_js: jsCode,
    }
    const values = { contents: updatedContents }
    const res = await updateAssignmentTask(
      values,
      assignmentTaskState.assignmentTask.assignment_task_uuid,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success) {
      assignmentTaskStateHook({ type: 'reload' })
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() })
      toast.success(t('dashboard.assignments.editor.toasts.task_updated'))
    } else {
      toast.error(t('dashboard.assignments.editor.toasts.task_update_error'))
    }
  }

  // Student-gating helpers: derived from the student-behavior flags.
  // `visibleResults` is the subset of run results that are NOT hidden test
  // cases — those are the only ones the student can see and reason about.
  const visibleResults = results.filter((r) => {
    const tc = contents.test_cases.find((t) => t.id === r.id)
    return !tc?.hidden
  })
  const currentSourceKey = contents.mode === 'web_preview'
    ? webSubmissionKey(htmlCode, cssCode, jsCode)
    : code
  const currentVisibleResults = contents.mode === 'web_preview'
    ? runWebPreviewChecks(contents, htmlCode, cssCode, jsCode)
    : visibleResults
  const visibleCheckCount = contents.mode === 'web_preview'
    ? (contents.web_checks || []).filter((check) => !check.hidden).length
    : contents.test_cases.filter((testCase) => !testCase.hidden).length
  const allVisiblePassing = visibleCheckCount === 0 || (
    currentVisibleResults.length === visibleCheckCount &&
    currentVisibleResults.every((r) => r.passed) &&
    (contents.mode === 'web_preview' || lastRunSourceKey === currentSourceKey)
  )
  // Only enforce the "must pass" gate when the teacher turned it on.
  const submissionGatedByPassing = contents.require_passing_to_submit === true
  const submissionBlocked = submissionGatedByPassing && !allVisiblePassing

  useEffect(() => {
    if (lastRunSourceKey !== null && lastRunSourceKey !== currentSourceKey) {
      setResults([])
      setShowResults(false)
      setLastRunSourceKey(null)
    }
  }, [currentSourceKey, lastRunSourceKey])

  // --- SUBMIT (student) ---
  async function submitFC() {
    if (!assignmentTaskUUID) return
    if (runtimeUnavailable) {
      toast.error(t('dashboard.assignments.editor.task_editor.code.unsupported_runtime_student', {
        language: selectedLang?.name || `ID ${contents.language_id}`,
      }))
      return
    }
    // Enforce "must pass all visible tests" gate if the teacher enabled it.
    if (submissionBlocked) {
      toast.error(t('dashboard.assignments.editor.task_editor.code.must_pass_toast'))
      return
    }
    if (contents.mode === 'web_preview') {
      const webResults = runWebPreviewChecks(contents, htmlCode, cssCode, jsCode)
      setResults(webResults)
      setLastRunSourceKey(webSubmissionKey(htmlCode, cssCode, jsCode))
      setShowResults(true)
      const values = {
        assignment_task_submission_uuid: userSubmissions?.assignment_task_submission_uuid || null,
        task_submission: {
          mode: 'web_preview',
          html_code: htmlCode,
          css_code: cssCode,
          js_code: jsCode,
        },
        grade: 0,
        task_submission_grade_feedback: '',
      }
      const res = await handleAssignmentTaskSubmission(
        values,
        assignmentTaskUUID,
        assignment.assignment_object.assignment_uuid,
        access_token
      )
      if (res.success) {
        setUserSubmissions(res.data)
        setInitialCode(webSubmissionKey(htmlCode, cssCode, jsCode))
        setShowSavingDisclaimer(false)
        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid) })
        toast.success(t('assignments.task_answer_saved_not_submitted'))
      } else {
        toast.error(t('dashboard.assignments.editor.toasts.task_save_error'))
      }
      return
    }
    const values = {
      assignment_task_submission_uuid: userSubmissions?.assignment_task_submission_uuid || null,
      task_submission: {
        source_code: code,
        language_id: contents.language_id,
      },
      grade: 0,
      task_submission_grade_feedback: '',
    }
    const res = await handleAssignmentTaskSubmission(
      values,
      assignmentTaskUUID,
      assignment.assignment_object.assignment_uuid,
      access_token
    )
    if (res.success) {
      setUserSubmissions(res.data)
      setInitialCode(code)
      setShowSavingDisclaimer(false)
      // Refresh the batch task-submissions cache so peer Task*Objects (and
      // a future re-mount of this one) see the new submission state.
      queryClient.invalidateQueries({ queryKey: queryKeys.assignments.taskSubmission(assignment.assignment_object.assignment_uuid) })
      toast.success(t('assignments.task_answer_saved_not_submitted'))
    } else {
      toast.error(t('dashboard.assignments.editor.toasts.task_save_error'))
    }
  }

  // --- RUN CODE ---
  async function runCode() {
    if (isRunning) return
    if (contents.mode === 'web_preview') {
      const webResults = runWebPreviewChecks(contents, htmlCode, cssCode, jsCode)
      setResults(webResults)
      setShowResults(true)
      return
    }
    if (runtimeUnavailable) {
      toast.error(t('dashboard.assignments.editor.task_editor.code.unsupported_runtime'))
      return
    }
    if (contents.test_cases.length === 0) {
      toast.error(t('dashboard.assignments.editor.task_editor.code.no_tests_error'))
      return
    }
    setIsRunning(true)
    setShowResults(true)
    try {
      // Always run ALL test cases — hidden ones just have details masked in the UI
      const resp = await fetch(`${getAPIUrl()}code/execute-batch`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${access_token}`,
        },
        body: JSON.stringify({
          language_id: contents.language_id,
          source_code: code,
          test_cases: contents.test_cases.map((tc) => ({
            id: tc.id,
            label: tc.label,
            stdin: tc.stdin,
            expected_stdout: tc.expectedStdout,
          })),
        }),
      })

      if (!resp.ok) {
        toast.error(t('dashboard.assignments.editor.task_editor.code.execution_error'))
        return
      }

      const data = await resp.json()
      const newResults: CodeTestResult[] = data.results.map((r: any) => ({
        id: r.id,
        label: r.label,
        passed: r.passed,
        actual_stdout: r.actual_stdout,
        expected_stdout: r.expected_stdout,
        stderr: r.stderr,
        compile_output: r.compile_output,
        status: r.status,
        time: r.time,
        memory: r.memory,
      }))
      setResults(newResults)
      setLastRunSourceKey(code)
    } catch {
      toast.error(t('dashboard.assignments.editor.task_editor.code.execution_error'))
    } finally {
      setIsRunning(false)
    }
  }

  // --- GRADE (grading view) ---
  async function gradeFC() {
    if (!assignmentTaskUUID || !userSubmissions) return
    if (contents.mode === 'web_preview') {
      const webSubmission = userSubmissions.task_submission || {}
      const submittedHtml = webSubmission.html_code ?? htmlCode
      const submittedCss = webSubmission.css_code ?? cssCode
      const submittedJs = webSubmission.js_code ?? jsCode
      const webResults = runWebPreviewChecks(contents, submittedHtml, submittedCss, submittedJs, true)
      setResults(webResults)
      setShowResults(true)

      const values = {
        assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid,
        task_submission: {
          mode: 'web_preview',
          html_code: submittedHtml,
          css_code: submittedCss,
          js_code: submittedJs,
        },
      }
      const res = await handleAssignmentTaskSubmission(
        values,
        assignmentTaskUUID,
        assignment.assignment_object.assignment_uuid,
        access_token
      )
      if (res.success) {
        setUserSubmissions(res.data)
        setUserSubmissionObject(res.data)
        getAssignmentTaskSubmissionFromIdentifiedUserUI()
        toast.success(`已由伺服器批改：${res.data.grade}/${assignmentTaskOutsideProvider?.max_grade_value || 100} 分`)
      } else {
        toast.error('批改題目失敗')
      }
      return
    }
    if (runtimeUnavailable) {
      toast.error(t('dashboard.assignments.editor.task_editor.code.unsupported_runtime'))
      return
    }
    setIsRunning(true)
    try {
      const resp = await fetch(`${getAPIUrl()}code/execute-batch`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${access_token}`,
        },
        body: JSON.stringify({
          language_id: contents.language_id,
          source_code: code,
          test_cases: contents.test_cases.map((tc) => ({
            id: tc.id,
            label: tc.label,
            stdin: tc.stdin,
            expected_stdout: tc.expectedStdout,
          })),
        }),
      })

      if (!resp.ok) {
        toast.error('批改時執行程式失敗')
        return
      }

      const data = await resp.json()
      const gradeResults: CodeTestResult[] = data.results
      setResults(gradeResults)
      setShowResults(true)

      const maxPoints = assignmentTaskOutsideProvider?.max_grade_value || 100
      const passedCount = gradeResults.filter((r: any) => r.passed).length
      const totalCount = gradeResults.length
      let finalGrade: number

      if (contents.grading_mode === 'binary') {
        finalGrade = passedCount === totalCount ? maxPoints : 0
      } else if (contents.grading_mode === 'custom_weights') {
        const totalWeight = contents.test_cases.reduce((s, tc) => s + tc.weight, 0)
        const passedWeight = contents.test_cases.reduce((s, tc) => {
          const result = gradeResults.find((r: any) => r.id === tc.id)
          return s + (result?.passed ? tc.weight : 0)
        }, 0)
        finalGrade = totalWeight > 0 ? Math.round((passedWeight / totalWeight) * maxPoints) : 0
      } else {
        finalGrade = totalCount > 0 ? Math.round((passedCount / totalCount) * maxPoints) : 0
      }

      const feedback = `自動批改：通過 ${passedCount}/${totalCount} 個測試，得分 ${finalGrade}/${maxPoints}`

      const values = {
        assignment_task_submission_uuid: userSubmissions.assignment_task_submission_uuid,
        task_submission: {
          source_code: code,
          language_id: contents.language_id,
        },
        grade: finalGrade,
        task_submission_grade_feedback: feedback,
      }

      const res = await handleAssignmentTaskSubmission(
        values,
        assignmentTaskUUID,
        assignment.assignment_object.assignment_uuid,
        access_token
      )
      if (res.success) {
        getAssignmentTaskSubmissionFromIdentifiedUserUI()
        toast.success(`已批改：${finalGrade}/${maxPoints} 分`)
      } else {
        toast.error('批改題目失敗')
      }
    } catch {
      toast.error('批改時發生錯誤')
    } finally {
      setIsRunning(false)
    }
  }

  // --- TEST CASE HELPERS (teacher) ---
  function addTestCase() {
    setContents((prev) => ({
      ...prev,
      test_cases: [
        ...prev.test_cases,
        {
          id: 'tc_' + uuidv4(),
          label: t('dashboard.assignments.editor.task_editor.code.test_default_label', {
            count: prev.test_cases.length + 1,
          }),
          stdin: '',
          expectedStdout: '',
          hidden: false,
          weight: 1,
        },
      ],
    }))
  }

  function removeTestCase(index: number) {
    setContents((prev) => ({
      ...prev,
      test_cases: prev.test_cases.filter((_, i) => i !== index),
    }))
  }

  function updateTestCase(index: number, field: keyof CodeTestCase, value: any) {
    setContents((prev) => ({
      ...prev,
      test_cases: prev.test_cases.map((tc, i) => (i === index ? { ...tc, [field]: value } : tc)),
    }))
  }

  const hiddenTestCount = view === 'student'
    ? contents.hidden_test_count ?? 0
    : contents.test_cases.filter((tc) => tc.hidden).length

  return (
    <AssignmentBoxUI
      type="code"
      view={view}
      saveFC={saveFC}
      submitFC={submitFC}
      gradeFC={gradeFC}
      currentPoints={userSubmissionObject?.grade}
      maxPoints={assignmentTaskOutsideProvider?.max_grade_value || assignmentTaskState?.assignmentTask?.max_grade_value}
      showSavingDisclaimer={showSavingDisclaimer}
      autoGradable={true}
      studentActionDisabled={runtimeUnavailable}
      studentActionDisabledDescriptionId={runtimeWarningId}
    >
      <div className="flex flex-col space-y-4">
        {runtimeUnavailable && (
          <div
            id={runtimeWarningId}
            role="alert"
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800"
          >
            {t(
              view === 'teacher'
                ? 'dashboard.assignments.editor.task_editor.code.unsupported_runtime_teacher'
                : 'dashboard.assignments.editor.task_editor.code.unsupported_runtime_student',
              { language: selectedLang?.name || `ID ${contents.language_id}` }
            )}
          </div>
        )}
        {/* === TEACHER VIEW === */}
        {view === 'teacher' && (
          <>
            {/* Language & Grading Mode */}
            <div className="flex flex-col sm:flex-row gap-3">
              <div className="flex flex-col space-y-1 flex-1">
                <label className="text-xs font-semibold text-slate-500">
                  {t('dashboard.assignments.editor.task_editor.code.language_label')}
                </label>
                <select
                  value={contents.language_id}
                  onChange={(e) => {
                    const langId = Number(e.target.value)
                    const lang = getLanguageById(langId)
                    setContents((prev) => ({
                      ...prev,
                      language_id: langId,
                      starter_code: lang?.defaultCode || prev.starter_code,
                    }))
                    setCode(lang?.defaultCode || code)
                  }}
                  className="px-3 py-1.5 text-sm border border-gray-200 rounded-md bg-white"
                >
                  {!selectedLang && (
                    <option value={contents.language_id} disabled>
                      {t('dashboard.assignments.editor.task_editor.code.unknown_language', {
                        id: contents.language_id,
                      })}
                    </option>
                  )}
                  {PLAYGROUND_LANGUAGES.map((lang) => {
                    // This surface only grades through the executor, so both
                    // unsupported runtimes and preview-only languages stay
                    // visible but disabled.
                    const { disabled, note } = getLanguageOptionState(lang.id, {
                      previewSupported: false,
                      apiAdaptersSupported: false,
                    })
                    return (
                      <option key={lang.id} value={lang.id} disabled={disabled}>
                        {disabled ? `${lang.name}（${note}）` : lang.name}
                      </option>
                    )
                  })}
                </select>
              </div>
              <div className="flex flex-col space-y-1 flex-1">
                <label className="text-xs font-semibold text-slate-500">
                  {t('dashboard.assignments.editor.task_editor.code.grading_mode_label')}
                </label>
                <select
                  value={contents.grading_mode}
                  onChange={(e) =>
                    setContents((prev) => ({ ...prev, grading_mode: e.target.value as any }))
                  }
                  className="px-3 py-1.5 text-sm border border-gray-200 rounded-md bg-white"
                >
                  <option value="equal_weight">{t('dashboard.assignments.editor.task_editor.code.grading_modes.equal_weight')}</option>
                  <option value="binary">{t('dashboard.assignments.editor.task_editor.code.grading_modes.binary')}</option>
                  <option value="custom_weights">{t('dashboard.assignments.editor.task_editor.code.grading_modes.custom_weights')}</option>
                </select>
              </div>
            </div>

            {contents.mode === 'web_preview' && (
              <>
                <WebPreviewWorkspace
                  htmlCode={htmlCode}
                  cssCode={cssCode}
                  jsCode={jsCode}
                  setHtmlCode={setHtmlCode}
                  setCssCode={setCssCode}
                  setJsCode={setJsCode}
                  showJs={true}
                  editable={true}
                />
                <div className="grid gap-3 md:grid-cols-3">
                  {(['html', 'css', 'js'] as const).map((kind) => (
                    <label key={kind} className="flex flex-col gap-1 text-xs font-semibold text-slate-500">
                      {kind.toUpperCase()} 參考答案
                      <textarea
                        value={contents[`solution_${kind}`] || ''}
                        onChange={(event) => setContents((prev) => ({
                          ...prev,
                          [`solution_${kind}`]: event.target.value,
                        }))}
                        rows={7}
                        className="rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-xs text-slate-100"
                      />
                    </label>
                  ))}
                </div>
              </>
            )}

            {/* Starter Code */}
            {contents.mode !== 'web_preview' && <div className="flex flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.code.starter_code_label')}
              </label>
              <div className={`rounded-md overflow-hidden ${cmClassName}`}>
                {cmTheme && (
                  <CodeMirror
                    value={code}
                    onChange={(val) => setCode(val)}
                    extensions={cmExtensions}
                    theme={cmTheme}
                    style={cmStyles}
                    height="200px"
                    basicSetup={{ lineNumbers: true, foldGutter: false }}
                  />
                )}
              </div>
            </div>}

            {/* Solution Code (collapsible) */}
            {contents.mode !== 'web_preview' && <div className="flex flex-col space-y-1">
              <button
                onClick={() => setShowSolution(!showSolution)}
                className="flex items-center space-x-1 text-xs font-semibold text-slate-500 hover:text-slate-700"
              >
                {showSolution ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span>{t('dashboard.assignments.editor.task_editor.code.solution_code_label')}</span>
              </button>
              {showSolution && (
                <div className={`rounded-md overflow-hidden ${cmClassName}`}>
                  {cmTheme && (
                    <CodeMirror
                      value={contents.solution_code}
                      onChange={(val) => setContents((prev) => ({ ...prev, solution_code: val }))}
                      extensions={cmExtensions}
                      theme={cmTheme}
                      style={cmStyles}
                      height="200px"
                      basicSetup={{ lineNumbers: true, foldGutter: false }}
                    />
                  )}
                </div>
              )}
            </div>}

            {/* Test Cases */}
            {contents.mode !== 'web_preview' && <div className="flex flex-col space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-slate-500">
                  {t('dashboard.assignments.editor.task_editor.code.test_cases_label')}
                </label>
                <button
                  onClick={addTestCase}
                  className="flex items-center space-x-1 text-xs font-semibold text-emerald-600 hover:text-emerald-700"
                >
                  <Plus size={14} />
                  <span>{t('dashboard.assignments.editor.task_editor.code.add_test_case')}</span>
                </button>
              </div>
              {contents.test_cases.map((tc, index) => (
                <div
                  key={tc.id}
                  className="flex flex-col space-y-2 p-3 border border-gray-200 rounded-md bg-white"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2 flex-1">
                      <input
                        value={tc.label}
                        onChange={(e) => updateTestCase(index, 'label', e.target.value)}
                        placeholder={t('dashboard.assignments.editor.task_editor.code.test_label_placeholder')}
                        className="px-2 py-1 text-sm border border-gray-200 rounded-md flex-1"
                      />
                      <button
                        onClick={() => updateTestCase(index, 'hidden', !tc.hidden)}
                        className={`flex items-center space-x-1 px-2 py-1 text-xs rounded-md ${
                          tc.hidden
                            ? 'bg-amber-100 text-amber-700'
                            : 'bg-slate-100 text-slate-500'
                        }`}
                        title={t(tc.hidden
                          ? 'dashboard.assignments.editor.task_editor.code.hidden_test_title'
                          : 'dashboard.assignments.editor.task_editor.code.visible_test_title')}
                      >
                        {tc.hidden ? <EyeOff size={12} /> : <Eye size={12} />}
                        <span>{t(tc.hidden
                          ? 'dashboard.assignments.editor.task_editor.code.hidden_label'
                          : 'dashboard.assignments.editor.task_editor.code.visible_label')}</span>
                      </button>
                      {contents.grading_mode === 'custom_weights' && (
                        <input
                          type="number"
                          value={tc.weight}
                          onChange={(e) => updateTestCase(index, 'weight', Math.max(1, Number(e.target.value)))}
                          className="w-16 px-2 py-1 text-sm border border-gray-200 rounded-md text-center"
                          min={1}
                          title={t('dashboard.assignments.editor.task_editor.code.weight_label')}
                        />
                      )}
                    </div>
                    <button
                      onClick={() => removeTestCase(index)}
                      className="w-6 h-6 flex items-center justify-center rounded-md bg-slate-100 text-slate-400 hover:bg-red-100 hover:text-red-500 ml-2"
                    >
                      <Minus size={12} />
                    </button>
                  </div>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <div className="flex flex-col space-y-1 flex-1">
                      <label className="text-[10px] font-semibold text-slate-400 uppercase">
                        {t('dashboard.assignments.editor.task_editor.code.stdin_label')}
                      </label>
                      <textarea
                        value={tc.stdin}
                        onChange={(e) => updateTestCase(index, 'stdin', e.target.value)}
                        placeholder={t('dashboard.assignments.editor.task_editor.code.input_placeholder')}
                        rows={2}
                        className="px-2 py-1 text-sm border border-gray-200 rounded-md font-mono resize-y"
                      />
                    </div>
                    <div className="flex flex-col space-y-1 flex-1">
                      <label className="text-[10px] font-semibold text-slate-400 uppercase">
                        {t('dashboard.assignments.editor.task_editor.code.expected_stdout_label')}
                      </label>
                      <textarea
                        value={tc.expectedStdout}
                        onChange={(e) => updateTestCase(index, 'expectedStdout', e.target.value)}
                        placeholder={t('dashboard.assignments.editor.task_editor.code.expected_output_placeholder')}
                        rows={2}
                        className="px-2 py-1 text-sm border border-gray-200 rounded-md font-mono resize-y"
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>}

            {/* Student behavior options */}
            <div className="flex flex-col space-y-2">
              <div className="flex items-center space-x-1.5 text-slate-500">
                <Settings2 size={13} />
                <p className="text-xs font-semibold">{t('dashboard.assignments.editor.task_editor.code.student_behavior_label')}</p>
              </div>
              <div className="flex flex-col space-y-1.5">
                <CodeOptionToggle
                  icon={<Play size={13} />}
                  label={t('dashboard.assignments.editor.task_editor.code.allow_student_run_label')}
                  description={t('dashboard.assignments.editor.task_editor.code.allow_student_run_description')}
                  checked={contents.allow_student_run !== false}
                  onChange={(v) => setContents((prev) => ({ ...prev, allow_student_run: v }))}
                />
                <CodeOptionToggle
                  icon={<Eye size={13} />}
                  label={t('dashboard.assignments.editor.task_editor.code.show_test_details_label')}
                  description={t('dashboard.assignments.editor.task_editor.code.show_test_details_description')}
                  checked={contents.show_test_details_on_fail !== false}
                  onChange={(v) => setContents((prev) => ({ ...prev, show_test_details_on_fail: v }))}
                />
                <CodeOptionToggle
                  icon={<EyeOff size={13} />}
                  label={t('dashboard.assignments.editor.task_editor.code.show_hidden_count_label')}
                  description={t('dashboard.assignments.editor.task_editor.code.show_hidden_count_description')}
                  checked={contents.show_hidden_test_count !== false}
                  onChange={(v) => setContents((prev) => ({ ...prev, show_hidden_test_count: v }))}
                />
                <CodeOptionToggle
                  icon={<ShieldCheck size={13} />}
                  label={t('dashboard.assignments.editor.task_editor.code.require_passing_label')}
                  description={t('dashboard.assignments.editor.task_editor.code.require_passing_description')}
                  checked={contents.require_passing_to_submit === true}
                  onChange={(v) => setContents((prev) => ({ ...prev, require_passing_to_submit: v }))}
                />
              </div>
            </div>

            {/* Run Tests (teacher) */}
            <div className="flex items-center space-x-2">
              <button
                onClick={runCode}
                disabled={isRunning || runtimeUnavailable}
                aria-describedby={runtimeUnavailable ? runtimeWarningId : undefined}
                className="flex items-center space-x-1.5 px-3 py-1.5 text-sm font-semibold bg-slate-700 text-white rounded-md hover:bg-slate-800 disabled:opacity-50"
              >
                {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                <span>{isRunning
                  ? t('dashboard.assignments.editor.task_editor.code.running_tests')
                  : t('dashboard.assignments.editor.task_editor.code.run_tests')}</span>
              </button>
            </div>

            {/* Results (teacher) */}
            {showResults && results.length > 0 && <TestResultsPanel
              results={results}
              testCases={contents.mode === 'web_preview'
                ? (contents.web_checks || []).map((check) => ({
                  id: check.id,
                  label: check.label,
                  stdin: '',
                  expectedStdout: `${check.target} ${check.match} ${check.pattern}`,
                  hidden: check.hidden,
                  weight: check.weight,
                }))
                : contents.test_cases}
              view="grading"
            />}
          </>
        )}

        {/* === STUDENT VIEW === */}
        {view === 'student' && (
          <>
            {/* Language badge */}
            <div className="flex items-center space-x-2">
              <span className="px-2 py-0.5 text-xs font-semibold bg-slate-100 text-slate-600 rounded-full">
                {contents.mode === 'web_preview'
                  ? t('dashboard.assignments.editor.task_editor.code.web_preview_badge')
                  : selectedLang?.name || t('dashboard.assignments.editor.task_editor.code.unknown_language', { id: contents.language_id })}
              </span>
              {/* Hidden-test badge only if the task allows showing the count */}
              {contents.mode !== 'web_preview' && hiddenTestCount > 0 && contents.show_hidden_test_count !== false && (
                <span className="px-2 py-0.5 text-xs font-semibold bg-amber-100 text-amber-700 rounded-full">
                  {t('dashboard.assignments.editor.task_editor.code.hidden_test_count', { count: hiddenTestCount })}
                </span>
              )}
            </div>

            {contents.mode === 'web_preview' && (
              <>
                <WebPreviewWorkspace
                  htmlCode={htmlCode}
                  cssCode={cssCode}
                  jsCode={jsCode}
                  setHtmlCode={setHtmlCode}
                  setCssCode={setCssCode}
                  setJsCode={setJsCode}
                  showJs={(contents.starter_js || contents.solution_js || jsCode).trim().length > 0}
                  editable={true}
                />
                {contents.allow_student_run !== false && (
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={runCode}
                      disabled={isRunning || runtimeUnavailable}
                      aria-describedby={runtimeUnavailable ? runtimeWarningId : undefined}
                      className="flex items-center space-x-1.5 px-3 py-1.5 text-sm font-semibold bg-emerald-600 text-white rounded-md hover:bg-emerald-700 disabled:opacity-50"
                    >
                      {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                      <span>{isRunning
                        ? t('dashboard.assignments.editor.task_editor.code.checking')
                        : t('dashboard.assignments.editor.task_editor.code.check_task')}</span>
                    </button>
                  </div>
                )}
                {showResults && (
                  <TestResultsPanel
                    results={results}
                    testCases={(contents.web_checks || []).map((check) => ({
                      id: check.id,
                      label: check.label,
                      stdin: '',
                      expectedStdout: `${check.target} ${check.match} ${check.pattern}`,
                      hidden: check.hidden,
                      weight: check.weight,
                    }))}
                    view="student"
                    showDetailsOnFail={contents.show_test_details_on_fail !== false}
                  />
                )}
              </>
            )}

            {contents.mode !== 'web_preview' && (
              <>
            {/* Code Editor */}
            <div className={`rounded-md overflow-hidden ${cmClassName}`}>
              {cmTheme && (
                <CodeMirror
                  value={code}
                  onChange={(val) => setCode(val)}
                  extensions={studentCmExtensions}
                  theme={cmTheme}
                  style={cmStyles}
                  height="300px"
                  basicSetup={{ lineNumbers: true, foldGutter: false }}
                />
              )}
            </div>
            {antiPasteEnabled && (
              <div className="flex items-center space-x-1.5 text-[10px] text-amber-600 bg-amber-50 rounded-md px-2 py-1 w-fit">
                <span>🔒</span>
                <span>{t('dashboard.assignments.editor.task_editor.general.paste_blocked_hint')}</span>
              </div>
            )}

            {/* Run Button — only if the task allows students to run */}
            {contents.allow_student_run !== false && (
              <div className="flex items-center space-x-2">
                <button
                  onClick={runCode}
                  disabled={isRunning || runtimeUnavailable}
                  aria-describedby={runtimeUnavailable ? runtimeWarningId : undefined}
                  className="flex items-center space-x-1.5 px-3 py-1.5 text-sm font-semibold bg-emerald-600 text-white rounded-md hover:bg-emerald-700 disabled:opacity-50"
                >
                  {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  <span>{isRunning
                    ? t('dashboard.assignments.editor.task_editor.code.running_tests')
                    : t('dashboard.assignments.editor.task_editor.code.run_tests')}</span>
                </button>
              </div>
            )}

            {/* Submission gating notice — when the teacher requires passing
                all visible tests before save. */}
            {submissionGatedByPassing && !allVisiblePassing && (
              <div className="flex items-center space-x-1.5 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2.5 py-1.5 w-fit">
                <ShieldCheck size={13} />
                <span>{t('dashboard.assignments.editor.task_editor.code.must_pass_hint')}</span>
              </div>
            )}

            {/* Results */}
            {showResults && (
              <TestResultsPanel
                results={results}
                testCases={contents.test_cases}
                view="student"
                showDetailsOnFail={contents.show_test_details_on_fail !== false}
              />
            )}

              </>
            )}
          </>
        )}

        {/* === GRADING VIEW === */}
        {view === 'grading' && (
          <>
            {/* Language badge */}
            <div className="flex items-center space-x-2">
              <span className="px-2 py-0.5 text-xs font-semibold bg-slate-100 text-slate-600 rounded-full">
                {contents.mode === 'web_preview'
                  ? t('dashboard.assignments.editor.task_editor.code.web_preview_badge')
                  : selectedLang?.name || t('dashboard.assignments.editor.task_editor.code.unknown_language', { id: contents.language_id })}
              </span>
              <span className="px-2 py-0.5 text-xs font-semibold bg-blue-100 text-blue-700 rounded-full">
                {contents.grading_mode === 'binary' ? 'Binary' : contents.grading_mode === 'custom_weights' ? 'Custom Weights' : 'Equal Weight'}
              </span>
            </div>

            {contents.mode === 'web_preview' && (
              <WebPreviewWorkspace
                htmlCode={htmlCode}
                cssCode={cssCode}
                jsCode={jsCode}
                setHtmlCode={setHtmlCode}
                setCssCode={setCssCode}
                setJsCode={setJsCode}
                showJs={(contents.starter_js || contents.solution_js || jsCode).trim().length > 0}
                editable={false}
              />
            )}

            {contents.mode !== 'web_preview' && (
              <>
            {/* Student's Code (read-only) */}
            <div className="flex flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">
                {t('dashboard.assignments.editor.task_editor.code.student_code_label')}
              </label>
              <div className={`rounded-md overflow-hidden ${cmClassName}`}>
                {cmTheme && (
                  <CodeMirror
                    value={code}
                    extensions={cmExtensions}
                    theme={cmTheme}
                    style={cmStyles}
                    height="300px"
                    readOnly={true}
                    editable={false}
                    basicSetup={{ lineNumbers: true, foldGutter: false }}
                  />
                )}
              </div>
            </div>

            {/* Solution Code (collapsible) */}
            {contents.solution_code && (
              <div className="flex flex-col space-y-1">
                <button
                  onClick={() => setShowSolution(!showSolution)}
                  className="flex items-center space-x-1 text-xs font-semibold text-slate-500 hover:text-slate-700"
                >
                  {showSolution ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span>{t('dashboard.assignments.editor.task_editor.code.reference_solution_label')}</span>
                </button>
                {showSolution && (
                  <div className={`rounded-md overflow-hidden ${cmClassName}`}>
                    {cmTheme && (
                      <CodeMirror
                        value={contents.solution_code}
                        extensions={cmExtensions}
                        theme={cmTheme}
                        style={cmStyles}
                        height="200px"
                        readOnly={true}
                        editable={false}
                        basicSetup={{ lineNumbers: true, foldGutter: false }}
                      />
                    )}
                  </div>
                )}
              </div>
            )}
              </>
            )}

            {/* Run & Grade */}
            {isRunning && (
              <div className="flex items-center space-x-2 text-sm text-slate-500">
                <Loader2 size={14} className="animate-spin" />
                <span>{t('dashboard.assignments.editor.task_editor.code.running_tests')}</span>
              </div>
            )}

            {/* Results */}
            {results.length > 0 && (
              <TestResultsPanel
                results={results}
                testCases={contents.mode === 'web_preview'
                  ? (contents.web_checks || []).map((check) => ({
                    id: check.id,
                    label: check.label,
                    stdin: '',
                    expectedStdout: `${check.target} ${check.match} ${check.pattern}`,
                    hidden: check.hidden,
                    weight: check.weight,
                  }))
                  : contents.test_cases}
                view="grading"
              />
            )}
          </>
        )}
      </div>
    </AssignmentBoxUI>
  )
}

function WebPreviewWorkspace({
  htmlCode,
  cssCode,
  jsCode,
  setHtmlCode,
  setCssCode,
  setJsCode,
  showJs,
  editable,
}: {
  htmlCode: string
  cssCode: string
  jsCode: string
  // eslint-disable-next-line no-unused-vars
  setHtmlCode: (value: string) => void
  // eslint-disable-next-line no-unused-vars
  setCssCode: (value: string) => void
  // eslint-disable-next-line no-unused-vars
  setJsCode: (value: string) => void
  showJs: boolean
  editable: boolean
}) {
  const { t } = useTranslation()
  const editorBaseClass = 'w-full min-h-[180px] flex-1 resize-y rounded-md border border-slate-700 bg-[#111827] p-3 font-mono text-[13px] leading-5 text-slate-100 outline-none focus:border-emerald-400 disabled:opacity-80'

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.95fr)]">
      <div className="grid gap-3">
        <div className="rounded-md border border-slate-200 bg-slate-950 p-3">
          <label className="mb-2 block text-xs font-bold uppercase tracking-wide text-slate-300">HTML</label>
          <textarea
            value={htmlCode}
            onChange={(event) => setHtmlCode(event.target.value)}
            disabled={!editable}
            spellCheck={false}
            className={editorBaseClass}
          />
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-950 p-3">
          <label className="mb-2 block text-xs font-bold uppercase tracking-wide text-slate-300">CSS</label>
          <textarea
            value={cssCode}
            onChange={(event) => setCssCode(event.target.value)}
            disabled={!editable}
            spellCheck={false}
            className={editorBaseClass}
          />
        </div>
        {showJs && (
          <div className="rounded-md border border-slate-200 bg-slate-950 p-3">
            <label className="mb-2 block text-xs font-bold uppercase tracking-wide text-slate-300">JavaScript</label>
            <textarea
              value={jsCode}
              onChange={(event) => setJsCode(event.target.value)}
              disabled={!editable}
              spellCheck={false}
              className={editorBaseClass}
            />
          </div>
        )}
      </div>
      <div className="flex min-h-[420px] flex-col overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
          <span className="text-sm font-semibold text-slate-700">
            {t('dashboard.assignments.editor.task_editor.code.live_preview_label')}
          </span>
          <span className="text-xs text-slate-500">
            {t('dashboard.assignments.editor.task_editor.code.live_preview_help')}
          </span>
        </div>
        <div className="min-h-0 flex-1">
          <LivePreview
            title={t('dashboard.assignments.editor.task_editor.code.live_preview_title')}
            source={htmlCode}
            files={[
              { name: 'styles.css', content: cssCode },
              { name: 'script.js', content: jsCode },
            ]}
            debounceMs={350}
          />
        </div>
      </div>
    </div>
  )
}

function TestResultsPanel({
  results,
  testCases,
  view,
  showDetailsOnFail = true,
}: {
  results: CodeTestResult[]
  testCases: CodeTestCase[]
  view: 'student' | 'grading'
  showDetailsOnFail?: boolean
}) {
  const { t } = useTranslation()
  const passedCount = results.filter((r) => r.passed).length
  const totalCount = results.length

  return (
    <div className="flex flex-col space-y-2 p-3 bg-slate-50 rounded-md border border-slate-200">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-600">
          {t('dashboard.assignments.editor.task_editor.code.results_summary', {
            passed: passedCount,
            total: totalCount,
          })}
        </span>
        <span
          className={`px-2 py-0.5 text-xs font-bold rounded-full ${
            passedCount === totalCount
              ? 'bg-emerald-100 text-emerald-700'
              : 'bg-red-100 text-red-700'
          }`}
        >
          {passedCount === totalCount
            ? t('dashboard.assignments.editor.task_editor.code.all_passed')
            : t('dashboard.assignments.editor.task_editor.code.some_failed')}
        </span>
      </div>
      <div className="flex flex-col space-y-1.5">
        {results.map((result) => {
          const tc = testCases.find((t) => t.id === result.id)
          const isHidden = tc?.hidden && view === 'student'
          // Teachers always see details. In the student view, failure details
          // are hidden when the task disables details OR the test is hidden.
          const detailsSuppressed = view === 'student' && !showDetailsOnFail
          return (
            <div
              key={result.id}
              className={`flex flex-col p-2 rounded-md text-sm ${
                result.passed ? 'bg-emerald-50 border border-emerald-200' : 'bg-red-50 border border-red-200'
              }`}
            >
              <div className="flex items-center space-x-2">
                {result.passed ? (
                  <CheckCircle2 size={14} className="text-emerald-600 flex-none" />
                ) : (
                  <XCircle size={14} className="text-red-600 flex-none" />
                )}
                <span className="font-medium text-slate-700">{result.label}</span>
                {result.time && (
                  <span className="text-[10px] text-slate-400 ml-auto">{result.time}s</span>
                )}
              </div>
              {!result.passed && !isHidden && !detailsSuppressed && (
                <div className="mt-1.5 pl-6 text-xs space-y-0.5">
                  {result.expected_stdout !== null && (
                    <div>
                      <span className="text-slate-400">
                        {t('dashboard.assignments.editor.task_editor.code.expected_result_label')}{' '}
                      </span>
                      <code className="text-slate-600 bg-white px-1 rounded">{result.expected_stdout}</code>
                    </div>
                  )}
                  {result.actual_stdout !== null && (
                    <div>
                      <span className="text-slate-400">
                        {t('dashboard.assignments.editor.task_editor.code.actual_result_label')}{' '}
                      </span>
                      <code className="text-red-600 bg-white px-1 rounded">{result.actual_stdout}</code>
                    </div>
                  )}
                  {result.stderr && (
                    <div>
                      <span className="text-slate-400">
                        {t('dashboard.assignments.editor.task_editor.code.error_result_label')}{' '}
                      </span>
                      <code className="text-red-600 bg-white px-1 rounded text-[11px] break-all">{result.stderr}</code>
                    </div>
                  )}
                  {result.compile_output && (
                    <div>
                      <span className="text-slate-400">
                        {t('dashboard.assignments.editor.task_editor.code.compile_result_label')}{' '}
                      </span>
                      <code className="text-red-600 bg-white px-1 rounded text-[11px] break-all">{result.compile_output}</code>
                    </div>
                  )}
                </div>
              )}
              {isHidden && !result.passed && (
                <div className="mt-1 pl-6 text-xs text-slate-400 italic">
                  {t('dashboard.assignments.editor.task_editor.code.hidden_details')}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function CodeOptionToggle({
  icon,
  label,
  description,
  checked,
  onChange,
}: {
  icon: React.ReactNode
  label: string
  description: string
  checked: boolean
  // eslint-disable-next-line no-unused-vars
  onChange: (next: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-2 p-2 rounded-md bg-white border border-slate-200">
      <div className="flex items-start gap-2 flex-1 min-w-0">
        <div className="mt-0.5 flex-none text-slate-500">{icon}</div>
        <div className="flex flex-col min-w-0">
          <p className="text-[11px] font-bold text-slate-700">{label}</p>
          <p className="text-[10px] text-slate-500 leading-snug mt-0.5">{description}</p>
        </div>
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        aria-pressed={checked}
        className={`relative flex-none inline-flex h-4 w-7 items-center rounded-full transition-colors ${
          checked ? 'bg-slate-700' : 'bg-slate-200 hover:bg-slate-300'
        }`}
      >
        <span
          className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-3.5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  )
}

export default TaskCodeObject
