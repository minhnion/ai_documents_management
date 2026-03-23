import { Fragment, type ElementType, type ReactNode } from 'react'
import { normalizeSectionContent } from './sectionContent'

interface SectionContentRendererProps {
  content: string | null
}

interface TableCellData {
  text: string
  colSpan: number
  rowSpan: number
  isHeader: boolean
}

type ContentBlock =
  | { type: 'text'; value: string }
  | { type: 'table'; rows: TableCellData[][] }
  | { type: 'flowchart'; steps: string[] }
  | {
      type: 'custom'
      inferredType: string | null
      label: string | null
      value: string
    }

const TABLE_BLOCK_RE = /<table\b[\s\S]*?<\/table>/gi
const HEADING_RE = /^(#{1,6})\s+(.+)$/
const LIST_ITEM_RE = /^(?:[-*•]\s+|(?:\d+|[A-Za-z])[.)]\s+)(.+)$/
const BOLD_RE = /\*\*(.+?)\*\*/g
const ARROW_ONLY_RE = /^(?:↓|⬇|->|=>|→)+$/
const FLOW_CONNECTOR_RE = /\s*(?:->|=>|→|↓|⬇)\s*/
const CUSTOM_CLOSING_TYPE_RE = /:\s*([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)*)\s*::>\s*$/i
const CUSTOM_OPENING_LABEL_RE = /^<::\s*([A-Za-z][\w-]*)\s*:/i

const CUSTOM_BLOCK_EXAMPLES = [
  '<:: flow\nA\n->\nB\n: flow ::>',
  '<:: flow : A -> B',
  '<:: something : custom body : something ::>',
  '<:: unknown block with missing ending',
]

// Temporary parser scaffolding for the upcoming custom tag refactor.
// These fixtures document the malformed and shorthand formats the scanner
// must keep recognizing while the heuristics are rewritten.
void CUSTOM_BLOCK_EXAMPLES

// Boundary priority for custom block recovery:
// 1. nearest : type ::>
// 2. nearest ::>
// 3. next <table or <:: start
// 4. end of content

export default function SectionContentRenderer({ content }: SectionContentRendererProps) {
  const normalized = normalizeSectionContent(content)

  if (!normalized) {
    return <span className="section-rich-empty">Không có nội dung.</span>
  }

  const blocks = parseContentBlocks(normalized)

  return (
    <div className="section-rich-content">
      {blocks.map((block, index) => {
        if (block.type === 'table') {
          return <TableBlock key={`table-${index}`} rows={block.rows} />
        }
        if (block.type === 'flowchart') {
          return <FlowchartBlock key={`flowchart-${index}`} steps={block.steps} />
        }
        if (block.type === 'custom') {
          return (
            <CustomTagFallbackBlock
              key={`custom-${index}`}
              value={block.value}
              label={block.label}
            />
          )
        }
        return <TextBlock key={`text-${index}`} value={block.value} />
      })}
    </div>
  )
}

