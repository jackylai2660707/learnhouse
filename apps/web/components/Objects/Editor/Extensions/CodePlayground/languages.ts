import codeLanguageCapabilities from '../../../../../../../apps/api/config/code-language-capabilities.json' with { type: 'json' }

export interface PlaygroundLanguage {
  id: number // Judge0 language ID (or a preview-only pseudo id, see HTML_LANGUAGE_ID)
  name: string
  codemirrorLang: string // key used to resolve the CodeMirror language extension
  defaultCode: string
  /**
   * Rendered client-side in a sandboxed iframe instead of being executed.
   * Preview languages are never sent to the executor.
   */
  preview?: boolean
}

/**
 * Preview-only pseudo language.
 *
 * Judge0 has no HTML runtime, so this id is deliberately far above the Judge0
 * range (max real id is 90) — it can never collide with a runtime id, and the
 * API rejects it instead of forwarding it to the executor.
 */
export const HTML_LANGUAGE_ID = codeLanguageCapabilities.preview_language_ids[0]

/**
 * Judge0 ids our self-hosted executor can actually run. Everything else in
 * PLAYGROUND_LANGUAGES is authorable but will fail at execution time until the
 * executor image gains that runtime.
 */
export const EXECUTOR_LANGUAGE_IDS: readonly number[] =
  codeLanguageCapabilities.executor_language_ids

/** Raw runtimes plus API-owned adapters such as SQL-over-Python/SQLite. */
export const API_EXECUTION_LANGUAGE_IDS: readonly number[] = [
  ...EXECUTOR_LANGUAGE_IDS,
  ...codeLanguageCapabilities.api_adapters.map((adapter) => adapter.language_id),
]

export const HTML_DEFAULT_CODE = `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>My Page</title>
    <style>
      body {
        font-family: system-ui, sans-serif;
        padding: 2rem;
      }
      h1 {
        color: #0d9488;
      }
    </style>
  </head>
  <body>
    <h1>Hello!</h1>
    <p>Edit the code — the preview updates as you type.</p>

    <script>
      console.log('Preview is live')
    ${'</'}script>
  </body>
</html>
`

export const PLAYGROUND_LANGUAGES: PlaygroundLanguage[] = [
  {
    id: HTML_LANGUAGE_ID,
    name: 'HTML / CSS / JS',
    codemirrorLang: 'html',
    defaultCode: HTML_DEFAULT_CODE,
    preview: true,
  },
  {
    id: 71,
    name: 'Python 3',
    codemirrorLang: 'python',
    defaultCode: '# Write your code here\n',
  },
  {
    id: 63,
    name: 'JavaScript (Node)',
    codemirrorLang: 'javascript',
    defaultCode: '// Write your code here\n',
  },
  {
    id: 74,
    name: 'TypeScript',
    codemirrorLang: 'javascript',
    defaultCode: '// Write your code here\n',
  },
  {
    id: 62,
    name: 'Java',
    codemirrorLang: 'java',
    defaultCode:
      'import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n        // Write your code here\n    }\n}\n',
  },
  {
    id: 54,
    name: 'C++',
    codemirrorLang: 'cpp',
    defaultCode:
      '#include <iostream>\nusing namespace std;\n\nint main() {\n    // Write your code here\n    return 0;\n}\n',
  },
  {
    id: 50,
    name: 'C',
    codemirrorLang: 'cpp',
    defaultCode:
      '#include <stdio.h>\n\nint main() {\n    // Write your code here\n    return 0;\n}\n',
  },
  {
    id: 73,
    name: 'Rust',
    codemirrorLang: 'rust',
    defaultCode: 'fn main() {\n    // Write your code here\n}\n',
  },
  {
    id: 60,
    name: 'Go',
    codemirrorLang: 'go',
    defaultCode:
      'package main\n\nimport "fmt"\n\nfunc main() {\n    // Write your code here\n    fmt.Println("Hello")\n}\n',
  },
  {
    id: 68,
    name: 'PHP',
    codemirrorLang: 'php',
    defaultCode: '<?php\n// Write your code here\n',
  },
  {
    id: 72,
    name: 'Ruby',
    codemirrorLang: 'python', // closest syntax highlighting
    defaultCode: '# Write your code here\n',
  },
  {
    id: 78,
    name: 'Kotlin',
    codemirrorLang: 'java',
    defaultCode: 'fun main() {\n    // Write your code here\n}\n',
  },
  {
    id: 51,
    name: 'C#',
    codemirrorLang: 'java',
    defaultCode:
      'using System;\n\nclass Program {\n    static void Main() {\n        // Write your code here\n    }\n}\n',
  },
  {
    id: 83,
    name: 'Swift',
    codemirrorLang: 'javascript',
    defaultCode: 'import Foundation\n\n// Write your code here\n',
  },
  {
    id: 81,
    name: 'Scala',
    codemirrorLang: 'java',
    defaultCode:
      'object Main extends App {\n    // Write your code here\n}\n',
  },
  {
    id: 85,
    name: 'Perl',
    codemirrorLang: 'perl',
    defaultCode: '#!/usr/bin/perl\nuse strict;\nuse warnings;\n\n# Write your code here\n',
  },
  {
    id: 80,
    name: 'R',
    codemirrorLang: 'r',
    defaultCode: '# Write your code here\n',
  },
  {
    id: 90,
    name: 'Dart',
    codemirrorLang: 'javascript',
    defaultCode: 'void main() {\n  // Write your code here\n}\n',
  },
  {
    id: 61,
    name: 'Haskell',
    codemirrorLang: 'haskell',
    defaultCode: 'main :: IO ()\nmain = do\n    -- Write your code here\n    putStrLn "Hello"\n',
  },
  {
    id: 64,
    name: 'Lua',
    codemirrorLang: 'lua',
    defaultCode: '-- Write your code here\n',
  },
  {
    id: 57,
    name: 'Elixir',
    codemirrorLang: 'python',
    defaultCode: '# Write your code here\n',
  },
  {
    id: 86,
    name: 'Clojure',
    codemirrorLang: 'clojure',
    defaultCode: '(defn -main []\n  ;; Write your code here\n  (println "Hello"))\n',
  },
  {
    id: 82,
    name: 'SQL',
    codemirrorLang: 'sql',
    defaultCode: '-- Write your query here\nSELECT 1;\n',
  },
  {
    id: 46,
    name: 'Bash',
    codemirrorLang: 'shell',
    defaultCode: '#!/bin/bash\n\n# Write your code here\n',
  },
  {
    id: 79,
    name: 'Objective-C',
    codemirrorLang: 'cpp',
    defaultCode:
      '#import <Foundation/Foundation.h>\n\nint main(int argc, const char * argv[]) {\n    @autoreleasepool {\n        // Write your code here\n    }\n    return 0;\n}\n',
  },
  {
    // Judge0 67 is Pascal (FPC 3.0.0). 77 is COBOL — it was wrong here before.
    id: 67,
    name: 'Pascal',
    codemirrorLang: 'pascal',
    defaultCode:
      'program Main;\nbegin\n    { Write your code here }\n    writeln(\'Hello\');\nend.\n',
  },
  {
    id: 59,
    name: 'Fortran',
    codemirrorLang: 'fortran',
    defaultCode:
      'program main\n    implicit none\n    ! Write your code here\n    print *, "Hello"\nend program main\n',
  },
  {
    id: 69,
    name: 'Prolog',
    codemirrorLang: 'javascript',
    defaultCode: '% Write your code here\n:- initialization(main).\nmain :- write(hello), nl.\n',
  },
  {
    id: 55,
    name: 'Common Lisp',
    codemirrorLang: 'clojure',
    defaultCode: ';;; Write your code here\n(format t "Hello~%")\n',
  },
  {
    id: 91,
    name: 'PowerShell',
    codemirrorLang: 'powershell',
    defaultCode: '# Write your code here\nWrite-Host "Hello"\n',
  },
  {
    id: 45,
    name: 'Assembly (NASM)',
    codemirrorLang: 'javascript',
    defaultCode:
      'section .data\n    msg db "Hello", 10\n    len equ $ - msg\n\nsection .text\n    global _start\n\n_start:\n    mov rax, 1\n    mov rdi, 1\n    mov rsi, msg\n    mov rdx, len\n    syscall\n\n    mov rax, 60\n    xor rdi, rdi\n    syscall\n',
  },
]

