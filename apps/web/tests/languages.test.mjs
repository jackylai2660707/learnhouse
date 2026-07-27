import assert from 'node:assert/strict'
import test from 'node:test'

import codeLanguageCapabilities from '../../api/config/code-language-capabilities.json' with { type: 'json' }

import {
  COMING_SOON_LABEL,
  API_EXECUTION_LANGUAGE_IDS,
  EXECUTOR_LANGUAGE_IDS,
  HTML_LANGUAGE_ID,
  PLAYGROUND_LANGUAGES,
  getApiAdapterRequirements,
  getLanguageDisplayName,
  getLanguageOptionState,
} from '../components/Objects/Editor/Extensions/CodePlayground/languages.ts'

test('preview and executor languages expose correct availability', () => {
  assert.equal(HTML_LANGUAGE_ID, 1000)
  assert.deepEqual(EXECUTOR_LANGUAGE_IDS, [50, 54, 62, 63, 71, 74])
  assert.deepEqual(API_EXECUTION_LANGUAGE_IDS, [50, 54, 62, 63, 71, 74, 82])
  assert.deepEqual(getLanguageOptionState(HTML_LANGUAGE_ID, {
    previewSupported: true,
    apiAdaptersSupported: true,
  }), {
    disabled: false,
    note: '',
  })
  assert.equal(getLanguageOptionState(HTML_LANGUAGE_ID, {
    previewSupported: false,
    apiAdaptersSupported: false,
  }).disabled, true)

  for (const id of EXECUTOR_LANGUAGE_IDS) {
    assert.deepEqual(getLanguageOptionState(id, {
      previewSupported: false,
      apiAdaptersSupported: false,
    }), {
      disabled: false,
      note: '',
    })
  }
  assert.deepEqual(getLanguageOptionState(82, {
    previewSupported: false,
    apiAdaptersSupported: true,
  }), {
    disabled: false,
    note: '',
  })
  assert.deepEqual(getLanguageOptionState(82, {
    previewSupported: false,
    apiAdaptersSupported: false,
  }), {
    disabled: true,
    note: COMING_SOON_LABEL,
  })
  assert.deepEqual(getApiAdapterRequirements(82), ['sqlite_db_path'])
  assert.deepEqual(getApiAdapterRequirements(71), [])
})

test('unsupported runtimes remain visible and disabled', () => {
  const rust = PLAYGROUND_LANGUAGES.find((language) => language.id === 73)
  const pascal = PLAYGROUND_LANGUAGES.find((language) => language.codemirrorLang === 'pascal')

  assert.ok(rust)
  assert.deepEqual(getLanguageOptionState(rust.id, {
    previewSupported: true,
    apiAdaptersSupported: true,
  }), {
    disabled: true,
    note: COMING_SOON_LABEL,
  })
  assert.equal(COMING_SOON_LABEL, '即將支持')
  assert.equal(pascal?.id, 67)
  assert.equal(PLAYGROUND_LANGUAGES.some((language) => language.id === 77), false)
})

test('canonical language ids override missing or stale persisted display labels', () => {
  const node = PLAYGROUND_LANGUAGES.find((language) => language.id === 63)
  assert.ok(node)

  // Tiptap fills a missing attribute with its historical Python default.
  assert.equal(getLanguageDisplayName(63), 'JavaScript (Node)')
  assert.equal(getLanguageDisplayName(63, 'Python 3'), 'JavaScript (Node)')
  assert.deepEqual(getLanguageOptionState(63, {
    previewSupported: true,
    apiAdaptersSupported: true,
  }), {
    disabled: false,
    note: '',
  })

  const rust = PLAYGROUND_LANGUAGES.find((language) => language.id === 73)
  assert.ok(rust)
  assert.equal(getLanguageDisplayName(rust.id, 'Python 3'), rust.name)
  assert.deepEqual(getLanguageOptionState(rust.id, {
    previewSupported: true,
    apiAdaptersSupported: true,
  }), {
    disabled: true,
    note: COMING_SOON_LABEL,
  })
})

test('frontend capabilities come from the machine-readable shared manifest', () => {
  assert.deepEqual(EXECUTOR_LANGUAGE_IDS, codeLanguageCapabilities.executor_language_ids)
  assert.deepEqual(
    API_EXECUTION_LANGUAGE_IDS,
    [
      ...codeLanguageCapabilities.executor_language_ids,
      ...codeLanguageCapabilities.api_adapters.map((adapter) => adapter.language_id),
    ]
  )
  assert.deepEqual(codeLanguageCapabilities.api_adapters, [{
    language_id: 82,
    name: 'SQL (SQLite)',
    executor_language_id: 71,
    requires: ['sqlite_db_path'],
  }])
})
