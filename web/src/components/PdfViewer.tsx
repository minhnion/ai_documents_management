import { useCallback, useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import {
  ChevronLeft, ChevronRight,
  ZoomIn, ZoomOut,
  RotateCw,
  Maximize2, AlignJustify,
  RefreshCw, Download, ExternalLink,
} from 'lucide-react'
import { api } from '../lib/api'

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

const ZOOM_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
const DEFAULT_SCALE = 1.5

interface PdfViewerProps {
  documentId: number | null
  page?: number
}

export default function PdfViewer({ documentId, page }: PdfViewerProps) {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [numPages, setNumPages] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [renderedPages, setRenderedPages] = useState<number[]>([])

  const [scale, setScale] = useState(DEFAULT_SCALE)
  const [rotation, setRotation] = useState(0)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageInputValue, setPageInputValue] = useState('1')
  const [reloadKey, setReloadKey] = useState(0)

  const containerRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<(HTMLDivElement | null)[]>([])
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([])
  const renderingRef = useRef(false)
  const currentDocRef = useRef<PDFDocumentProxy | null>(null)
  const observerRef = useRef<IntersectionObserver | null>(null)
  const arrayBufferRef = useRef<ArrayBuffer | null>(null)
  const blobUrlRef = useRef<string | null>(null)

  // Load PDF document
  useEffect(() => {
    if (!documentId) {
      setPdfDoc(null)
      setNumPages(0)
      setRenderedPages([])
      return
    }
    setLoading(true)
    setError(null)
    setPdfDoc(null)
    setRenderedPages([])
    setCurrentPage(1)
    setPageInputValue('1')

    api
      .get(`/documents/${documentId}/file`, { responseType: 'arraybuffer' })
      .then(async (res) => {
        const buf = res.data as ArrayBuffer
        arrayBufferRef.current = buf
        // create blob URL for open-in-new-tab (revoke previous)
        if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current)
        blobUrlRef.current = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }))

        const doc = await pdfjs.getDocument({ data: buf }).promise
        currentDocRef.current = doc
        setPdfDoc(doc)
        setNumPages(doc.numPages)
      })
      .catch(() => setError('Không thể tải tài liệu PDF.'))
      .finally(() => setLoading(false))

    return () => {
      currentDocRef.current = null
      arrayBufferRef.current = null
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current)
        blobUrlRef.current = null
      }
    }
  }, [documentId, reloadKey])

  // Render all pages
  const renderAll = useCallback(
    async (doc: PDFDocumentProxy, sc: number, rot: number) => {
      if (renderingRef.current) return
      renderingRef.current = true

      for (let i = 1; i <= doc.numPages; i++) {
        if (currentDocRef.current !== doc) break

        const canvas = canvasRefs.current[i - 1]
        if (!canvas) continue

        try {
          const pdfPage = await doc.getPage(i)
          const viewport = pdfPage.getViewport({ scale: sc, rotation: rot })

          canvas.width = viewport.width
          canvas.height = viewport.height
          canvas.style.width = '100%'
          canvas.style.height = 'auto'

          await pdfPage.render({ canvas, viewport }).promise
        } catch {
          // skip failed pages silently
        }
      }

      renderingRef.current = false
    },
    []
  )

  // Initial render when pdfDoc loads
  useEffect(() => {
    if (!pdfDoc) return

    const totalPages = pdfDoc.numPages
    pageRefs.current = new Array(totalPages).fill(null)
    canvasRefs.current = new Array(totalPages).fill(null)
    setRenderedPages(Array.from({ length: totalPages }, (_, i) => i + 1))

    const timer = setTimeout(() => renderAll(pdfDoc, scale, rotation), 50)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfDoc])

  // Re-render when scale or rotation changes
  useEffect(() => {
    if (!pdfDoc || renderedPages.length === 0) return
    renderAll(pdfDoc, scale, rotation)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale, rotation])

  // IntersectionObserver: track current visible page while scrolling
  useEffect(() => {
    if (!containerRef.current || renderedPages.length === 0) return

    observerRef.current?.disconnect()

    const ratios = new Map<number, number>()

    observerRef.current = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = pageRefs.current.indexOf(entry.target as HTMLDivElement)
          if (idx >= 0) ratios.set(idx, entry.intersectionRatio)
        })

        let maxRatio = -1
        let maxIdx = 0
        ratios.forEach((ratio, idx) => {
          if (ratio > maxRatio) { maxRatio = ratio; maxIdx = idx }
        })

        const pg = maxIdx + 1
        setCurrentPage(pg)
        setPageInputValue(String(pg))
      },
      { root: containerRef.current, threshold: [0, 0.25, 0.5, 0.75, 1.0] }
    )

    pageRefs.current.forEach((el) => {
      if (el) observerRef.current!.observe(el)
    })

    return () => observerRef.current?.disconnect()
  }, [renderedPages])

  // Scroll to page from TOC prop
  useEffect(() => {
    if (!page || page < 1) return
    const el = pageRefs.current[page - 1]
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [page, renderedPages])

  // ── Toolbar handlers ──────────────────────────────────────────

  const zoomIn = () => {
    setScale((s) => ZOOM_STEPS.find((z) => z > s) ?? s)
  }

  const zoomOut = () => {
    setScale((s) => [...ZOOM_STEPS].reverse().find((z) => z < s) ?? s)
  }

  const fitWidth = () => {
    if (!containerRef.current || !pdfDoc) return
    const containerWidth = containerRef.current.clientWidth - 32
    pdfDoc.getPage(1).then((p) => {
      const vp = p.getViewport({ scale: 1, rotation })
      setScale(containerWidth / vp.width)
    })
  }

  const fitPage = () => {
    if (!containerRef.current || !pdfDoc) return
    const containerH = containerRef.current.clientHeight
    const containerW = containerRef.current.clientWidth - 32
    pdfDoc.getPage(1).then((p) => {
      const vp = p.getViewport({ scale: 1, rotation })
      setScale(Math.min(containerH / vp.height, containerW / vp.width))
    })
  }

  const rotateCw = () => setRotation((r) => (r + 90) % 360)

  const reload = () => {
    setPdfDoc(null)
    setRenderedPages([])
    setScale(DEFAULT_SCALE)
    setRotation(0)
    setReloadKey((k) => k + 1)
  }

  const downloadPdf = () => {
    if (!arrayBufferRef.current) return
    const blob = new Blob([arrayBufferRef.current], { type: 'application/pdf' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `document-${documentId}.pdf`
    a.click()
    URL.revokeObjectURL(url)
  }

  const openInNewTab = () => {
    if (blobUrlRef.current) window.open(blobUrlRef.current, '_blank')
  }

  const goToPage = (pg: number) => {
    const clamped = Math.max(1, Math.min(numPages, pg))
    const el = pageRefs.current[clamped - 1]
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const handlePageInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setPageInputValue(e.target.value)
  }

  const handlePageInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const pg = parseInt(pageInputValue, 10)
      if (!isNaN(pg)) goToPage(pg)
    }
  }

  const handlePageInputBlur = () => {
    setPageInputValue(String(currentPage))
  }

  // ── Render ────────────────────────────────────────────────────

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

  const zoomPercent = Math.round(scale * 100)
  const canZoomIn = scale < ZOOM_STEPS[ZOOM_STEPS.length - 1]
  const canZoomOut = scale > ZOOM_STEPS[0]

  return (
    <div className="pdf-viewer-wrapper">
      {/* Toolbar */}
      <div className="pdf-viewer-toolbar">
        <span>
          PDF Viewer
        </span>
        <div className="pdf-tb-sep" />
        {/* Prev / Next */}
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

        {/* Page input */}
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

        {/* Zoom */}
        {/* <button
          className="pdf-tb-btn"
          onClick={zoomOut}
          disabled={!canZoomOut}
          title="Thu nhỏ"
        >
          <ZoomOut size={16} />
        </button>
        <span className="pdf-tb-zoom-label">{zoomPercent}%</span>
        <button
          className="pdf-tb-btn"
          onClick={zoomIn}
          disabled={!canZoomIn}
          title="Phóng to"
        >
          <ZoomIn size={16} />
        </button>

        <div className="pdf-tb-sep" /> */}

        {/* Fit width / fit page */}
        {/* <button className="pdf-tb-btn" onClick={fitWidth} title="Vừa chiều rộng">
          <AlignJustify size={16} />
        </button>
        <button className="pdf-tb-btn" onClick={fitPage} title="Vừa trang">
          <Maximize2 size={16} />
        </button>

        <div className="pdf-tb-sep" /> */}

        {/* Rotate */}
        {/* <button className="pdf-tb-btn" onClick={rotateCw} title="Xoay 90°">
          <RotateCw size={16} />
        </button> */}

        {/* <div className="pdf-tb-sep" /> */}

        {/* Reload / Download / Open in new tab */}
        <button className="pdf-tb-btn" onClick={reload} title="Tải lại">
          <RefreshCw size={16} />
        </button>
        <button className="pdf-tb-btn" onClick={downloadPdf} disabled={!pdfDoc} title="Tải xuống PDF">
          <Download size={16} />
        </button>
        <button className="pdf-tb-btn" onClick={openInNewTab} disabled={!pdfDoc} title="Mở tab mới">
          <ExternalLink size={16} />
        </button>
      </div>

      {/* Scroll container */}
      <div className="pdf-scroll-container" ref={containerRef}>
        {renderedPages.map((pageNum) => (
          <div
            key={pageNum}
            className="pdf-page-wrapper"
            ref={(el) => { pageRefs.current[pageNum - 1] = el }}
          >
            <canvas ref={(el) => { canvasRefs.current[pageNum - 1] = el }} />
          </div>
        ))}
      </div>
    </div>
  )
}
