'use client'
import React from 'react'
import { ArrowRight, ArrowUpRight } from 'lucide-react'
import { useOrg } from '@components/Contexts/OrgContext'
import {
  hubProductAccentClass,
  hubProductIcon,
  resolveHubProducts,
  type HubProduct,
} from '@/lib/hub-products'

/**
 * Product hub tiles on the student home page.
 *
 * The list is data, not JSX — see apps/web/lib/hub-products.ts for the shipped
 * defaults and the org-config override (`customization.hub_products`).
 */
function ProductHubGrid() {
  const org = useOrg() as any
  const hub = resolveHubProducts(org)

  if (!hub.enabled || hub.items.length === 0) return null

  return (
    <section
      aria-labelledby="product-hub-title"
      className="mb-6 rounded-xl border border-gray-100 bg-white px-4 py-4 nice-shadow"
    >
      <div className="space-y-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-gray-400">學習中心</p>
          <h2 id="product-hub-title" className="mt-1 text-xl font-black text-gray-950">
            {hub.title}
          </h2>
          <p className="mt-1 text-sm text-gray-500">{hub.subtitle}</p>
        </div>

        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {hub.items.map((product) => (
            <li key={product.id} className="flex">
              <ProductHubTile product={product} />
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function ProductHubTile({ product }: { product: HubProduct }) {
  const Icon = hubProductIcon(product.icon)
  const opensNewTab = product.open_in_new_tab !== false

  return (
    <a
      href={product.url}
      {...(opensNewTab ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      className="group flex w-full min-h-24 flex-col justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-3 text-gray-800 transition-colors hover:border-gray-200 hover:bg-white focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-gray-950 focus-visible:ring-offset-2"
    >
      <div className="flex min-w-0 items-start gap-3">
        <span
          className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${hubProductAccentClass(product.accent)}`}
        >
          <Icon size={18} aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-black">{product.name}</p>
            {product.badge && (
              <span
                className={`rounded-full border px-2 py-0.5 text-[10px] font-black ${hubProductAccentClass(product.accent)}`}
              >
                {product.badge}
              </span>
            )}
          </div>
          {product.description && (
            <p className="mt-1 text-xs leading-snug text-gray-500">{product.description}</p>
          )}
        </div>
      </div>
      <span className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-bold text-gray-400">
          {opensNewTab ? '在新分頁開啟' : '前往'}
        </span>
        <span className="flex shrink-0 items-center gap-1.5 rounded-lg bg-gray-950 px-3 py-1.5 text-xs font-black text-white">
          開始
          {opensNewTab ? <ArrowUpRight size={13} aria-hidden="true" /> : <ArrowRight size={13} aria-hidden="true" />}
        </span>
      </span>
    </a>
  )
}

export default ProductHubGrid
