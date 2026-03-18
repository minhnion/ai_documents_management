// web/src/components/SectionCard.tsx
import { useEffect, useRef } from 'react'
import { Edit3, Check, X, AlertTriangle } from 'lucide-react'
import type { WorkspaceSectionNode } from '../lib/types'
import SectionContentRenderer, { normalizeSectionContent } from './SectionContentRenderer'

interface SectionCardProps {
  node: WorkspaceSectionNode
  editValue: string | null        // null = view mode, string = edit mode
  canEdit: boolean
  isActive: boolean               // true when TOC-selected
  refCallback: (el: HTMLDivElement | null) => void
  onEditStart: () => void
  onEditChange: (value: string) => void
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

  const isEditing = editValue !== null
  const hasRenderableContent = normalizeSectionContent(node.content).length > 0
  const hideEmptyBody = !isEditing && node.children.length > 0 && !hasRenderableContent

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

  const headingLabel = node.heading || `Mục ${node.section_id}`
  const indent = Math.max(0, (node.level ?? 1) - 1) * 16

  return (
    <div
      ref={refCallback}
      className={`section-card${isActive ? ' section-card--active' : ''}${isEditing ? ' section-card--editing' : ''}${hideEmptyBody ? ' section-card--compact' : ''}`}
    >
      {/* Header */}
      <div className="section-card-header">
        <span className={`section-card-heading section-heading-level-${node.level ?? 1}`} style={{ paddingLeft: indent }}>
          {headingLabel}
        </span>
        <div className="section-card-header-actions">
          {node.is_suspect && (
            <span className="section-suspect-badge" title="Mục cần kiểm tra chất lượng OCR">
              <AlertTriangle size={13} />
            </span>
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
      {!hideEmptyBody && (
        <div className="section-card-body">
          {isEditing ? (
            <textarea
              ref={textareaRef}
              className="section-card-textarea"
              value={editValue}
              onChange={e => onEditChange(e.target.value)}
              disabled={saving}
            />
          ) : (
            <div className="section-card-content">
              <SectionContentRenderer content={node.content} />
            </div>
          )}
        </div>
      )}

      {/* Footer (edit mode only) */}
      {isEditing && (
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
