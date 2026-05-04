// web/src/components/TextContent.tsx
import { useEffect, useMemo, useRef, useCallback } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'
import SectionCard from './SectionCard'

interface TextContentProps {
  toc: WorkspaceSectionNode[]
  canEdit: boolean
  activeSectionId: number | null
  activeSectionScrollBehavior?: ScrollBehavior | 'none'
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
  onVisibleSectionChange?: (sectionId: number) => void
}

const EXTERNAL_SCROLL_EMISSION_SUPPRESS_MS = 700

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
  activeSectionScrollBehavior = 'smooth',
  sectionEdits,
  savingSections,
  onSectionEditStart,
  onSectionEditChange,
  onSaveSection,
  onCancelSection,
  onVisibleSectionChange,
}: TextContentProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sectionRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const lastReportedSectionRef = useRef<number | null>(null)
  const suppressEmissionUntilRef = useRef(0)

  const flatNodes = useMemo(() => flattenNodes(toc ?? []), [toc])
  const sectionIdSequence = useMemo(
    () => flatNodes.map(node => node.section_id).join('|'),
    [flatNodes],
  )

  // Scroll to active section when activeSectionId changes from outside this pane.
  useEffect(() => {
    if (activeSectionId == null) return
    lastReportedSectionRef.current = activeSectionId
    if (activeSectionScrollBehavior === 'none') return
    const el = sectionRefs.current.get(activeSectionId)
    if (!el) return
    suppressEmissionUntilRef.current = Date.now() + EXTERNAL_SCROLL_EMISSION_SUPPRESS_MS
    el.scrollIntoView({ behavior: activeSectionScrollBehavior, block: 'start' })
  }, [activeSectionId, activeSectionScrollBehavior])

  // Detect which section card sits at the user's reading line in the middle
  // pane and notify the parent so the PDF + TOC can sync to it.
  useEffect(() => {
    if (!onVisibleSectionChange) return
    const root = containerRef.current
    if (!root) return

    let frameId: number | null = null

    const detectAndEmit = () => {
      if (Date.now() < suppressEmissionUntilRef.current) return
      const targetY = root.scrollTop + root.clientHeight * 0.3
      let bestId: number | null = null
      let bestTop = -Infinity
      let earliestId: number | null = null
      let earliestTop = Number.POSITIVE_INFINITY
      sectionRefs.current.forEach((el, sectionId) => {
        const top = el.offsetTop
        if (top < earliestTop) {
          earliestTop = top
          earliestId = sectionId
        }
        if (top <= targetY && top > bestTop) {
          bestTop = top
          bestId = sectionId
        }
      })
      const picked = bestId ?? earliestId
      if (picked == null) return
      if (picked === lastReportedSectionRef.current) return
      lastReportedSectionRef.current = picked
      onVisibleSectionChange(picked)
    }

    const handleScroll = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId)
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null
        detectAndEmit()
      })
    }

    root.addEventListener('scroll', handleScroll, { passive: true })
    handleScroll()

    return () => {
      root.removeEventListener('scroll', handleScroll)
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId)
      }
    }
  }, [onVisibleSectionChange, sectionIdSequence])

  const setRef = useCallback((sectionId: number) => (el: HTMLDivElement | null) => {
    if (el) {
      el.dataset.sectionId = String(sectionId)
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

  return (
    <div className="content-body" ref={containerRef}>
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
