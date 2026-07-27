import fs from 'node:fs'

const INITIAL_PASSWORD_PATTERN = /^\s*LEARNHOUSE_INITIAL_ADMIN_PASSWORD\s*=/
const BOOTSTRAP_FLAG_PATTERN = /^\s*LEARNHOUSE_BOOTSTRAP_ADMIN\s*=/

export function finalizeBootstrapEnv(filePath: string): void {
  const lines = fs.readFileSync(filePath, 'utf-8').split(/\r?\n/)
  let bootstrapFlagFound = false
  const sanitized = lines.flatMap((line) => {
    if (INITIAL_PASSWORD_PATTERN.test(line)) return []
    if (BOOTSTRAP_FLAG_PATTERN.test(line)) {
      bootstrapFlagFound = true
      return ['LEARNHOUSE_BOOTSTRAP_ADMIN=False']
    }
    return [line]
  })

  if (!bootstrapFlagFound) {
    sanitized.push('LEARNHOUSE_BOOTSTRAP_ADMIN=False')
  }

  const content = `${sanitized.join('\n').replace(/\n+$/, '')}\n`
  fs.writeFileSync(filePath, content)
  fs.chmodSync(filePath, 0o600)
}
