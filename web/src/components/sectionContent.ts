const PAGE_BREAK_RE = /<!--\s*PAGE_BREAK\s*-->/gi
const PURE_PAGE_NUMBER_RE = /^\s*\d+\s*$/gm

export function normalizeSectionContent(content: string | null): string {
  if (!content) return ''

  let cleaned = content
    .replace(/\r\n?/g, '\n')
    .replace(PAGE_BREAK_RE, '\n')
    .replace(PURE_PAGE_NUMBER_RE, '\n')

  cleaned = cleanIncompleteTables(cleaned)

  return cleaned
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function cleanIncompleteTables(content: string): string {
  let result = content
  let match: RegExpExecArray | null

  const completeTablePattern = /<table\b[^>]*>[\s\S]*?<\/table>/gi

  while ((match = completeTablePattern.exec(content)) !== null) {
    const tableHtml = match[0]
    if (!isValidTable(tableHtml)) {
      const textOnly = extractTextFromTable(tableHtml)
      result = result.replace(tableHtml, textOnly)
    }
  }

  return result
    .replace(/<table\b[^>]*>/gi, ' ')
    .replace(/<\/?(?:tbody|thead|tfoot|tr|td|th)\b[^>]*>/gi, ' ')
}

function isValidTable(tableHtml: string): boolean {
  if (typeof window === 'undefined' || typeof DOMParser === 'undefined') {
    return false
  }

  try {
    const doc = new DOMParser().parseFromString(tableHtml, 'text/html')
    const table = doc.querySelector('table')
    if (!table) return false

    const rows = table.querySelectorAll('tr')
    if (rows.length === 0) return false

    for (const row of Array.from(rows)) {
      const cells = row.querySelectorAll('th, td')
      if (cells.length === 0) return false
    }

    return true
  } catch {
    return false
  }
}

function extractTextFromTable(tableHtml: string): string {
  if (typeof window === 'undefined' || typeof DOMParser === 'undefined') {
    return tableHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  }

  try {
    const doc = new DOMParser().parseFromString(tableHtml, 'text/html')
    return (doc.body.textContent ?? '').replace(/\s+/g, ' ').trim()
  } catch {
    return tableHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  }
}