export function getLanguageById(id: number): PlaygroundLanguage | undefined {
  return PLAYGROUND_LANGUAGES.find((l) => l.id === id)
}

/**
 * A persisted name is display metadata only. When the language id is known,
 * the capability-backed picker definition is the authoritative label.
 *
 * Older Tiptap content can omit `languageName`; its schema default then says
 * "Python 3" even when the durable language id is JavaScript/Node.
 */
export function getLanguageDisplayName(id: number, persistedName?: string): string {
  return getLanguageById(id)?.name ?? (persistedName?.trim() || `ID ${id}`)
}

/** True when the language renders in the browser instead of running on the executor. */
export function isPreviewLanguage(id: number): boolean {
  return getLanguageById(id)?.preview === true
}

/** True when the self-hosted executor has a runtime for this Judge0 id. */
export function isExecutorLanguage(id: number): boolean {
  return EXECUTOR_LANGUAGE_IDS.includes(id)
}

/** True when the API can execute the id directly or through a declared adapter. */
export function isApiExecutionLanguage(id: number): boolean {
  return API_EXECUTION_LANGUAGE_IDS.includes(id)
}

/** Adapter prerequisites declared by the shared capability manifest. */
export function getApiAdapterRequirements(id: number): readonly string[] {
  return codeLanguageCapabilities.api_adapters.find(
    (adapter) => adapter.language_id === id
  )?.requires ?? []
}

/** Suffix for languages the executor cannot run yet. */
export const COMING_SOON_LABEL = '即將支持'

/** Suffix for preview languages shown on a surface that only runs server-side. */
export const PREVIEW_ONLY_LABEL = '僅瀏覽器預覽'

export interface LanguageOptionState {
  /** The picker must not let a student select this. */
  disabled: boolean
  /** Short suffix explaining why, or '' when the language is fully usable. */
  note: string
}

/**
 * Single source of truth for how a language renders in a picker.
 *
 * Unsupported languages stay VISIBLE but disabled, so an author never picks a
 * runtime that would fail at execution time.
 *
 * Capability flags distinguish the two surfaces:
 *  - CodePlayground renders HTML and owns the SQLite upload/path UI required
 *    by the SQL API adapter;
 *  - assignment CODE tasks expose only raw executor runtimes, because they
 *    have neither browser preview nor adapter-required SQLite files.
 */
export function getLanguageOptionState(
  id: number,
  opts: { previewSupported: boolean; apiAdaptersSupported: boolean }
): LanguageOptionState {
  if (isPreviewLanguage(id)) {
    return opts.previewSupported
      ? { disabled: false, note: '' }
      : { disabled: true, note: PREVIEW_ONLY_LABEL }
  }
  return (isExecutorLanguage(id) || (opts.apiAdaptersSupported && isApiExecutionLanguage(id)))
    ? { disabled: false, note: '' }
    : { disabled: true, note: COMING_SOON_LABEL }
}
