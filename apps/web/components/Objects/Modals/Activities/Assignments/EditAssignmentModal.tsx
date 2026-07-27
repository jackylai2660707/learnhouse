import React from 'react';
import { updateAssignment } from '@services/courses/assignments';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/query/keys';
import { useOrg } from '@components/Contexts/OrgContext';
import { getUserGroups } from '@services/usergroups/usergroups';
import toast from 'react-hot-toast';
import * as Form from '@radix-ui/react-form';
import { useFormik } from 'formik';
import Modal from '@components/Objects/StyledElements/Modal/Modal';
import { useTranslation } from 'react-i18next';

// Same input class used by the create-assignment modal, so both forms look
// identical to the user.
const inputClass =
    'w-full h-9 px-3 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors';
const textareaClass =
    'w-full px-3 py-2 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors resize-none';
const labelClass = 'text-sm font-medium text-gray-700';
const errorClass = 'text-xs text-red-500';

function normalizeUsergroupIds(value: any): number[] {
    if (!Array.isArray(value)) return [];
    const ids: number[] = [];
    value.forEach((item) => {
        const id = Number(item);
        if (Number.isFinite(id) && !ids.includes(id)) {
            ids.push(id);
        }
    });
    return ids;
}

function responseErrorMessage(response: any, fallback: string) {
    const detail = response?.data?.detail ?? response?.data?.message ?? response?.detail ?? response?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    if (response instanceof Error && response.message) return response.message;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join('；');
    return fallback;
}

