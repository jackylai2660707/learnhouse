import {
  Blocks,
  Boxes,
  Brain,
  Calculator,
  Code2,
  Compass,
  FlaskConical,
  Gamepad2,
  GraduationCap,
  Keyboard,
  Languages,
  LayoutGrid,
  Mic,
  Music,
  Palette,
  PenLine,
  Sparkles,
  Trophy,
  type LucideIcon,
} from 'lucide-react'

/**
 * Product hub configuration.
 *
 * LearnHouse is the central hub: the student home page shows a tile for each of
 * the owner's other learning products so one login page is the entry point to
 * everything.
 *
 * Adding a product is a small data edit, in one of two places:
 *
 *  1. `DEFAULT_HUB_PRODUCTS` below — ships with the build, works for every org.
 *  2. Org config JSON (no migration, no redeploy) — set
 *     `customization.hub_products` on the `organizationconfig` row. When that
 *     object carries at least one item it fully replaces the defaults, so the
 *     owner can retitle, reorder, hide or add products per organization.
 *
 * Org config shape (mirrors `HubProductsConfig` in
 * apps/api/src/db/organization_config.py):
 *
 * ```json
 * "hub_products": {
 *   "enabled": true,
 *   "title": "其他學習平台",
 *   "subtitle": "同一個帳號，一站進入所有練習平台。",
 *   "items": [
 *     {
 *       "id": "typing",
 *       "name": "打字學習平台",
 *       "description": "拼音、倉頡、速成同 English 打字練習同測驗。",
 *       "url": "https://type.armjp.yueseng-ys.com",
 *       "icon": "keyboard",
 *       "accent": "amber",
 *       "badge": "打字",
 *       "enabled": true,
 *       "open_in_new_tab": true
 *     }
 *   ]
 * }
 * ```
 */

export type HubProductAccent =
  | 'amber'
  | 'cyan'
  | 'blue'
  | 'emerald'
  | 'rose'
  | 'violet'
  | 'slate'

export type HubProductIconName =
  | 'keyboard'
  | 'languages'
  | 'sparkles'
  | 'gamepad'
  | 'graduation'
  | 'code'
  | 'grid'
  | 'compass'
  | 'boxes'
  | 'mic'
  | 'pen'
  | 'blocks'
  | 'calculator'
  | 'flask'
  | 'music'
  | 'palette'
  | 'trophy'
  | 'brain'

export interface HubProduct {
  /** Stable key, also used as the React list key. */
  id: string
  name: string
  /** One short sentence — what a student does there. */
  description: string
  /** Absolute public URL (must be reachable from a student's browser). */
  url: string
  icon: HubProductIconName
  accent: HubProductAccent
  /** Optional short pill rendered next to the name. */
  badge?: string
  /** Set to false to keep the entry but hide the tile. */
  enabled?: boolean
  /** External products open in a new tab by default. */
  open_in_new_tab?: boolean
}

export interface HubProductsConfig {
  enabled: boolean
  title: string
  subtitle: string
  items: HubProduct[]
}

const HUB_PRODUCT_ICONS: Record<HubProductIconName, LucideIcon> = {
  keyboard: Keyboard,
  languages: Languages,
  sparkles: Sparkles,
  gamepad: Gamepad2,
  graduation: GraduationCap,
  code: Code2,
  grid: LayoutGrid,
  compass: Compass,
  boxes: Boxes,
  mic: Mic,
  pen: PenLine,
  blocks: Blocks,
  calculator: Calculator,
  flask: FlaskConical,
  music: Music,
  palette: Palette,
  trophy: Trophy,
  brain: Brain,
}

const HUB_PRODUCT_ACCENTS: Record<HubProductAccent, string> = {
  amber: 'border-amber-200 bg-amber-50 text-amber-800',
  cyan: 'border-cyan-200 bg-cyan-50 text-cyan-800',
  blue: 'border-blue-200 bg-blue-50 text-blue-800',
  emerald: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  rose: 'border-rose-200 bg-rose-50 text-rose-800',
  violet: 'border-violet-200 bg-violet-50 text-violet-800',
  slate: 'border-gray-200 bg-gray-50 text-gray-700',
}

export function hubProductIcon(icon: HubProductIconName | string): LucideIcon {
  return HUB_PRODUCT_ICONS[icon as HubProductIconName] || Boxes
}

export function hubProductAccentClass(accent: HubProductAccent | string): string {
  return HUB_PRODUCT_ACCENTS[accent as HubProductAccent] || HUB_PRODUCT_ACCENTS.slate
}

export const DEFAULT_HUB_PRODUCTS_TITLE = '其他學習平台'
export const DEFAULT_HUB_PRODUCTS_SUBTITLE =
  '同一個學習中心，按一下就可以進入其他練習平台。'

/**
 * ---------------------------------------------------------------------------
 * ADD A NEW PRODUCT HERE (public URLs come from the Caddy site configs).
 * ---------------------------------------------------------------------------
 */
export const DEFAULT_HUB_PRODUCTS: HubProduct[] = [
  {
    id: 'typing',
    name: '黎sir打字學習平台',
    description: '拼音、倉頡、速成同 English 打字：學習、練習、限時測驗同打字遊戲。',
    url: 'https://type.armjp.yueseng-ys.com',
    icon: 'keyboard',
    accent: 'amber',
    badge: '打字',
  },
  {
    id: 'tenlingo',
    name: 'Tenlingo 英語學習',
    description: '英文作業、詞彙 SRS 複習、錯題修復同朗讀作文練習。',
    url: 'https://english.armjp.yueseng-ys.com',
    icon: 'languages',
    accent: 'blue',
    badge: '英語',
  },
]

function normalizeProduct(raw: any, index: number): HubProduct | null {
  if (!raw || typeof raw !== 'object') return null
  const url = typeof raw.url === 'string' ? raw.url.trim() : ''
  const name = typeof raw.name === 'string' ? raw.name.trim() : ''
  if (!url || !name) return null
  if (raw.enabled === false) return null

  return {
    id: String(raw.id || `product-${index}`),
    name,
    description: typeof raw.description === 'string' ? raw.description : '',
    url,
    icon: (raw.icon || 'boxes') as HubProductIconName,
    accent: (raw.accent || 'slate') as HubProductAccent,
    badge: typeof raw.badge === 'string' && raw.badge.trim() ? raw.badge.trim() : undefined,
    enabled: true,
    open_in_new_tab: raw.open_in_new_tab !== false,
  }
}

/**
 * Resolve the tile list for an org: org config wins when it supplies items,
 * otherwise the shipped defaults are used.
 */
export function resolveHubProducts(org: any): HubProductsConfig {
  const raw =
    org?.config?.config?.customization?.hub_products ||
    org?.config?.config?.hub_products ||
    null

  const configuredItems = Array.isArray(raw?.items)
    ? (raw.items.map(normalizeProduct).filter(Boolean) as HubProduct[])
    : []

  const items = configuredItems.length > 0
    ? configuredItems
    : DEFAULT_HUB_PRODUCTS.filter((product) => product.enabled !== false).map((product) => ({
        ...product,
        open_in_new_tab: product.open_in_new_tab !== false,
      }))

  return {
    enabled: raw?.enabled !== false,
    title: (typeof raw?.title === 'string' && raw.title.trim()) || DEFAULT_HUB_PRODUCTS_TITLE,
    subtitle:
      (typeof raw?.subtitle === 'string' && raw.subtitle.trim()) || DEFAULT_HUB_PRODUCTS_SUBTITLE,
    items,
  }
}
