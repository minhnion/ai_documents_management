// web/src/components/TextContent.tsx
import { useEffect, useRef, useCallback } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'
import SectionCard from './SectionCard'

interface TextContentProps {
  toc: WorkspaceSectionNode[]
  canEdit: boolean
  activeSectionId: number | null
  sectionEdits: Record<number, { heading: string; content: string }>
  savingSections: Record<number, boolean>
  onSectionEditStart: (
    sectionId: number,
    currentHeading: string,
    currentContent: string,
  ) => void
  onSectionEditChange: (
    sectionId: number,
    field: 'heading' | 'content',
    value: string,
  ) => void
  onSaveSection: (sectionId: number) => Promise<void>
  onCancelSection: (sectionId: number) => void
}

function flattenNodes(nodes: WorkspaceSectionNode[]): WorkspaceSectionNode[] {
  const result: WorkspaceSectionNode[] = []
  for (const node of nodes) {
    result.push(node)
    if (node.children.length > 0) {
      result.push(...flattenNodes(node.children))
    }
  }
  return result
}

export default function TextContent({
  toc,
  canEdit,
  activeSectionId,
  sectionEdits,
  savingSections,
  onSectionEditStart,
  onSectionEditChange,
  onSaveSection,
  onCancelSection,
}: TextContentProps) {
  const sectionRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  // Scroll to active section when TOC selection changes
  useEffect(() => {
    if (activeSectionId == null) return
    const el = sectionRefs.current.get(activeSectionId)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [activeSectionId])

  const setRef = useCallback((sectionId: number) => (el: HTMLDivElement | null) => {
    if (el) {
      sectionRefs.current.set(sectionId, el)
    } else {
      sectionRefs.current.delete(sectionId)
    }
  }, [])

  if (!toc || toc.length === 0) {
    return (
      <div className="loading-center">
        <span className="text-muted">Không có nội dung văn bản.</span>
      </div>
    )
  }

  const flatNodes = flattenNodes(toc)

  return (
    <div className="content-body">
      {flatNodes.map(node => (
        <SectionCard
          key={node.section_id}
          node={node}
          editValue={
            node.section_id in sectionEdits
              ? sectionEdits[node.section_id]
              : null
          }
          canEdit={canEdit}
          isActive={node.section_id === activeSectionId}
          refCallback={setRef(node.section_id)}
          saving={savingSections[node.section_id] ?? false}
          onEditStart={() => onSectionEditStart(
            node.section_id,
            node.heading ?? '',
            node.content ?? '',
          )}
          onEditChange={(field, value) => onSectionEditChange(node.section_id, field, value)}
          onSave={() => onSaveSection(node.section_id)}
          onCancel={() => onCancelSection(node.section_id)}
        />
      ))}
    </div>
  )
}
