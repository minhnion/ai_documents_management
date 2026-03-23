// web/src/components/SectionCard.tsx
import { useEffect, useRef, useState } from 'react'
import { Edit3, Check, X, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react'
import type { WorkspaceSectionNode } from '../lib/types'
import SectionContentRenderer from './SectionContentRenderer'
import { normalizeSectionContent } from './sectionContent'

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
              <SectionContentRenderer content={node.content} />
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
