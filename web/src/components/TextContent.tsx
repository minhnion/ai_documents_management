import { useEffect, useRef, useState } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'

interface TextContentProps {
  fullText: string | null
  activeSection: WorkspaceSectionNode | null
  editMode?: boolean
  sectionEdits?: Record<number, { content: string | null; heading: string | null }>
  onSectionEdit?: (sectionId: number, field: 'content' | 'heading', value: string) => void
  toc?: WorkspaceSectionNode[]
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

export default function TextContent({ fullText, activeSection, editMode, sectionEdits, onSectionEdit, toc }: TextContentProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [beforeText, setBeforeText] = useState('')
  const [highlightText, setHighlightText] = useState('')
  const [afterText, setAfterText] = useState('')
  const highlightRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!fullText) {
      setBeforeText('')
      setHighlightText('')
      setAfterText('')
      return
    }
    if (!activeSection || activeSection.start_char == null || activeSection.end_char == null) {
      setBeforeText(fullText)
      setHighlightText('')
      setAfterText('')
      return
    }
    const s = Math.max(0, activeSection.start_char)
    const e = Math.min(fullText.length, activeSection.end_char)
    setBeforeText(fullText.slice(0, s))
    setHighlightText(fullText.slice(s, e))
    setAfterText(fullText.slice(e))
  }, [fullText, activeSection])

  useEffect(() => {
    if (highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [highlightText])

  if (!fullText) {
    return (
      <div className="loading-center">
        <span className="text-muted">Không có nội dung văn bản.</span>
      </div>
    )
  }

  if (editMode && toc) {
    return (
      <div ref={containerRef} className="content-body">
        {flattenNodes(toc).map(node => (
          <div key={node.section_id} style={{ marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              {node.heading || `Mục ${node.section_id}`}
            </div>
            <textarea
              className="form-textarea"
              value={sectionEdits?.[node.section_id]?.content ?? node.content ?? ''}
              onChange={e => onSectionEdit?.(node.section_id, 'content', e.target.value)}
              rows={6}
              style={{ width: '100%', fontFamily: 'inherit', fontSize: 14 }}
            />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div ref={containerRef} className="content-body">
      <pre className="content-text">
        <span>{beforeText}</span>
        {highlightText && (
          <span ref={highlightRef} className="content-highlight">
            {highlightText}
          </span>
        )}
        <span>{afterText}</span>
      </pre>
    </div>
  )
}