function parseContentBlocks(content: string): ContentBlock[] {
  const blocks: ContentBlock[] = []

  let cursor = 0
  while (cursor < content.length) {
    const nextTable = content.indexOf('<table', cursor)
    const nextCustom = content.indexOf('<::', cursor)
    const nextIndex = chooseNearest(nextTable, nextCustom)

    if (nextIndex === -1) {
      pushTextBlock(blocks, content.slice(cursor))
      break
    }

    if (nextIndex > cursor) {
      pushTextBlock(blocks, content.slice(cursor, nextIndex))
    }

    if (nextIndex === nextTable) {
      TABLE_BLOCK_RE.lastIndex = nextIndex
      const tableMatch = TABLE_BLOCK_RE.exec(content)
      if (!tableMatch || tableMatch.index !== nextIndex) {
        const danglingTagEnd = content.indexOf('>', nextIndex)
        cursor = danglingTagEnd >= 0 ? danglingTagEnd + 1 : nextIndex + 1
        continue
      }

      const rows = parseTable(tableMatch[0])
      if (rows.length > 0) {
        blocks.push({ type: 'table', rows })
      } else {
        pushTextBlock(blocks, tableMatch[0])
      }

      cursor = Math.max(nextIndex + 1, tableMatch.index + tableMatch[0].length)
      continue
    }

    const { raw, end, hasExplicitClose } = extractCustomBlockCandidate(content, nextIndex)
    const inferredType = inferCustomBlockType(raw)
    const label = extractCustomBlockLabel(raw)
    const value = cleanCustomBlockBody(raw, inferredType)

    if (!value && end <= nextIndex + 3) {
      pushTextBlock(blocks, content.slice(nextIndex, nextIndex + 3))
      cursor = nextIndex + 3
      continue
    }

    if (!value && end > nextIndex) {
      pushTextBlock(blocks, raw)
      cursor = end
      continue
    }

    if (!isConfidentCustomBlockCandidate(raw, value, inferredType, hasExplicitClose)) {
      pushTextBlock(blocks, raw)
      cursor = Math.max(nextIndex + 3, end)
      continue
    }

    const rendererKey = getCustomRendererKey(inferredType)

    if (rendererKey === 'flowchart') {
      const steps = parseFlowchart(value)
      if (steps.length > 0) {
        blocks.push({ type: 'flowchart', steps })
      } else {
        blocks.push({ type: 'custom', inferredType, label, value })
      }
    } else {
      blocks.push({ type: 'custom', inferredType, label, value })
    }

    cursor = Math.max(nextIndex + 3, end)
  }

  if (blocks.length > 0) {
    return blocks
  }

  const fallbackText = normalizeTextSegment(content).trim()
  return fallbackText ? [{ type: 'text', value: fallbackText }] : []
}

function pushTextBlock(blocks: ContentBlock[], value: string): void {
  const cleaned = normalizeTextSegment(value).trim()
  if (!cleaned) return
  blocks.push({ type: 'text', value: cleaned })
}

function normalizeTextSegment(value: string): string {
  return sanitizeRenderableText(value).replace(/\n{3,}/g, '\n\n')
}

function chooseNearest(...indexes: number[]): number {
  const candidates = indexes.filter((index) => index >= 0)
  return candidates.length > 0 ? Math.min(...candidates) : -1
}

function isStructuralBoundaryStart(content: string, index: number): boolean {
  const lineStart = content.lastIndexOf('\n', index - 1) + 1
  return content.slice(lineStart, index).trim().length === 0
}

function findNextTableBoundary(content: string, start: number): number {
  TABLE_BLOCK_RE.lastIndex = start
  const match = TABLE_BLOCK_RE.exec(content)
  return match?.index ?? -1
}

function findNextBlockBoundary(content: string, start: number): number {
  let cursor = start
  let nextTable = findNextTableBoundary(content, start)

  while (cursor < content.length) {
    const nextCustom = content.indexOf('<::', cursor)
    const nextIndex = chooseNearest(nextTable, nextCustom)

    if (nextIndex === -1) {
      return -1
    }

    if (nextIndex === nextTable) {
      return nextTable
    }

    if (isStructuralBoundaryStart(content, nextIndex)) {
      return nextIndex
    }

    cursor = nextIndex + 1
    nextTable = nextTable >= cursor ? nextTable : findNextTableBoundary(content, cursor)
  }

  return -1
}

function extractCustomBlockCandidate(
  content: string,
  start: number,
): { raw: string; end: number; hasExplicitClose: boolean } {
  const contentAfterStart = start + 3
  const boundary = findNextBlockBoundary(content, contentAfterStart)
  const limit = boundary === -1 ? content.length : boundary
  const candidate = content.slice(contentAfterStart, limit)
  const typedClosingMatch = /:\s*[A-Za-z][\w-]*\s*::>/.exec(candidate)

  if (typedClosingMatch) {
    const end = contentAfterStart + typedClosingMatch.index + typedClosingMatch[0].length
    return { raw: content.slice(start, end), end, hasExplicitClose: true }
  }

  const plainClosing = candidate.indexOf('::>')
  if (plainClosing >= 0) {
    const end = contentAfterStart + plainClosing + 3
    return { raw: content.slice(start, end), end, hasExplicitClose: true }
  }

  if (boundary >= 0) {
    return { raw: content.slice(start, boundary), end: boundary, hasExplicitClose: false }
  }

  return { raw: content.slice(start), end: content.length, hasExplicitClose: false }
}