function dateInputValue(date: Date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

import {
    Loader2,
    RotateCcw,
    School,
    UsersRound,
} from 'lucide-react';

interface Assignment {
    assignment_uuid: string;
    title: string;
    description: string;
    due_date?: string;
    grading_type?: string;
    auto_grading?: boolean | string | number;
    anti_copy_paste?: boolean | string | number;
    show_correct_answers?: boolean | string | number;
    allow_retries?: boolean | string | number;
    max_retries?: number;
    subject?: string;
    education_stage?: string;
    grade_level?: string;
    school_year?: string;
    term?: string;
    unit?: string;
    learning_objectives?: string[];
    target_usergroup_ids?: number[];
    score_policy?: string;
    teacher_review_required?: boolean | string | number;
    teacher_review_status?: string;
    assignment_tasks?: any[];
}

interface EditAssignmentFormProps {
    onClose: () => void;
    assignment: Assignment;
    accessToken: string;
}

interface EditAssignmentModalProps {
    isOpen: boolean;
    onClose: () => void;
    assignment: Assignment;
    accessToken: string;
}

const EditAssignmentForm: React.FC<EditAssignmentFormProps> = ({
    onClose,
    assignment,
    accessToken
}) => {
    const { t } = useTranslation()
    const queryClient = useQueryClient()
    const org = useOrg() as any

    const usergroupsQuery = useQuery({
        queryKey: queryKeys.usergroups.list(org?.id),
        queryFn: async () => {
            const res = await getUserGroups(org.id, accessToken)
            if (res.success === false) {
                throw new Error(res?.data?.detail || '讀取班級/群組失敗')
            }
            return Array.isArray(res.data) ? res.data : []
        },
        enabled: !!org?.id && !!accessToken,
        staleTime: 60_000,
    })
    const usergroups = Array.isArray(usergroupsQuery.data) ? usergroupsQuery.data : []
    const usergroupSelectionTouchedRef = React.useRef(false);
    const todayDate = React.useMemo(() => dateInputValue(new Date()), []);

    const formik = useFormik({
        initialValues: {
            title: assignment.title || '',
            description: assignment.description || '',
            due_date: assignment.due_date || '',
            subject: assignment.subject || '',
            education_stage: assignment.education_stage || '',
            grade_level: assignment.grade_level || '',
            school_year: assignment.school_year || '',
            term: assignment.term || '',
            unit: assignment.unit || '',
            learning_objectives: (assignment.learning_objectives || []).join('\n'),
            target_usergroup_ids: normalizeUsergroupIds(assignment.target_usergroup_ids),
        },
        enableReinitialize: true,
        onSubmit: async (values, { setSubmitting }) => {
            if (
                values.due_date &&
                values.due_date < todayDate &&
                values.due_date !== (assignment.due_date || '')
            ) {
                toast.error('截止日期不能早於今天。');
                setSubmitting(false);
                return;
            }
            const payload: any = { ...values };
            payload.learning_objectives = String(payload.learning_objectives || '')
                .split('\n')
                .map((item) => item.trim())
                .filter(Boolean);
            payload.target_usergroup_ids = normalizeUsergroupIds(payload.target_usergroup_ids);
            payload.grading_type = 'PERCENTAGE';
            payload.auto_grading = true;
            payload.anti_copy_paste = false;
            payload.show_correct_answers = true;
            payload.allow_retries = true;
            payload.max_retries = 0;
            payload.score_policy = 'highest';
            payload.teacher_review_required = false;
            payload.teacher_review_status = 'not_required';
            const toast_loading = toast.loading(t('dashboard.assignments.modals.edit.toasts.updating'));
            try {
                const res = await updateAssignment(payload, assignment.assignment_uuid, accessToken);
                if (res.success) {
                    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.detail(assignment.assignment_uuid) });
                    queryClient.invalidateQueries({ queryKey: queryKeys.assignments.allCourseAssignments() });
                    if (org?.id) {
                        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.workbench(org.id) });
                        queryClient.invalidateQueries({ queryKey: queryKeys.assignments.gradebookAll(org.id) });
                    }
                    toast.success(t('dashboard.assignments.modals.edit.toasts.success'));
                    onClose();
                } else {
                    toast.error(responseErrorMessage(res, t('dashboard.assignments.modals.edit.toasts.error')));
                }
            } catch (error) {
                toast.error(responseErrorMessage(error, t('dashboard.assignments.modals.edit.toasts.error_detail')));
            } finally {
                toast.dismiss(toast_loading);
                setSubmitting(false);
            }
        }
    });
    const selectedUsergroupIds = normalizeUsergroupIds(formik.values.target_usergroup_ids);
    const selectedUsergroupCount = selectedUsergroupIds.length;

    React.useEffect(() => {
        if (
            usergroupSelectionTouchedRef.current ||
            selectedUsergroupCount > 0 ||
            usergroups.length !== 1
        ) {
            return;
        }
        const onlyGroupId = Number(usergroups[0]?.id);
        if (Number.isFinite(onlyGroupId)) {
            formik.setFieldValue('target_usergroup_ids', [onlyGroupId], false);
        }
    }, [formik, selectedUsergroupCount, usergroups]);

    const toggleUsergroup = (groupId: number) => {
        usergroupSelectionTouchedRef.current = true;
        const current = normalizeUsergroupIds(formik.values.target_usergroup_ids);
        formik.setFieldValue(
            'target_usergroup_ids',
            current.includes(groupId)
                ? current.filter((id: number) => id !== groupId)
                : [...current, groupId],
            true
        );
    };

    return (
        <Form.Root onSubmit={formik.handleSubmit} className="space-y-5">
            {/* Basic info */}
            <Form.Field name="title" className="space-y-1.5">
                <Form.Label className={labelClass}>
                    {t('dashboard.assignments.modals.edit.form.title_label')}
                </Form.Label>
                <Form.Message match="valueMissing" className={errorClass}>
                    {t('dashboard.assignments.modals.edit.form.title_required')}
                </Form.Message>
                <Form.Control asChild>
                    <input
                        onChange={formik.handleChange}
                        value={formik.values.title}
                        type="text"
                        required
                        className={inputClass}
                    />
                </Form.Control>
            </Form.Field>

            <Form.Field name="description" className="space-y-1.5">
                <Form.Label className={labelClass}>
                    {t('dashboard.assignments.modals.edit.form.description_label')}
                </Form.Label>
                <Form.Control asChild>
                    <textarea
                        onChange={formik.handleChange}
                        value={formik.values.description}
                        placeholder={t('dashboard.assignments.modals.edit.form.description_placeholder')}
                        rows={3}
                        className={textareaClass}
                    />
                </Form.Control>
            </Form.Field>

            <Form.Field name="due_date" className="space-y-1.5">
                <Form.Label className={labelClass}>
                    {t('dashboard.assignments.modals.edit.form.due_date_label')}
                </Form.Label>
                <Form.Message match="valueMissing" className={errorClass}>
                    {t('dashboard.assignments.modals.edit.form.due_date_required')}
                </Form.Message>
                <Form.Control asChild>
                    <input
                        type="date"
                        onChange={formik.handleChange}
                        value={formik.values.due_date}
                        required
                        min={
                            formik.values.due_date && formik.values.due_date < todayDate
                                ? formik.values.due_date
                                : todayDate
                        }
                        className={inputClass}
                    />
                </Form.Control>
            </Form.Field>

            <div className="rounded-xl nice-shadow p-4 space-y-3">
                <div className="flex items-center gap-2">
                    <UsersRound size={16} className="text-gray-500" />
                    <p className={labelClass}>發布對象</p>
                </div>
                <div className="space-y-2">
                    <p className="text-xs font-semibold text-gray-600">班級/群組</p>
                    <div className="flex flex-wrap gap-2">
                        {usergroupsQuery.isError && (
                            <div className="w-full rounded-lg border border-rose-100 bg-rose-50 px-3 py-2">
                                <p className="text-xs font-bold text-rose-700">
                                    {(usergroupsQuery.error as Error)?.message || '讀取班級/群組失敗'}
                                </p>
                                <button
                                    type="button"
                                    onClick={() => usergroupsQuery.refetch()}
                                    disabled={usergroupsQuery.isFetching}
                                    className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-3 text-xs font-black text-rose-800 ring-1 ring-inset ring-rose-200 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                    {usergroupsQuery.isFetching ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                                    {usergroupsQuery.isFetching ? '載入中' : '重新讀取班級'}
                                </button>
                            </div>
                        )}
                        {!usergroupsQuery.isError && usergroups.map((group: any) => {
                            const groupId = Number(group.id);
                            const active = selectedUsergroupIds.includes(groupId);
                            return (
                                <button
                                    key={group.id}
                                    type="button"
                                    onClick={() => toggleUsergroup(groupId)}
                                    className={`rounded-full px-3 py-1.5 text-xs font-bold border ${
                                        active
                                            ? 'bg-gray-900 text-white border-gray-900'
                                            : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                                    }`}
                                >
                                    {group.name}
                                </button>
                            );
                        })}
                        {!usergroupsQuery.isError && usergroups.length === 0 && (
                            <span className="text-xs text-gray-400">未建立班級/群組時可先存草稿；正式試行前請先匯入學生並建立班級。</span>
                        )}
                    </div>
                    {!usergroupsQuery.isError && usergroups.length > 0 && selectedUsergroupCount === 0 && (
                        <p className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
                            發布前請先指定班級/群組；草稿可以先儲存，稍後再補上。
                        </p>
                    )}
                </div>
                <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] font-semibold text-emerald-800">
                    簡單模式：只用選擇題、填空題和短問答；可用 AI、題庫或手動出題。學生可看答案再重做；預設取最高分並自動批改。
                </div>
            </div>

            <div className="rounded-xl nice-shadow p-4 space-y-3">
                <div className="flex items-center gap-2">
                    <School size={16} className="text-gray-500" />
                    <div>
                        <p className={labelClass}>AI 出題資料（可選）</p>
                        <p className="text-[11px] font-semibold text-gray-500">
                            填得越清楚，AI 生成的選擇、填空、短問答越貼近課堂。
                        </p>
                    </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <FieldInput
                        label="科目"
                        value={formik.values.subject}
                        onChange={(value) => formik.setFieldValue('subject', value, true)}
                        placeholder="例如：中文、數學、常識、Python"
                    />
                    <label className="space-y-1.5">
                        <span className="text-xs font-semibold text-gray-600">學段</span>
                        <select
                            value={formik.values.education_stage}
                            onChange={(event) => formik.setFieldValue('education_stage', event.target.value, true)}
                            className={inputClass}
                        >
                            <option value="">未設定</option>
                            <option value="primary">小學</option>
                            <option value="secondary">中學</option>
                        </select>
                    </label>
                    <FieldInput
                        label="年級"
                        value={formik.values.grade_level}
                        onChange={(value) => formik.setFieldValue('grade_level', value, true)}
                        placeholder="例如：小四 / 中一"
                    />
                    <FieldInput
                        label="學年"
                        value={formik.values.school_year}
                        onChange={(value) => formik.setFieldValue('school_year', value, true)}
                        placeholder="例如：2026-2027"
                    />
                    <FieldInput
                        label="學期"
                        value={formik.values.term}
                        onChange={(value) => formik.setFieldValue('term', value, true)}
                        placeholder="例如：第一學期"
                    />
                    <FieldInput
                        label="單元"
                        value={formik.values.unit}
                        onChange={(value) => formik.setFieldValue('unit', value, true)}
                        placeholder="例如：分數比較 / 水循環"
                    />
                </div>
                <label className="space-y-1.5 block">
                    <span className="text-xs font-semibold text-gray-600">學習目標（可選，每行一項）</span>
                    <textarea
                        value={formik.values.learning_objectives}
                        onChange={(event) => formik.setFieldValue('learning_objectives', event.target.value, true)}
                        rows={3}
                        className={textareaClass}
                        placeholder={'學生能理解基本概念\n學生能完成一個小練習'}
                    />
                </label>
            </div>

            <div className="flex justify-end space-x-3">
                <button
                    type="button"
                    onClick={onClose}
                    className="inline-flex items-center justify-center h-9 px-5 text-sm font-medium text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                >
                    {t('dashboard.assignments.modals.edit.form.cancel')}
                </button>
                <Form.Submit asChild>
                    <button
                        type="submit"
                        disabled={formik.isSubmitting}
                        className="inline-flex items-center justify-center h-9 px-5 text-sm font-medium text-white bg-black rounded-lg hover:bg-gray-800 transition-colors disabled:opacity-50"
                    >
                        {formik.isSubmitting ? t('dashboard.assignments.modals.edit.form.saving') : t('dashboard.assignments.modals.edit.form.save')}
                    </button>
                </Form.Submit>
            </div>
        </Form.Root>
    );
};

const EditAssignmentModal: React.FC<EditAssignmentModalProps> = ({
    isOpen,
    onClose,
    assignment,
    accessToken
}) => {
    const { t } = useTranslation()
    return (
        <Modal
            isDialogOpen={isOpen}
            onOpenChange={onClose}
            minHeight="md"
            minWidth="lg"
            dialogContent={
                <EditAssignmentForm
                    onClose={onClose}
                    assignment={assignment}
                    accessToken={accessToken}
                />
            }
            dialogTitle={t('dashboard.assignments.modals.edit.title')}
            dialogDescription={t('dashboard.assignments.modals.edit.description')}
            dialogTrigger={null}
        />
    );
};

function FieldInput({
    label,
    value,
    onChange,
    placeholder,
}: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
}) {
    return (
        <label className="space-y-1.5">
            <span className="text-xs font-semibold text-gray-600">{label}</span>
            <input
                value={value}
                onChange={(event) => onChange(event.target.value)}
                placeholder={placeholder}
                className={inputClass}
            />
        </label>
    );
}

export default EditAssignmentModal;
