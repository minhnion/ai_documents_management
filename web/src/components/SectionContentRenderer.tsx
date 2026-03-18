import { Fragment, type ElementType, type ReactNode } from 'react'

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

const TABLE_BLOCK_RE = /<table\b[\s\S]*?<\/table>/gi
const FLOWCHART_BLOCK_RE = /<::flowchart\s*([\s\S]*?)\s*:\s*flowchart::>/gi
const PAGE_BREAK_RE = /<!--\s*PAGE_BREAK\s*-->/gi
const LOGO_BLOCK_RE = /<::logo:[\s\S]*?::>/gi
const PURE_PAGE_NUMBER_RE = /^\s*\d+\s*$/gm
const HEADING_RE = /^(#{1,6})\s+(.+)$/
const LIST_ITEM_RE = /^(?:[-*•]\s+|(?:\d+|[A-Za-z])[.)]\s+)(.+)$/
const BOLD_RE = /\*\*(.+?)\*\*/g
const ARROW_ONLY_RE = /^(?:↓|⬇|->|=>|→)+$/

export default function SectionContentRenderer({ content }: SectionContentRendererProps) {
  const normalized = normalizeContent(content)

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
        return <TextBlock key={`text-${index}`} value={block.value} />
      })}
    </div>
  )
}

function normalizeContent(content: string | null): string {
  if (!content) return ''

  return content
    .replace(/\r\n?/g, '\n')
    .replace(LOGO_BLOCK_RE, '\n')
    .replace(PAGE_BREAK_RE, '\n')
    .replace(PURE_PAGE_NUMBER_RE, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function parseContentBlocks(content: string): ContentBlock[] {
  const blocks: ContentBlock[] = []
  const tokenRe = new RegExp(`${TABLE_BLOCK_RE.source}|${FLOWCHART_BLOCK_RE.source}`, 'gi')

  let cursor = 0
  let match = tokenRe.exec(content)
  while (match) {
    const start = match.index
    if (start > cursor) {
      pushTextBlock(blocks, content.slice(cursor, start))
    }

    const token = match[0]
    if (token.toLowerCase().startsWith('<table')) {
      const rows = parseTable(token)
      if (rows.length > 0) {
        blocks.push({ type: 'table', rows })
      }
    } else {
      const steps = parseFlowchart(token)
      if (steps.length > 0) {
        blocks.push({ type: 'flowchart', steps })
      }
    }

    cursor = start + token.length
    match = tokenRe.exec(content)
  }

  if (cursor < content.length) {
    pushTextBlock(blocks, content.slice(cursor))
  }

  return blocks.length > 0 ? blocks : [{ type: 'text', value: content }]
}

function pushTextBlock(blocks: ContentBlock[], value: string): void {
  const cleaned = value.trim()
  if (!cleaned) return
  blocks.push({ type: 'text', value: cleaned })
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

function parseFlowchart(flowchartBlock: string): string[] {
  const match = /<::flowchart\s*([\s\S]*?)\s*:\s*flowchart::>/i.exec(flowchartBlock)
  if (!match) return []

  const lines = match[1]
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

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
