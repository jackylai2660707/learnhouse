import { ClipboardText } from '@phosphor-icons/react'
import type { SearchMeta } from '@/lib/dashboard-search/types'

export const searchMeta: SearchMeta = {
  id: 'dash.self_tests',
  titleKey: 'dashboard.self_tests.title',
  descriptionKey: 'dashboard.self_tests.subtitle',
  keywordsKey: 'dashboard.self_tests.keywords',
  icon: ClipboardText,
  href: '/dash/self-tests',
  group: 'navigation',
}

export default searchMeta
