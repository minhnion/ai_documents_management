// web/src/components/SectionCard.tsx
import { useEffect, useMemo, useRef, useState } from 'react'
import { Edit3, Check, X, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react'
import type { WorkspaceSectionNode } from '../lib/types'
import SectionAssets from './SectionAssets'
import SectionContentRenderer from './SectionContentRenderer'
import { normalizeSectionContent } from './sectionContent'

function collectDescendantAssetIds(children: WorkspaceSectionNode[]): Set<string> {
  const ids = new Set<string>()
  const walk = (nodes: WorkspaceSectionNode[]) => {
    for (const child of nodes) {
      if (Array.isArray(child.landing_chunks)) {
        for (const entry of child.landing_chunks) {
          if (entry && typeof entry === 'object') {
            const id = (entry as Record<string, unknown>).id
            if (typeof id === 'string') ids.add(id)
          }
        }
      }
      if (child.children?.length) walk(child.children)
    }
  }
  walk(children)
  return ids
}

function selectOwnAssets(node: WorkspaceSectionNode): unknown[] {
  if (!Array.isArray(node.landing_chunks) || node.landing_chunks.length === 0) {
    return []
  }
  const descendantIds = collectDescendantAssetIds(node.children ?? [])
  if (descendantIds.size === 0) return node.landing_chunks
  return node.landing_chunks.filter((entry) => {
    if (!entry || typeof entry !== 'object') return false
    const id = (entry as Record<string, unknown>).id
    return typeof id === 'string' && !descendantIds.has(id)
  })
}

interface SectionCardProps {
  node: WorkspaceSectionNode
  editValue: { heading: string; content: string } | null
  canEdit: boolean
  isActive: boolean               // true when TOC-selected
  refCallback: (el: HTMLDivElement | null) => void
  onEditStart: () => void
  onEditChange: (field: 'heading' | 'content', value: string) => void
  onSave: () => void
  onCancel: () => void
  saving?: boolean
}

export default function SectionCard({
  node,
  editValue,
  canEdit,
  isActive,
  refCallback,
  onEditStart,
  onEditChange,
  onSave,
  onCancel,
  saving = false,
}: SectionCardProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [isCollapsed, setIsCollapsed] = useState(false)

  const isEditing = editValue !== null
  const hasRenderableContent = normalizeSectionContent(node.content).length > 0
  const ownAssets = useMemo(() => selectOwnAssets(node), [node])
  const ownAssetCount = ownAssets.filter(
    (entry) =>
      !!entry &&
      typeof entry === 'object' &&
      typeof (entry as Record<string, unknown>).image_url === 'string',
  ).length
  const hideEmptyBody =
    !isEditing && node.children.length > 0 && !hasRenderableContent && ownAssetCount === 0

  // Auto-focus textarea when entering edit mode
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus()
    }
  }, [isEditing]) // only when entering/leaving edit

  // Auto-resize textarea height to content
  useEffect(() => {
    if (!isEditing || !textareaRef.current) return
    textareaRef.current.style.height = 'auto'
    textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px'
  }, [editValue, isEditing])

  const headingLabel = (isEditing ? editValue.heading : node.heading) || `Mục ${node.section_id}`
  const indent = Math.max(0, (node.level ?? 1) - 1) * 16

  return (
    <div
      ref={refCallback}
      className={`section-card${isActive ? ' section-card--active' : ''}${isEditing ? ' section-card--editing' : ''}${hideEmptyBody ? ' section-card--compact' : ''}${isCollapsed ? ' section-card--collapsed' : ''}`}
    >
      {/* Header */}
      <div className="section-card-header">
        {!hideEmptyBody && (
          <button
            className="section-collapse-btn"
            onClick={() => setIsCollapsed(!isCollapsed)}
            title={isCollapsed ? 'Mở rộng' : 'Thu gọn'}
          >
            {isCollapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
          </button>
        )}
        <span className={`section-card-heading section-heading-level-${node.level ?? 1}`} style={{ paddingLeft: hideEmptyBody ? indent : 0 }}>
          {headingLabel}
        </span>
        <div className="section-card-header-actions">
          {node.is_suspect && (
            <div className="section-suspect-badge" title={`OCR Score: ${node.score?.toFixed(2) ?? 'N/A'} - Cần kiểm tra chất lượng`}>
              <AlertTriangle size={13} />
            </div>
          )}
          {canEdit && !isEditing && (
            <button
              className="btn btn-ghost btn-xs section-edit-btn"
              onClick={onEditStart}
              title="Chỉnh sửa mục này"
            >
              <Edit3 size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      {!hideEmptyBody && !isCollapsed && (
        <div className="section-card-body">
          {isEditing ? (
            <div className="section-card-edit-fields">
              <input
                className="section-card-heading-input"
                value={editValue.heading}
                onChange={e => onEditChange('heading', e.target.value)}
                disabled={saving}
                placeholder="Tiêu đề mục"
              />
              <textarea
                ref={textareaRef}
                className="section-card-textarea"
                value={editValue.content}
                onChange={e => onEditChange('content', e.target.value)}
                disabled={saving}
              />
            </div>
          ) : (
            <div className="section-card-content">
              <SectionContentRenderer
                content={node.content}
                hideEmptyMessage={ownAssetCount > 0}
              />
              <SectionAssets assets={ownAssets} />
            </div>
          )}
        </div>
      )}

      {/* Footer (edit mode only) */}
      {isEditing && !isCollapsed && (
        <div className="section-card-footer">
          <button
            className="btn btn-primary btn-xs"
            onClick={onSave}
            disabled={saving}
          >
            {saving
              ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
              : <><Check size={12} /> Lưu</>
            }
          </button>
          <button
            className="btn btn-secondary btn-xs"
            onClick={onCancel}
            disabled={saving}
          >
            <X size={12} /> Hủy
          </button>
        </div>
      )}
    </div>
  )
}
