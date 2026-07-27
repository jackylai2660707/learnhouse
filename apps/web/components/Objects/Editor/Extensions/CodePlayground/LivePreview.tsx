'use client'
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { RotateCcw, Ban, AlertTriangle, Info, TriangleAlert } from 'lucide-react'
import {
  buildPreviewDocument,
  normalizePreviewMessage,
  previewFilesKey,
  type PreviewFile,
} from './preview-document'
import { useTranslation } from 'react-i18next'

export type { PreviewFile } from './preview-document'

export type PreviewLogLevel = 'log' | 'info' | 'warn' | 'error'

export interface PreviewLogEntry {
  id: number
  level: PreviewLogLevel
  text: string
  line?: number | null
  col?: number | null
}

interface LivePreviewProps {
  /** Contents of the main editor pane (a whole HTML document). */
  source: string
  /** Extra files; *.css and *.js are linked into the document. */
  files?: PreviewFile[]
  /** Accessible name for the preview frame. */
  title: string
  /** Bump to force an immediate reload (bypasses the debounce). */
  reloadNonce?: number
  /** Debounce for keystroke-driven refreshes. */
  debounceMs?: number
  // eslint-disable-next-line no-unused-vars
  onErrorCountChange?: (count: number) => void
}

const MAX_LOGS = 200

const LEVEL_STYLES: Record<PreviewLogLevel, { text: string; icon: React.ReactNode }> = {
  error: { text: 'text-red-400', icon: <TriangleAlert size={11} className="text-red-400 shrink-0 mt-[3px]" /> },
  warn: { text: 'text-amber-400', icon: <AlertTriangle size={11} className="text-amber-400 shrink-0 mt-[3px]" /> },
  info: { text: 'text-sky-300', icon: <Info size={11} className="text-sky-300 shrink-0 mt-[3px]" /> },
  log: { text: 'text-neutral-300', icon: <Info size={11} className="text-neutral-500 shrink-0 mt-[3px]" /> },
}

