import { useCallback, useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import {
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Download,
  ExternalLink,
} from 'lucide-react'
import { api } from '../lib/api'

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

const DEFAULT_SCALE = 1.5
const DEFAULT_ROTATION = 0
const RANGE_CHUNK_SIZE = 256 * 1024

interface PdfViewerProps {
  documentId: number | null
  page?: number
  pageJumpKey?: number | null
}

export default function PdfViewer({ documentId, page, pageJumpKey }: PdfViewerProps) {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [numPages, setNumPages] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [renderedPages, setRenderedPages] = useState<number[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const [pageInputValue, setPageInputValue] = useState('1')
  const [reloadKey, setReloadKey] = useState(0)

  const containerRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<(HTMLDivElement | null)[]>([])
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([])
  const renderingRef = useRef(false)
  const currentDocRef = useRef<PDFDocumentProxy | null>(null)
  const observerRef = useRef<IntersectionObserver | null>(null)

  const loadPdfBlob = useCallback(async () => {
    if (!documentId) return null
    const response = await api.get(`/documents/${documentId}/file`, {
      responseType: 'blob',
    })
    return response.data as Blob
  }, [documentId])

  const buildPdfRequest = useCallback(() => {
    if (!documentId) return null
    const token = localStorage.getItem('access_token')
    return {
      url: api.getUri({ url: `/documents/${documentId}/file` }),
      httpHeaders: token ? { Authorization: `Bearer ${token}` } : undefined,
      withCredentials: false,
      rangeChunkSize: RANGE_CHUNK_SIZE,
      disableAutoFetch: false,
      disableStream: false,
    }
  }, [documentId])

  const renderAllPages = useCallback(async (doc: PDFDocumentProxy) => {
    if (renderingRef.current) return
    renderingRef.current = true

    try {
      for (let pageNumber = 1; pageNumber <= doc.numPages; pageNumber += 1) {
        if (currentDocRef.current !== doc) break

        const canvas = canvasRefs.current[pageNumber - 1]
        const wrapper = pageRefs.current[pageNumber - 1]
        if (!canvas || !wrapper) continue

        try {
          const pdfPage = await doc.getPage(pageNumber)
          const viewport = pdfPage.getViewport({
            scale: DEFAULT_SCALE,
            rotation: DEFAULT_ROTATION,
          })
          const context = canvas.getContext('2d')
          if (!context) continue

          const outputScale = window.devicePixelRatio || 1
          canvas.width = Math.floor(viewport.width * outputScale)
          canvas.height = Math.floor(viewport.height * outputScale)
          canvas.style.width = '100%'
          canvas.style.height = 'auto'
          wrapper.style.width = `${viewport.width}px`

          context.setTransform(outputScale, 0, 0, outputScale, 0, 0)
          context.clearRect(0, 0, viewport.width, viewport.height)

          await pdfPage.render({
            canvas,
            canvasContext: context,
            viewport,
          }).promise

          if (typeof pdfPage.cleanup === 'function') {
            pdfPage.cleanup()
          }
          await new Promise<void>((resolve) => {
            window.setTimeout(() => resolve(), 0)
          })
        } catch (pageError) {
          console.error(`Failed rendering PDF page ${pageNumber}`, pageError)
        }
      }
    } finally {
      renderingRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!documentId) {
      setPdfDoc(null)
      setNumPages(0)
      setRenderedPages([])
      setError(null)
      setActionError(null)
      return
    }

    const request = buildPdfRequest()
    if (!request) return

    let cancelled = false
    let loadingTask: ReturnType<typeof pdfjs.getDocument> | null = null

    setLoading(true)
    setError(null)
    setActionError(null)
    setPdfDoc(null)
    setNumPages(0)
    setRenderedPages([])
    setCurrentPage(1)
    setPageInputValue('1')
    currentDocRef.current = null
    pageRefs.current = []
    canvasRefs.current = []

    ;(async () => {
      try {
        loadingTask = pdfjs.getDocument(request)
        const doc = await loadingTask.promise
        if (cancelled) {
          await doc.destroy()
          return
        }
        currentDocRef.current = doc
        setPdfDoc(doc)
        setNumPages(doc.numPages)
      } catch (loadError) {
        console.error('Failed loading PDF document', loadError)
        if (!cancelled) {
          setError('Không thể tải tài liệu PDF.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
      const existingDoc = currentDocRef.current
      currentDocRef.current = null
      void loadingTask?.destroy()
      if (existingDoc) {
        void existingDoc.destroy()
      }
    }
  }, [buildPdfRequest, documentId, reloadKey])

  useEffect(() => {
    if (!pdfDoc) return

    const totalPages = pdfDoc.numPages
    pageRefs.current = new Array(totalPages).fill(null)
    canvasRefs.current = new Array(totalPages).fill(null)
    setRenderedPages(Array.from({ length: totalPages }, (_, index) => index + 1))

    const timer = window.setTimeout(() => {
      void renderAllPages(pdfDoc)
    }, 50)
    return () => window.clearTimeout(timer)
  }, [pdfDoc, renderAllPages])

  useEffect(() => {
    if (!containerRef.current || renderedPages.length === 0) return

    observerRef.current?.disconnect()
    const ratios = new Map<number, number>()

    observerRef.current = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = pageRefs.current.indexOf(entry.target as HTMLDivElement)
          if (idx >= 0) {
            ratios.set(idx, entry.intersectionRatio)
          }
        })

        let maxRatio = -1
        let maxIdx = 0
        ratios.forEach((ratio, idx) => {
          if (ratio > maxRatio) {
            maxRatio = ratio
            maxIdx = idx
          }
        })

        const nextPage = maxIdx + 1
        setCurrentPage(nextPage)
        setPageInputValue(String(nextPage))
      },
      { root: containerRef.current, threshold: [0, 0.25, 0.5, 0.75, 1.0] }
    )

    pageRefs.current.forEach((el) => {
      if (el) {
        observerRef.current?.observe(el)
      }
    })

    return () => observerRef.current?.disconnect()
  }, [renderedPages])

  useEffect(() => {
    if (!page || page < 1) return
    setCurrentPage(page)
    setPageInputValue(String(page))
    const el = pageRefs.current[page - 1]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [page, pageJumpKey, renderedPages])

  const reload = () => {
    setReloadKey((value) => value + 1)
  }

  const downloadPdf = async () => {
    if (!documentId) return
    setActionError(null)
    try {
      const blob = await loadPdfBlob()
      if (!blob) return
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `document-${documentId}.pdf`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1500)
    } catch (downloadError) {
      console.error('Failed downloading PDF', downloadError)
      setActionError('Không thể tải xuống PDF.')
    }
  }

  const openInNewTab = async () => {
    if (!documentId) return
    setActionError(null)
    try {
      const blob = await loadPdfBlob()
      if (!blob) return
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (openError) {
      console.error('Failed opening PDF in new tab', openError)
      setActionError('Không thể mở PDF ở tab mới.')
    }
  }

  const goToPage = (pageNumber: number) => {
    const clamped = Math.max(1, Math.min(numPages, pageNumber))
    setCurrentPage(clamped)
    setPageInputValue(String(clamped))
    const el = pageRefs.current[clamped - 1]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  const handlePageInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setPageInputValue(e.target.value)
  }

  const handlePageInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const pageNumber = parseInt(pageInputValue, 10)
      if (!Number.isNaN(pageNumber)) {
        goToPage(pageNumber)
      }
    }
  }

  const handlePageInputBlur = () => {
    setPageInputValue(String(currentPage))
  }

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

  return (
    <div className="pdf-viewer-wrapper">
      <div className="pdf-viewer-toolbar">
        <span>PDF Viewer</span>
        <div className="pdf-tb-sep" />

        <button
          className="pdf-tb-btn"
          onClick={() => goToPage(currentPage - 1)}
          disabled={currentPage <= 1}
          title="Trang trước"
        >
          <ChevronLeft size={16} />
        </button>
        <button
          className="pdf-tb-btn"
          onClick={() => goToPage(currentPage + 1)}
          disabled={currentPage >= numPages}
          title="Trang sau"
        >
          <ChevronRight size={16} />
        </button>

        <div className="pdf-tb-sep" />

        <div className="pdf-tb-page">
          <input
            className="pdf-tb-page-input"
            type="text"
            value={pageInputValue}
            onChange={handlePageInputChange}
            onKeyDown={handlePageInputKeyDown}
            onBlur={handlePageInputBlur}
            aria-label="Số trang"
          />
          <span className="pdf-tb-page-total">/ {numPages}</span>
        </div>

        <div className="pdf-tb-sep" />

        <button className="pdf-tb-btn" onClick={reload} title="Tải lại">
          <RefreshCw size={16} />
        </button>
        <button
          className="pdf-tb-btn"
          onClick={downloadPdf}
          disabled={!pdfDoc}
          title="Tải xuống PDF"
        >
          <Download size={16} />
        </button>
        <button
          className="pdf-tb-btn"
          onClick={openInNewTab}
          disabled={!pdfDoc}
          title="Mở tab mới"
        >
          <ExternalLink size={16} />
        </button>
        {actionError && <span className="pdf-action-error">{actionError}</span>}
      </div>

      <div className="pdf-scroll-container" ref={containerRef}>
        {renderedPages.map((pageNumber) => (
          <div
            key={pageNumber}
            className="pdf-page-wrapper"
            ref={(el) => {
              pageRefs.current[pageNumber - 1] = el
            }}
          >
            <canvas
              ref={(el) => {
                canvasRefs.current[pageNumber - 1] = el
              }}
            />
            <span className="pdf-page-label">{pageNumber}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
