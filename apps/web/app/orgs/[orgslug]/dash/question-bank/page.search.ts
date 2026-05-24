import { Question } from '@phosphor-icons/react'
import type { SearchMeta } from '@/lib/dashboard-search/types'

export const searchMeta: SearchMeta = {
  id: 'dash.question_bank',
  titleKey: 'dashboard.question_bank.title',
  descriptionKey: 'dashboard.question_bank.subtitle',
  keywordsKey: 'dashboard.question_bank.keywords',
  icon: Question,
  href: '/dash/question-bank',
  group: 'navigation',
}

export default searchMeta
