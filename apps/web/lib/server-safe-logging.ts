export function safeErrorType(error: unknown): string {
  if (error instanceof Error) {
    return error.name || 'Error'
  }
  return typeof error
}
