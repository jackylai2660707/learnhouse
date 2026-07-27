import React, { useState } from 'react'
import Link from 'next/link'
import * as Form from '@radix-ui/react-form'
import BarLoader from 'react-spinners/BarLoader'
import { FileText } from '@phosphor-icons/react'
import { constructAcceptValue } from '@/lib/constants'
import { useOrg } from '@components/Contexts/OrgContext'
import useAdminStatus from '@components/Hooks/useAdminStatus'

const SUPPORTED_FILES = constructAcceptValue(['pdf'])

function DocumentPdfModal({ submitFileActivity, chapterId, course }: any) {
  const org = useOrg() as any
  const { rights } = useAdminStatus()
  const [documentpdf, setDocumentPdf] = React.useState(null) as any
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [name, setName] = React.useState('')
  const canBuildCourseFromPdf = (
    org?.config?.config?.resolved_features?.ai?.enabled === true
    && rights?.courses?.action_create === true
  )

  const handleSubmit = async (e: any) => {
    e.preventDefault()
    setIsSubmitting(true)
    await submitFileActivity(
      documentpdf,
      'documentpdf',
      {
        name: name,
        chapter_id: chapterId,
        activity_type: 'TYPE_DOCUMENT',
        activity_sub_type: 'SUBTYPE_DOCUMENT_PDF',
        published_version: 1,
        version: 1,
        course_id: course.id,
      },
      chapterId
    )
    setIsSubmitting(false)
  }

  return (
    <Form.Root onSubmit={handleSubmit} className="space-y-4">
      <div
        className="relative flex items-center justify-center h-20 rounded-xl overflow-hidden"
        style={{
          backgroundImage:
            'repeating-linear-gradient(0deg, transparent, transparent 8px, rgba(167,243,208,0.25) 8px, rgba(167,243,208,0.25) 9px), repeating-linear-gradient(90deg, transparent, transparent 8px, rgba(167,243,208,0.25) 8px, rgba(167,243,208,0.25) 9px)',
        }}
      >
        <span className="flex items-center gap-2 bg-white nice-shadow rounded-full px-4 py-1.5 text-sm font-medium text-gray-600">
          <FileText size={18} weight="duotone" className="text-emerald-400" />
          PDF 文件
        </span>
      </div>

      <div className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
        這會建立一個單一 PDF 學習活動，不會自動建立課程。
      </div>

      <div className="rounded-xl nice-shadow p-4 space-y-4">
        <Form.Field name="documentpdf-activity-name" className="space-y-1.5">
          <Form.Label className="text-sm font-medium text-gray-700">
            活動名稱
          </Form.Label>
          <Form.Message match="valueMissing" className="text-xs text-red-500">
            請輸入活動名稱
          </Form.Message>
          <Form.Control asChild>
            <input
              onChange={(e) => setName(e.target.value)}
              type="text"
              required
              placeholder="輸入活動名稱…"
              className="w-full h-9 px-3 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors"
            />
          </Form.Control>
        </Form.Field>

        <Form.Field name="documentpdf-activity-file" className="space-y-1.5">
          <Form.Label className="text-sm font-medium text-gray-700">
            PDF 檔案
          </Form.Label>
          <Form.Message match="valueMissing" className="text-xs text-red-500">
            請選擇 PDF 檔案
          </Form.Message>
          <Form.Control asChild>
            <input
              accept={SUPPORTED_FILES}
              type="file"
              onChange={(e: any) => setDocumentPdf(e.target.files[0])}
              required
              className="w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded-full file:border-0 file:text-xs file:font-medium file:bg-gray-100 file:text-gray-700 hover:file:bg-gray-200 transition-colors"
            />
          </Form.Control>
        </Form.Field>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {canBuildCourseFromPdf && (
          <Link
            href="/dash/courses/pdf-build"
            className="text-sm font-medium text-gray-600 underline underline-offset-4 hover:text-gray-900"
          >
            改用 PDF 智能建課
          </Link>
        )}
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
              '建立活動'
            )}
          </button>
        </Form.Submit>
      </div>
    </Form.Root>
  )
}

export default DocumentPdfModal