function inferCustomBlockType(raw: string): string | null {
  const closingType = CUSTOM_CLOSING_TYPE_RE.exec(raw)?.[1] ?? null
  if (closingType) {
    return normalizeCustomType(closingType)
  }

  const openingType = CUSTOM_OPENING_LABEL_RE.exec(raw)?.[1] ?? null
  return normalizeCustomType(openingType)
}

function getCustomRendererKey(inferredType: string | null): 'flowchart' | 'fallback' {
  return inferredType === 'flowchart' ? 'flowchart' : 'fallback'
}

function normalizeCustomType(value: string | null): string | null {
  const normalized = value?.trim().toLowerCase().replace(/[\s_]+/g, '-') ?? ''
  if (!normalized) {
    return null
  }

  if (normalized === 'flow' || normalized === 'flowchart' || normalized === 'flow-chart') {
    return 'flowchart'
  }

  return normalized
}

function extractCustomBlockLabel(raw: string): string | null {
  const label =
    CUSTOM_OPENING_LABEL_RE.exec(raw)?.[1]?.trim() ??
    CUSTOM_CLOSING_TYPE_RE.exec(raw)?.[1]?.trim() ??
    ''
  return label || null
}

function cleanCustomBlockBody(raw: string, inferredType: string | null): string {
  let value = raw.replace(/^<::\s*/, '')
  const openingLabel = CUSTOM_OPENING_LABEL_RE.exec(raw)?.[1]?.trim() ?? ''

  if (openingLabel) {
    value = value.replace(new RegExp(`^${escapeRegExp(openingLabel)}\\s*:\\s*`, 'i'), '')
  }

  if (inferredType) {
    value = value.replace(CUSTOM_CLOSING_TYPE_RE, '')
  }

  value = value.replace(/\s*::>\s*$/, '')

  return sanitizeRenderableText(value).trim()
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function sanitizeRenderableText(value: string): string {
  return value
    .replace(/<::\s*[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)*\s*::>/gi, ' ')
    .replace(/<\/(?:table|tbody|thead|tfoot|tr|td|th)\s*>/gi, ' ')
    .replace(/<(?:table|tbody|thead|tfoot|tr|td|th)\b[^>]*>/gi, ' ')
    .replace(/<::\s*/g, ' ')
    .replace(/\s*::>/g, ' ')
    .replace(/\n?[ \t]*:[ \t]*[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)*[ \t]*::>\s*$/gim, ' ')
    .replace(/[ \t]{2,}/g, ' ')
}

function isConfidentCustomBlockCandidate(
  raw: string,
  value: string,
  inferredType: string | null,
  hasExplicitClose: boolean,
): boolean {
  if (!value) {
    return false
  }

  if (hasExplicitClose) {
    return true
  }

  if (inferredType === 'flowchart') {
    const lines = value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)

    if (lines.length === 1 && FLOW_CONNECTOR_RE.test(lines[0]) && !ARROW_ONLY_RE.test(lines[0])) {
      return true
    }

    if (lines.length < 3) {
      return false
    }

    const arrowLineCount = lines.filter((line) => ARROW_ONLY_RE.test(line)).length
    return arrowLineCount > 0
  }

  if (inferredType) {
    return true
  }

  return /^<::\s*[A-Za-z][\w-]*\s*:[^\n]+$/.test(raw)
}

function parseTable(tableHtml: string): TableCellData[][] {
  if (typeof window === 'undefined' || typeof DOMParser === 'undefined') {
    return []
  }

  const doc = new DOMParser().parseFromString(tableHtml, 'text/html')
  const table = doc.querySelector('table')
  if (!table) return []

  return Array.from(table.querySelectorAll('tr'))
    .map((row) =>
      Array.from(row.querySelectorAll('th, td')).map((cell) => ({
        text: (cell.textContent ?? '').replace(/\s+/g, ' ').trim(),
        colSpan: Number(cell.getAttribute('colspan') ?? '1') || 1,
        rowSpan: Number(cell.getAttribute('rowspan') ?? '1') || 1,
        isHeader: cell.tagName.toLowerCase() === 'th',
      })),
    )
    .filter((row) => row.length > 0)
}

