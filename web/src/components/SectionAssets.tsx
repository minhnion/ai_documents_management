import { useEffect, useMemo, useState } from 'react'
import { Image as ImageIcon, Table2, X } from 'lucide-react'
import { api } from '../lib/api'

interface LandingAsset {
  id: string
  type: string
  image_url: string
}

interface SectionAssetsProps {
  assets: unknown
}

const TABLE_TYPES = new Set(['table'])
const FIGURE_TYPES = new Set(['figure', 'diagram', 'chart', 'image', 'logo'])

function normalizeAssets(raw: unknown): LandingAsset[] {
  if (!Array.isArray(raw)) return []
  const out: LandingAsset[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue
    const candidate = entry as Record<string, unknown>
    const id = candidate.id
    const url = candidate.image_url
    const type = candidate.type
    if (typeof id !== 'string' || !id) continue
    if (typeof url !== 'string' || !url) continue
    out.push({
      id,
      type: typeof type === 'string' ? type : 'asset',
      image_url: url,
    })
  }
  return out
}

function assetLabel(type: string): string {
  if (TABLE_TYPES.has(type)) return 'Bảng biểu'
  if (FIGURE_TYPES.has(type)) return 'Hình ảnh'
  return type ? type.charAt(0).toUpperCase() + type.slice(1) : 'Tài liệu'
}

function AssetIcon({ type }: { type: string }) {
  if (TABLE_TYPES.has(type)) return <Table2 size={14} />
  return <ImageIcon size={14} />
}

function useAssetBlobUrl(path: string | null): {
  url: string | null
  status: 'idle' | 'loading' | 'ready' | 'error'
} {
  const [state, setState] = useState<{
    url: string | null
    status: 'idle' | 'loading' | 'ready' | 'error'
  }>({ url: null, status: 'idle' })

  useEffect(() => {
    if (!path) {
      setState({ url: null, status: 'idle' })
      return
    }
    let cancelled = false
    let createdUrl: string | null = null
    setState({ url: null, status: 'loading' })

    void (async () => {
      try {
        const response = await api.get(path, { responseType: 'blob' })
        if (cancelled) return
        const objectUrl = URL.createObjectURL(response.data as Blob)
        createdUrl = objectUrl
        setState({ url: objectUrl, status: 'ready' })
      } catch {
        if (!cancelled) {
          setState({ url: null, status: 'error' })
        }
      }
    })()

    return () => {
      cancelled = true
      if (createdUrl) {
        URL.revokeObjectURL(createdUrl)
      }
    }
  }, [path])

  return state
}

function AssetThumbnail({
  asset,
  index,
  onOpen,
}: {
  asset: LandingAsset
  index: number
  onOpen: () => void
}) {
  const { url, status } = useAssetBlobUrl(asset.image_url)
  const label = `${assetLabel(asset.type)} ${index + 1}`

  return (
    <button
      type="button"
      className={`section-asset-thumb section-asset-thumb--${asset.type}`}
      onClick={onOpen}
      title={label}
    >
      <span className="section-asset-thumb-tag">
        <AssetIcon type={asset.type} />
        {label}
      </span>
      <span className="section-asset-thumb-frame">
        {status === 'ready' && url ? (
          <img src={url} alt={label} loading="lazy" />
        ) : status === 'error' ? (
          <span className="section-asset-thumb-error">Không tải được</span>
        ) : (
          <span className="section-asset-thumb-spinner" aria-hidden />
        )}
      </span>
    </button>
  )
}

function AssetLightbox({
  asset,
  index,
  total,
  onClose,
}: {
  asset: LandingAsset
  index: number
  total: number
  onClose: () => void
}) {
  const { url, status } = useAssetBlobUrl(asset.image_url)
  const label = `${assetLabel(asset.type)} ${index + 1}/${total}`

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      className="section-asset-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onClick={onClose}
    >
      <div
        className="section-asset-lightbox-body"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-asset-lightbox-header">
          <span>{label}</span>
          <button
            type="button"
            className="section-asset-lightbox-close"
            onClick={onClose}
            aria-label="Đóng"
          >
            <X size={18} />
          </button>
        </div>
        <div className="section-asset-lightbox-image">
          {status === 'ready' && url ? (
            <img src={url} alt={label} />
          ) : status === 'error' ? (
            <span className="section-asset-thumb-error">Không tải được hình.</span>
          ) : (
            <span className="section-asset-thumb-spinner" aria-hidden />
          )}
        </div>
      </div>
    </div>
  )
}

export default function SectionAssets({ assets }: SectionAssetsProps) {
  const items = useMemo(() => normalizeAssets(assets), [assets])
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  if (items.length === 0) return null

  const open = openIndex == null ? null : items[openIndex] ?? null

  return (
    <div className="section-asset-list">
      <div className="section-asset-list-label">
        Hình ảnh & bảng biểu trong mục
      </div>
      <div className="section-asset-grid">
        {items.map((asset, index) => (
          <AssetThumbnail
            key={asset.id}
            asset={asset}
            index={index}
            onOpen={() => setOpenIndex(index)}
          />
        ))}
      </div>
      {open && openIndex != null && (
        <AssetLightbox
          asset={open}
          index={openIndex}
          total={items.length}
          onClose={() => setOpenIndex(null)}
        />
      )}
    </div>
  )
}
