import { redirect } from 'next/navigation'
import { getServerSession } from '@/lib/auth/server'
import { getUriWithOrg } from '@services/config/config'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import PDFCourseBuildClient from './client'

interface PDFBuildGuardSession {
  user?: { is_superadmin?: boolean } | undefined
  roles?: Array<{
    org?: { id?: number }
    role?: { rights?: { courses?: { action_create?: boolean } } }
  }> | undefined
}

interface PDFBuildGuardOrg {
  id?: number
  config?: { config?: { resolved_features?: { ai?: { enabled?: boolean } } } }
}

export function canAccessPDFCourseBuild(
  session: PDFBuildGuardSession | null,
  org: PDFBuildGuardOrg | null,
) {
  if (!session?.user || !org?.id) return false
  if (org.config?.config?.resolved_features?.ai?.enabled !== true) return false
  if (session.user.is_superadmin === true) return true

  return session.roles?.some((membership) => (
    membership.org?.id === org.id && membership.role?.rights?.courses?.action_create === true
  )) ?? false
}

export default async function PDFCourseBuildPage({
  params,
}: {
  params: Promise<{ orgslug: string }>
}) {
  const { orgslug } = await params
  const session = await getServerSession()
  if (!session?.tokens?.access_token) {
    redirect(getUriWithOrg(orgslug, '/login'))
  }

  const org = await getOrganizationContextInfo(
    orgslug,
    { cache: 'no-store' },
    session.tokens.access_token,
  )
  if (!canAccessPDFCourseBuild(session as PDFBuildGuardSession, org as PDFBuildGuardOrg)) {
    redirect(getUriWithOrg(orgslug, '/dash/courses'))
  }

  return <PDFCourseBuildClient orgslug={orgslug} />
}
