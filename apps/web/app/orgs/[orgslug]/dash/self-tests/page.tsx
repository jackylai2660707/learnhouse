import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { Metadata } from 'next'
import SelfTestsDashboardClient from './self-tests-dashboard-client'

type MetadataProps = {
  params: Promise<{ orgslug: string }>
}

export async function generateMetadata(props: MetadataProps): Promise<Metadata> {
  const params = await props.params
  const org = await getOrganizationContextInfo(params.orgslug, {
    revalidate: 120,
    tags: ['organizations'],
  })

  return {
    title: 'Self-test Records - ' + org.name,
    robots: {
      index: false,
      follow: false,
    },
  }
}

async function SelfTestsDashboardPage(props: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await props.params
  const org = await getOrganizationContextInfo(orgslug, {
    revalidate: 120,
    tags: ['organizations'],
  })

  return <SelfTestsDashboardClient org_id={org.id} orgslug={orgslug} />
}

export default SelfTestsDashboardPage
