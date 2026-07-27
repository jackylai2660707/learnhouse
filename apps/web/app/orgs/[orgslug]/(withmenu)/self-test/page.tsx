import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { Metadata } from 'next'
import SelfTestClient from './self-test-client'

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
    title: '自我練習 - ' + org.name,
    robots: {
      index: false,
      follow: false,
    },
  }
}

async function SelfTestPage(props: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await props.params
  const org = await getOrganizationContextInfo(orgslug, {
    revalidate: 120,
    tags: ['organizations'],
  })

  return <SelfTestClient org_id={org.id} orgslug={orgslug} />
}

export default SelfTestPage