const LivePreview: React.FC<LivePreviewProps> = ({
  source,
  files,
  title,
  reloadNonce = 0,
  debounceMs = 350,
  onErrorCountChange,
}) => {
  const { t } = useTranslation()
  const [doc, setDoc] = useState<{ html: string; token: string; nonce: number } | null>(null)
  const [logs, setLogs] = useState<PreviewLogEntry[]>([])
  const [isRefreshing, setIsRefreshing] = useState(false)

  const tokenRef = useRef('')
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const logIdRef = useRef(0)
  const firstRenderRef = useRef(true)
  const lastReloadNonceRef = useRef(reloadNonce)

  const fileKey = useMemo(() => previewFilesKey(files), [files])
  const filesRef = useRef(files)
  useEffect(() => {
    filesRef.current = files
  }, [files])

  // Debounced rebuild — the first paint is immediate so the preview is never
  // blank on open.
  useEffect(() => {
    const forcedReload = lastReloadNonceRef.current !== reloadNonce
    lastReloadNonceRef.current = reloadNonce
    const immediate = firstRenderRef.current || forcedReload
    firstRenderRef.current = false

    const build = () => {
      const token = `lh_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
      tokenRef.current = token
      setLogs([])
      setDoc({
        html: buildPreviewDocument(source, token, filesRef.current || []),
        token,
        nonce: reloadNonce,
      })
      setIsRefreshing(false)
    }

    // State changes stay in timer callbacks so this effect schedules the
    // preview synchronization without cascading a synchronous render.
    const refreshingTimer = immediate
      ? null
      : setTimeout(() => setIsRefreshing(true), 0)
    const buildTimer = setTimeout(build, immediate ? 0 : debounceMs)
    return () => {
      if (refreshingTimer) clearTimeout(refreshingTimer)
      clearTimeout(buildTimer)
    }
    // fileKey stands in for the (unstable) files array identity.
  }, [source, fileKey, reloadNonce, debounceMs])

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const message = normalizePreviewMessage(
        event.source,
        iframeRef.current?.contentWindow,
        event.data,
        tokenRef.current
      )
      if (!message) return
      setLogs((prev) => {
        const next = [
          ...prev,
          {
            id: logIdRef.current++,
            ...message,
          },
        ]
        return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next
      })
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  const errorCount = logs.filter((l) => l.level === 'error').length
  useEffect(() => {
    onErrorCountChange?.(errorCount)
  }, [errorCount, onErrorCountChange])

  const reload = useCallback(() => {
    const token = `lh_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
    tokenRef.current = token
    setLogs([])
    setDoc((prev) => ({
      html: buildPreviewDocument(source, token, filesRef.current || []),
      token,
      nonce: (prev?.nonce ?? 0) + 1,
    }))
  }, [source])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Browser chrome */}
      <div className="flex items-center gap-2 border-b border-neutral-200/60 bg-neutral-50 px-3 py-2 shrink-0">
        <div className="flex gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#ff5f57]" />
          <span className="w-2 h-2 rounded-full bg-[#febc2e]" />
          <span className="w-2 h-2 rounded-full bg-[#28c840]" />
        </div>
        <span className="text-[10px] font-mono text-neutral-400 ml-1 truncate">
          {isRefreshing ? t('code_playground.preview.updating') : t('code_playground.preview.label')}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {errorCount > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full leading-none bg-red-100 text-red-600">
              {errorCount}
            </span>
          )}
          <button
            onClick={reload}
            className="p-1 rounded-md text-neutral-400 hover:text-neutral-600 hover:bg-neutral-200/60 transition-colors"
            title={t('code_playground.preview.reload')}
            aria-label={t('code_playground.preview.reload')}
          >
            <RotateCcw size={12} />
          </button>
        </div>
      </div>

      {/* The frame itself. Opaque origin: no allow-same-origin, so it cannot
          reach the parent page, its cookies, or the session. */}
      <div className="flex-1 min-h-0 bg-white">
        {doc && (
          <iframe
            ref={iframeRef}
            key={`${doc.token}`}
            title={title}
            srcDoc={doc.html}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            className="h-full w-full border-0 bg-white"
          />
        )}
      </div>

      {/* Console — where student JS errors surface. */}
      <div className="shrink-0 border-t border-neutral-200/60 bg-[#1e1e2e]">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-white/5">
          <span className="text-[10px] font-mono uppercase tracking-wider text-neutral-500">
            {t('code_playground.preview.console')}
          </span>
          {errorCount > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full leading-none bg-red-500/20 text-red-400">
              {t('code_playground.preview.error_count', { count: errorCount })}
            </span>
          )}
          <button
            onClick={() => setLogs([])}
            className="ml-auto p-1 rounded-md text-neutral-500 hover:text-neutral-300 transition-colors"
            title={t('code_playground.preview.clear_console')}
            aria-label={t('code_playground.preview.clear_console')}
          >
            <Ban size={11} />
          </button>
        </div>
        <div className="max-h-[120px] min-h-[52px] overflow-y-auto px-3 py-2 space-y-1">
          {logs.length === 0 ? (
            <p className="text-[11px] font-mono text-neutral-600 italic">
              {t('code_playground.preview.console_empty')}
            </p>
          ) : (
            logs.map((entry) => {
              const style = LEVEL_STYLES[entry.level]
              return (
                <div key={entry.id} className="flex gap-1.5">
                  {style.icon}
                  <pre className={`text-[11px] font-mono whitespace-pre-wrap break-words leading-relaxed ${style.text}`}>
                    {entry.text}
                    {entry.line
                      ? ` (${t('code_playground.preview.line', { line: entry.line })}${entry.col ? `:${entry.col}` : ''})`
                      : ''}
                  </pre>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

export default LivePreview