function TableBlock({ rows }: { rows: TableCellData[][] }) {
  return (
    <div className="section-rich-table-wrap">
      <table className="section-rich-table">
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`row-${rowIndex}`}>
              {row.map((cell, cellIndex) => {
                const CellTag = cell.isHeader ? 'th' : 'td'
                return (
                  <CellTag
                    key={`cell-${rowIndex}-${cellIndex}`}
                    colSpan={cell.colSpan}
                    rowSpan={cell.rowSpan}
                  >
                    {cell.text}
                  </CellTag>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function parseFlowchart(flowchartContent: string): string[] {
  const lines = flowchartContent
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

  if (lines.length === 1 && FLOW_CONNECTOR_RE.test(lines[0])) {
    return lines[0]
      .split(FLOW_CONNECTOR_RE)
      .map((step) => step.trim())
      .filter(Boolean)
  }

  const steps: string[] = []
  let current: string[] = []

  for (const line of lines) {
    if (ARROW_ONLY_RE.test(line)) {
      if (current.length > 0) {
        steps.push(current.join('\n'))
        current = []
      }
      continue
    }
    current.push(line)
  }

  if (current.length > 0) {
    steps.push(current.join('\n'))
  }

  return steps
}

function FlowchartBlock({ steps }: { steps: string[] }) {
  return (
    <div className="section-flowchart">
      {steps.map((step, index) => {
        const lines = step.split('\n').map((line) => line.trim()).filter(Boolean)
        const [title, ...details] = lines

        return (
          <Fragment key={`step-${index}`}>
            <div className="section-flow-step">
              <div className="section-flow-step-title">{renderInlineNodes(title)}</div>
              {details.length > 0 && (
                <div className="section-flow-step-body">
                  {details.map((line, detailIndex) => (
                    <p key={`detail-${detailIndex}`}>{renderInlineNodes(line)}</p>
                  ))}
                </div>
              )}
            </div>
            {index < steps.length - 1 && <div className="section-flow-arrow">↓</div>}
          </Fragment>
        )
      })}
    </div>
  )
}

function CustomTagFallbackBlock({
  value,
  label,
}: {
  value: string
  label: string | null
}) {
  return (
    <div className="section-custom-block">
      {label && <div className="section-custom-block-label">{label}</div>}
      <TextBlock value={value} />
    </div>
  )
}

function TextBlock({ value }: { value: string }) {
  const paragraphs = value.split(/\n{2,}/).map((paragraph) => paragraph.trim()).filter(Boolean)

  return (
    <>
      {paragraphs.map((paragraph, index) => {
        const headingMatch = HEADING_RE.exec(paragraph)
        if (headingMatch) {
          const headingLevel = Math.min(6, headingMatch[1].length + 2)
          const tagName = `h${headingLevel}` as ElementType
          return createHeading(tagName, `heading-${index}`, headingMatch[2].trim())
        }

        const lines = paragraph.split('\n').map((line) => line.trim()).filter(Boolean)
        const listItems = lines
          .map((line) => LIST_ITEM_RE.exec(line)?.[1]?.trim() ?? null)
        if (lines.length > 1 && listItems.every(Boolean)) {
          return (
            <ul key={`list-${index}`} className="section-rich-list">
              {listItems.map((item, itemIndex) => (
                <li key={`item-${itemIndex}`}>{renderInlineNodes(item ?? '')}</li>
              ))}
            </ul>
          )
        }

        return (
          <p key={`paragraph-${index}`} className="section-rich-paragraph">
            {lines.map((line, lineIndex) => (
              <Fragment key={`line-${lineIndex}`}>
                {renderInlineNodes(line)}
                {lineIndex < lines.length - 1 && <br />}
              </Fragment>
            ))}
          </p>
        )
      })}
    </>
  )
}

function createHeading(
  tagName: ElementType,
  key: string,
  text: string,
) {
  const HeadingTag = tagName
  return (
    <HeadingTag key={key} className="section-rich-heading">
      {renderInlineNodes(text)}
    </HeadingTag>
  )
}

function renderInlineNodes(value: string) {
  const nodes: ReactNode[] = []
  let cursor = 0
  BOLD_RE.lastIndex = 0
  let match = BOLD_RE.exec(value)

  while (match) {
    if (match.index > cursor) {
      nodes.push(value.slice(cursor, match.index))
    }
    nodes.push(<strong key={`strong-${match.index}`}>{match[1]}</strong>)
    cursor = match.index + match[0].length
    match = BOLD_RE.exec(value)
  }

  if (cursor < value.length) {
    nodes.push(value.slice(cursor))
  }

  return nodes.length > 0 ? nodes : value
}
