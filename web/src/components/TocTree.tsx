import { type WorkspaceSectionNode } from '../lib/types'

interface TocTreeProps {
  nodes: WorkspaceSectionNode[]
  activeId: number | null
  onSelect: (node: WorkspaceSectionNode) => void
  depth?: number
}

export default function TocTree({ nodes, activeId, onSelect, depth = 0 }: TocTreeProps) {
  return (
    <>
      {nodes.map((node) => (
        <div key={node.section_id}>
          <button
            className={`toc-node${node.section_id === activeId ? ' active' : ''}`}
            style={{ paddingLeft: `${10 + depth * 14}px` }}
            onClick={() => onSelect(node)}
            title={node.heading ?? ''}
          >
            <span className="toc-node-dot" />
            <span className="truncate" style={{ fontSize: depth === 0 ? '13px' : '12px' }}>
              {node.heading ?? `Section ${node.section_id}`}
            </span>
          </button>
          {node.children.length > 0 && (
            <TocTree
              nodes={node.children}
              activeId={activeId}
              onSelect={onSelect}
              depth={depth + 1}
            />
          )}
        </div>
      ))}
    </>
  )
}
