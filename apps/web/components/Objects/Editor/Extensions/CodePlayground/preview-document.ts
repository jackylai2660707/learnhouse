export const MAX_PREVIEW_LOG_TEXT_LENGTH = 10000

export type NormalizedPreviewMessage = {
  level: 'log' | 'info' | 'warn' | 'error'
  text: string
  line: number | null
  col: number | null
}

export function normalizePreviewMessage(
  eventSource: unknown,
  expectedSource: unknown,
  data: unknown,
  token: string
): NormalizedPreviewMessage | null {
  if (!expectedSource || eventSource !== expectedSource) return null
  if (!data || typeof data !== 'object') return null

  const payload = data as Record<string, unknown>
  if (payload.__lhPreview !== token) return null

  const level = ['log', 'info', 'warn', 'error'].includes(String(payload.level))
    ? (payload.level as NormalizedPreviewMessage['level'])
    : 'log'
  return {
    level,
    text: String(payload.text ?? '').slice(0, MAX_PREVIEW_LOG_TEXT_LENGTH),
    line: typeof payload.line === 'number' ? payload.line : null,
    col: typeof payload.col === 'number' ? payload.col : null,
  }
}

export interface PreviewFile {
  name: string
  content: string
}

/** Stable dependency key for preview files without invisible delimiters. */
export function previewFilesKey(files: PreviewFile[] = []): string {
  return JSON.stringify(files.map(({ name, content }) => [name, content]))
}

/**
 * Everything below runs inside the sandboxed iframe.
 *
 * The frame uses `sandbox="allow-scripts"` without `allow-same-origin`, so it
 * receives an opaque origin. postMessage is the only reporting channel.
 */
function inlineScriptJson(value: unknown): string {
  return (JSON.stringify(value) ?? 'null')
    .replace(/</g, '\\u003c')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029')
}

function previewBootstrap(token: string, files: PreviewFile[]): string {
  const styles = files
    .filter((file) => /\.s?css$/i.test(file.name || ''))
    .map(({ name, content }) => ({ name, content: content || '' }))
  const scripts = files
    .filter((file) => /\.m?js$/i.test(file.name || ''))
    .map(({ name, content }) => ({ name, content: content || '' }))

  return `
(function () {
  var TOKEN = ${JSON.stringify(token)};
  var EXTRA_STYLES = ${inlineScriptJson(styles)};
  var EXTRA_SCRIPTS = ${inlineScriptJson(scripts)};
  function send(level, text, line, col) {
    try {
      parent.postMessage(
        { __lhPreview: TOKEN, level: level, text: String(text).slice(0, ${MAX_PREVIEW_LOG_TEXT_LENGTH}), line: line, col: col },
        '*'
      );
    } catch (e) {}
  }
  function fmt(args) {
    var out = [];
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      if (typeof a === 'string') { out.push(a); continue; }
      if (a instanceof Error) { out.push(a.stack || (a.name + ': ' + a.message)); continue; }
      try { out.push(JSON.stringify(a)); } catch (e) { out.push(String(a)); }
    }
    return out.join(' ');
  }
  var levels = ['log', 'info', 'warn', 'error'];
  for (var i = 0; i < levels.length; i++) {
    (function (level) {
      var original = console[level];
      console[level] = function () {
        send(level, fmt(arguments), null, null);
        if (original) { try { original.apply(console, arguments); } catch (e) {} }
      };
    })(levels[i]);
  }
  window.onerror = function (message, source, lineno, colno, error) {
    send('error', (error && error.stack) || message, lineno || null, colno || null);
    return false;
  };
  window.addEventListener('unhandledrejection', function (event) {
    var reason = event.reason;
    var text = reason && (reason.stack || reason.message) ? (reason.stack || reason.message) : String(reason);
    send('error', 'Unhandled promise rejection: ' + text, null, null);
  });
  window.addEventListener('error', function (event) {
    var target = event.target;
    if (target && target !== window && (target.src || target.href)) {
      send('warn', 'Failed to load ' + (target.src || target.href), null, null);
    }
  }, true);
  window.alert = function (value) { send('info', 'alert(' + fmt([value]) + ')', null, null); };
  window.confirm = function (value) { send('info', 'confirm(' + fmt([value]) + ') -> false', null, null); return false; };
  window.prompt = function (value) { send('info', 'prompt(' + fmt([value]) + ') -> null', null, null); return null; };
  window.addEventListener('DOMContentLoaded', function () {
    var head = document.head || document.documentElement;
    var body = document.body || document.documentElement;
    EXTRA_STYLES.forEach(function (file) {
      var style = document.createElement('style');
      style.dataset.lhFile = file.name;
      style.textContent = file.content;
      head.appendChild(style);
    });
    EXTRA_SCRIPTS.forEach(function (file) {
      var script = document.createElement('script');
      script.dataset.lhFile = file.name;
      script.textContent = file.content;
      body.appendChild(script);
    });
  }, { once: true });
})();
`
}

/** Compose the document handed to the opaque-origin preview iframe. */
export function buildPreviewDocument(
  source: string,
  token: string,
  files: PreviewFile[] = []
): string {
  const bootstrap = `<script>${previewBootstrap(token, files)}</script>`
  const doc = source || ''
  const doctype = /^\s*<!doctype[^>]*>/i.exec(doc)
  if (doctype) {
    const at = doctype.index + doctype[0].length
    return doc.slice(0, at) + bootstrap + doc.slice(at)
  }
  return bootstrap + doc
}
