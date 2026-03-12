import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react'
import { type MouseEvent } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'

interface TocTreeProps {
  nodes: WorkspaceSectionNode[]
  activeId: number | null
  onSelect: (node: WorkspaceSectionNode) => void
  depth?: number
}

export default function TocTree({ nodes, activeId, onSelect, depth = 0 }: TocTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<number>>(() => {
    if (depth >= 2) {
      return new Set(nodes.filter(n => n.children.length > 0).map(n => n.section_id))
    }
    return new Set<number>()
  })

  const toggleCollapse = (id: number, e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  return (
    <>
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0
        const isCollapsed = collapsed.has(node.section_id)

        return (
          <div key={node.section_id}>
            <div className="toc-row" style={{ paddingLeft: `${10 + depth * 14}px` }}>
              {hasChildren ? (
                <button
                  className="toc-chevron"
                  onClick={(e) => toggleCollapse(node.section_id, e)}
                  aria-label={isCollapsed
                    ? `Expand ${node.heading ?? node.section_id}`
                    : `Collapse ${node.heading ?? node.section_id}`}
                >
                  {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                </button>
              ) : (
                <span className="toc-chevron-placeholder" />
              )}
              <button
                className={`toc-node${node.section_id === activeId ? ' active' : ''}${node.is_suspect ? ' toc-node-suspect' : ''}`}
                onClick={() => onSelect(node)}
                title={node.heading ?? ''}
              >
                {node.is_suspect && <AlertTriangle size={12} style={{ flexShrink: 0 }} />}
                <span className="truncate" style={{ fontSize: depth === 0 ? '13px' : '12px' }}>
                  {node.heading ?? `Section ${node.section_id}`}
                </span>
              </button>
            </div>
            {hasChildren && !isCollapsed && (
              <TocTree
                nodes={node.children}
                activeId={activeId}
                onSelect={onSelect}
                depth={depth + 1}
              />
            )}
          </div>
        )
      })}
    </>
  )
}
