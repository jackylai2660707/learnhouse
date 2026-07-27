import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { Metadata } from 'next'
import QuestionBankClient from './question-bank-client'

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
    title: '校本題庫 - ' + org.name,
    robots: {
      index: false,
      follow: false,
    },
  }
}

async function QuestionBankPage(props: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await props.params
  const org = await getOrganizationContextInfo(orgslug, {
    revalidate: 120,
    tags: ['organizations'],
  })

  return <QuestionBankClient org_id={org.id} orgslug={orgslug} />
}

export default QuestionBankPage
