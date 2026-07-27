import { Metadata } from 'next'
import { OrgProvider } from '@components/Contexts/OrgContext'
import OrgLanguageSync from '@components/Contexts/OrgLanguageSync'
import NextTopLoader from 'nextjs-toploader'
import Toast from '@components/Objects/StyledElements/Toast/Toast'
import '@styles/globals.css'
import Footer from '@components/Footer/Footer'

const API_URL = `${(
  process.env.NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL
  || process.env.LEARNHOUSE_BACKEND_URL
  || 'http://localhost:1338'
).replace(/\/+$/, '')}/api/v1/`
const MEDIA_URL = (
  process.env.NEXT_PUBLIC_LEARNHOUSE_MEDIA_URL
  || process.env.NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL
  || process.env.LEARNHOUSE_BACKEND_URL
  || 'http://localhost:1338'
).replace(/\/+$/, '')

export async function generateMetadata({
  params,
}: {
  params: Promise<{ orgslug: string }>
}): Promise<Metadata> {
  const { orgslug } = await params
  try {
    const org = await fetchOrgContext(orgslug, {
      revalidate: 86400,
      tags: ['organizations'],
    })
    const faviconImage = org?.config?.config?.customization?.general?.favicon_image || org?.config?.config?.general?.favicon_image
    if (faviconImage) {
      return {
        icons: { icon: `${MEDIA_URL}/content/orgs/${org.org_uuid}/favicons/${faviconImage}` },
      }
    }
  } catch {}
  return {}
}

async function fetchOrgContext(orgslug: string, next: NextFetchRequestConfig) {
  const response = await fetch(`${API_URL}orgs/slug/${encodeURIComponent(orgslug)}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
    next,
  })
  if (!response.ok) {
    throw new Error(`Organization metadata request failed: ${response.status}`)
  }
  return response.json()
}

export default async function RootLayout(props: {
  children: React.ReactNode
  params: Promise<{ orgslug: string }>
}) {
  const params = await props.params

  return (
    <div>
      <OrgProvider orgslug={params.orgslug}>
        <OrgLanguageSync />
        <NextTopLoader color="#2e2e2e" initialPosition={0.3} height={4} easing={'ease'} speed={500} showSpinner={false} />
        <Toast />
        {props.children}
        <Footer />
      </OrgProvider>
    </div>
  )
}
