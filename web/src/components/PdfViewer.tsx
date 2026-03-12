import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface PdfViewerProps {
  documentId: number | null
  page?: number
}

export default function PdfViewer({ documentId, page }: PdfViewerProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // get page from url
  const pageFromUrl = new URLSearchParams(window.location.search).get('page')

  useEffect(() => {
    if (!documentId) {
      setObjectUrl(null)
      return
    }
    setLoading(true)
    setError(null)

    // Fetch the PDF with auth header, then create a blob URL for the embed
    api
      .get(`/documents/${documentId}/file`, { responseType: 'blob' })
      .then((res) => {
        const blob = res.data as Blob
        const url = URL.createObjectURL(blob)
        setObjectUrl(url)
      })
      .catch(() => setError('Không thể tải tài liệu PDF.'))
      .finally(() => setLoading(false))

    // Cleanup previous object URL
    return () => {
      setObjectUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return null
      })
    }
  }, [documentId])

  if (!documentId) {
    return (
      <div className="loading-center">
        <span className="text-muted">Chưa có tài liệu.</span>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="loading-center">
        <span className="loading-spinner" />
        <span>Đang tải PDF...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="loading-center">
        <span style={{ color: 'var(--danger)' }}>{error}</span>
      </div>
    )
  }

  if (!objectUrl) return null

  const pageNumber = page ? page : pageFromUrl

  const src = pageNumber ? `${objectUrl}#page=${pageNumber}` : objectUrl

  return (
    <embed
      src={src}
      type="application/pdf"
      className="pdf-embed"
      style={{ width: '100%', height: '100%', border: 'none' }}
    />
  )
}
