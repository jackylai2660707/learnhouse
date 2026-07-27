import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
export const revalidate = 0

const BACKEND_URL = (
  process.env.NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL
  || process.env.LEARNHOUSE_BACKEND_URL
  || 'http://localhost:1338'
).replace(/\/+$/, '')

export async function GET() {
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1/health/ready`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(5_000),
    })
    const body = await response.json().catch(() => null)
    if (!body) {
      return NextResponse.json(
        { status: 'not_ready', service: 'learnhouse-web', code: 'backend_not_ready' },
        { status: 503 }
      )
    }
    return NextResponse.json(body, { status: response.ok ? 200 : 503 })
  } catch {
    return NextResponse.json(
      { status: 'not_ready', service: 'learnhouse-web', code: 'backend_unavailable' },
      { status: 503 }
    )
  }
}
