import { useEffect, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react'
import { type MouseEvent } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'

interface TocTreeProps {
  nodes: WorkspaceSectionNode[]
  activeId: number | null
  onSelect: (node: WorkspaceSectionNode) => void
  revealTargetId?: number | null
  revealRequestKey?: number | null
  depth?: number
}

function nodeContainsSection(node: WorkspaceSectionNode, sectionId: number): boolean {
  if (node.section_id === sectionId) return true
  return node.children.some(child => nodeContainsSection(child, sectionId))
}

export default function TocTree({
  nodes,
  activeId,
  onSelect,
  revealTargetId = null,
  revealRequestKey = null,
  depth = 0,
}: TocTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<number>>(() => {
    if (depth >= 2) {
      return new Set(nodes.filter(n => n.children.length > 0).map(n => n.section_id))
    }
    return new Set<number>()
  })

  useEffect(() => {
    if (revealTargetId == null || revealRequestKey == null) return
    setCollapsed(prev => {
      let changed = false
      const next = new Set(prev)
      for (const node of nodes) {
        if (next.has(node.section_id) && nodeContainsSection(node, revealTargetId)) {
          next.delete(node.section_id)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [nodes, revealRequestKey, revealTargetId])

  // Only the root TocTree drives the scroll-into-view: descendants share the
  // same DOM and finding by data attribute is enough.
  useEffect(() => {
    if (depth !== 0 || activeId == null) return
    let frameId: number | null = null
    let attempts = 0
    const targetId = revealTargetId === activeId ? revealTargetId : activeId

    const scrollWhenMounted = () => {
      attempts += 1
      const el = document.querySelector(
        `[data-toc-section-id="${targetId}"]`,
      ) as HTMLElement | null
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
        return
      }
      if (attempts < 8) {
        frameId = window.requestAnimationFrame(scrollWhenMounted)
      }
    }

    frameId = window.requestAnimationFrame(scrollWhenMounted)
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId)
      }
    }
  }, [activeId, depth, revealRequestKey, revealTargetId])

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
            <div
              className="toc-row"
              data-toc-section-id={node.section_id}
              style={{ paddingLeft: `${10 + depth * 14}px` }}
            >
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
                revealTargetId={revealTargetId}
                revealRequestKey={revealRequestKey}
                depth={depth + 1}
              />
            )}
          </div>
        )
      })}
    </>
  )
}
