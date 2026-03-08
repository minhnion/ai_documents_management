import { useEffect, useRef, useState } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'

interface TextContentProps {
  fullText: string | null
  activeSection: WorkspaceSectionNode | null
}

export default function TextContent({ fullText, activeSection }: TextContentProps) {
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
