import { ClipboardText } from '@phosphor-icons/react'
import type { SearchMeta } from '@/lib/dashboard-search/types'

export const searchMeta: SearchMeta = {
  id: 'dash.gradebook',
  titleKey: 'dashboard.gradebook.title',
  descriptionKey: 'dashboard.gradebook.subtitle',
  keywordsKey: 'dashboard.gradebook.keywords',
  href: '/dash/gradebook',
  icon: ClipboardText,
  group: 'navigation',
}

export default searchMeta
