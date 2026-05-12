import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import {
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Download,
  ExternalLink,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { api } from '../lib/api'

function installPdfJsCollectionPolyfills() {
  const mapProto = Map.prototype as Map<unknown, unknown> & {
    getOrInsert?: (key: unknown, value: unknown) => unknown
    getOrInsertComputed?: (key: unknown, factory: (key: unknown) => unknown) => unknown
  }
  const weakMapProto = WeakMap.prototype as WeakMap<object, unknown> & {
    getOrInsert?: (key: object, value: unknown) => unknown
    getOrInsertComputed?: (key: object, factory: (key: object) => unknown) => unknown
  }

  if (typeof mapProto.getOrInsert !== 'function') {
    Object.defineProperty(Map.prototype, 'getOrInsert', {
      value(this: Map<unknown, unknown>, key: unknown, value: unknown) {
        if (this.has(key)) return this.get(key)
        this.set(key, value)
        return value
      },
      configurable: true,
      writable: true,
    })
  }

  if (typeof mapProto.getOrInsertComputed !== 'function') {
    Object.defineProperty(Map.prototype, 'getOrInsertComputed', {
      value(this: Map<unknown, unknown>, key: unknown, factory: (key: unknown) => unknown) {
        if (this.has(key)) return this.get(key)
        const value = factory(key)
        this.set(key, value)
        return value
      },
      configurable: true,
      writable: true,
    })
  }

  if (typeof weakMapProto.getOrInsert !== 'function') {
    Object.defineProperty(WeakMap.prototype, 'getOrInsert', {
      value(this: WeakMap<object, unknown>, key: object, value: unknown) {
        if (this.has(key)) return this.get(key)
        this.set(key, value)
        return value
      },
      configurable: true,
      writable: true,
    })
  }

  if (typeof weakMapProto.getOrInsertComputed !== 'function') {
    Object.defineProperty(WeakMap.prototype, 'getOrInsertComputed', {
      value(
        this: WeakMap<object, unknown>,
        key: object,
        factory: (key: object) => unknown
      ) {
        if (this.has(key)) return this.get(key)
        const value = factory(key)
        this.set(key, value)
        return value
      },
      configurable: true,
      writable: true,
    })
  }
}

installPdfJsCollectionPolyfills()

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

const PDFJS_VERSION = '5.5.207'
const PDFJS_WASM_URL = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/wasm/`

const DEFAULT_SCALE = 1.25
const ZOOM_STEP = 0.1
const MIN_ZOOM_LEVEL = -5
const MAX_ZOOM_LEVEL = 8
const PDF_PAGE_HORIZONTAL_MARGIN = 32
const PDF_PAGE_MAX_WIDTH = 900
const DEFAULT_ROTATION = 0
const RANGE_CHUNK_SIZE = 256 * 1024

export const clampZoomLevel = (zoomLevel: number) => {
  return Math.max(MIN_ZOOM_LEVEL, Math.min(MAX_ZOOM_LEVEL, zoomLevel))
}

export const getZoomScale = (fitScale: number, zoomLevel: number) => {
  return fitScale * (1 + clampZoomLevel(zoomLevel) * ZOOM_STEP)
}

type PdfBboxLike = Record<string, unknown> | null | undefined

interface PdfViewerProps {
  documentId: number | null
  page?: number
  pageY?: number | null
  pageJumpKey?: number | null
  visibleLocationBias?: number
  highlightKey?: number | string | null
  highlightHeadingBbox?: PdfBboxLike
  highlightContentBboxes?: PdfBboxLike[]
  onVisiblePageChange?: (page: number) => void
  onVisibleLocationChange?: (page: number, normalizedY: number) => void
}

interface UsableBbox {
  page: number
  left: number
  top: number
  right: number
  bottom: number
}

function toUsableBbox(bbox: PdfBboxLike): UsableBbox | null {
  if (!bbox || typeof bbox !== 'object') return null
  const page = bbox.page
  const left = bbox.left
  const top = bbox.top
  const right = bbox.right
  const bottom = bbox.bottom
  if (
    typeof page !== 'number'
    || typeof left !== 'number'
    || typeof top !== 'number'
    || typeof right !== 'number'
    || typeof bottom !== 'number'
  ) return null
  if (right <= left || bottom <= top) return null
  return { page, left, top, right, bottom }
}

function bboxStyle(bbox: UsableBbox): React.CSSProperties {
  const left = Math.max(0, bbox.left)
  const top = Math.max(0, bbox.top)
  const width = Math.max(0, Math.min(1, bbox.right) - left)
  const height = Math.max(0, Math.min(1, bbox.bottom) - top)
  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${width * 100}%`,
    height: `${height * 100}%`,
  }
}

const isExpectedPdfLoadAbort = (error: unknown) => {
  if (!error || typeof error !== 'object') return false
  const maybeError = error as { name?: string; message?: string }
  return (
    maybeError.name === 'AbortException' ||
    maybeError.message === 'Worker was destroyed' ||
    maybeError.message?.includes('Worker was destroyed') === true
  )
}

function clampNormalizedY(value: number | null | undefined): number {
  if (value == null || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(1, value))
}

export default function PdfViewer({
  documentId,
  page,
  pageY,
  pageJumpKey,
  visibleLocationBias = 0,
  highlightKey = null,
  highlightHeadingBbox = null,
  highlightContentBboxes = [],
  onVisiblePageChange,
  onVisibleLocationChange,
}: PdfViewerProps) {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [numPages, setNumPages] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [renderedPages, setRenderedPages] = useState<number[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const [pageInputValue, setPageInputValue] = useState('1')
  const [reloadKey, setReloadKey] = useState(0)
  const [fitScale, setFitScale] = useState(DEFAULT_SCALE)
  const [zoomLevel, setZoomLevel] = useState(0)
  const [placeholderSize, setPlaceholderSize] = useState<{ width: number; height: number } | null>(null)

  const containerRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<(HTMLDivElement | null)[]>([])
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([])
  const renderingRef = useRef(false)
  const queuedRenderRef = useRef<{ doc: PDFDocumentProxy; scale: number } | null>(null)
  const currentDocRef = useRef<PDFDocumentProxy | null>(null)
  const observerRef = useRef<IntersectionObserver | null>(null)
  const currentPageRef = useRef(1)
  const lastReportedLocationRef = useRef<{ page: number; y: number } | null>(null)
  // True only after the user explicitly scrolled. TOC-driven jumps reset it so IntersectionObserver fires during programmatic scroll don't yank activeSection mid-flight.
  const userScrollingRef = useRef(false)

  const renderScale = getZoomScale(fitScale, zoomLevel)

  // Group highlight bboxes by page so each pdf-page-wrapper renders only its own.
  const highlightsByPage = useMemo(() => {
    const map = new Map<number, { heading: UsableBbox | null; contents: UsableBbox[] }>()
    const heading = toUsableBbox(highlightHeadingBbox)
    if (heading) {
      map.set(heading.page, {
        heading,
        contents: map.get(heading.page)?.contents ?? [],
      })
    }
    if (Array.isArray(highlightContentBboxes)) {
      for (const raw of highlightContentBboxes) {
        const bbox = toUsableBbox(raw)
        if (!bbox) continue
        const existing = map.get(bbox.page)
        if (existing) {
          existing.contents.push(bbox)
        } else {
          map.set(bbox.page, { heading: null, contents: [bbox] })
        }
      }
    }
    return map
  }, [highlightHeadingBbox, highlightContentBboxes])

  const getNormalizedVisibleY = useCallback((pageNumber: number) => {
    const container = containerRef.current
    const pageElement = pageRefs.current[pageNumber - 1]
    if (!container || !pageElement || pageElement.offsetHeight <= 0) {
      return 0
    }

    const biasPx = Math.max(container.clientHeight * Math.max(visibleLocationBias, 0), 0)
    const offsetWithinPage = container.scrollTop - pageElement.offsetTop + biasPx
    return clampNormalizedY(offsetWithinPage / pageElement.offsetHeight)
  }, [visibleLocationBias])

  const emitVisibleLocation = useCallback((pageNumber: number) => {
    if (!onVisibleLocationChange) return
    // Only forward to the parent when the user is actively scrolling. During TOC-driven jumps this stays false so IntersectionObserver hits along the way don't reopen the suppress window.
    if (!userScrollingRef.current) return

    const normalizedY = getNormalizedVisibleY(pageNumber)
    const previous = lastReportedLocationRef.current
    if (
      previous
      && previous.page === pageNumber
      && Math.abs(previous.y - normalizedY) < 0.01
    ) {
      return
    }

    lastReportedLocationRef.current = { page: pageNumber, y: normalizedY }
    onVisibleLocationChange(pageNumber, normalizedY)
  }, [getNormalizedVisibleY, onVisibleLocationChange])

  useEffect(() => {
    currentPageRef.current = currentPage
  }, [currentPage])

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
      wasmUrl: PDFJS_WASM_URL,
    }
  }, [documentId])

  const renderAllPages = useCallback(async (doc: PDFDocumentProxy, scale: number) => {
    if (renderingRef.current) {
      queuedRenderRef.current = { doc, scale }
      return
    }
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
            scale,
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
      const queuedRender = queuedRenderRef.current
      queuedRenderRef.current = null
      if (queuedRender && currentDocRef.current === queuedRender.doc) {
        void renderAllPages(queuedRender.doc, queuedRender.scale)
      }
    }
  }, [])

  const measureFitScale = useCallback(async (doc: PDFDocumentProxy) => {
    const container = containerRef.current
    if (!container) return DEFAULT_SCALE

    const firstPage = await doc.getPage(1)

    try {
      const viewport = firstPage.getViewport({
        scale: 1,
        rotation: DEFAULT_ROTATION,
      })
      const availableWidth = Math.min(
        Math.max(container.clientWidth - PDF_PAGE_HORIZONTAL_MARGIN, 0),
        PDF_PAGE_MAX_WIDTH
      )

      if (availableWidth <= 0 || viewport.width <= 0) {
        return DEFAULT_SCALE
      }

      return availableWidth / viewport.width
    } finally {
      if (typeof firstPage.cleanup === 'function') {
        firstPage.cleanup()
      }
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
    setFitScale(DEFAULT_SCALE)
    setZoomLevel(0)
    lastReportedLocationRef.current = null
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
        if (cancelled || isExpectedPdfLoadAbort(loadError)) {
          return
        }
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
  }, [buildPdfRequest, documentId, measureFitScale, reloadKey])

  useEffect(() => {
    if (!pdfDoc) return

    let cancelled = false

    ;(async () => {
      try {
        const nextFitScale = await measureFitScale(pdfDoc)
        if (!cancelled) {
          setFitScale(nextFitScale)
        }
      } catch (scaleError) {
        console.error('Failed measuring PDF fit scale', scaleError)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [measureFitScale, pdfDoc])

  useEffect(() => {
    if (!pdfDoc) return

    const totalPages = pdfDoc.numPages
    pageRefs.current = new Array(totalPages).fill(null)
    canvasRefs.current = new Array(totalPages).fill(null)
    setRenderedPages(Array.from({ length: totalPages }, (_, index) => index + 1))

    const timer = window.setTimeout(() => {
      void renderAllPages(pdfDoc, renderScale)
    }, 50)
    return () => window.clearTimeout(timer)
  }, [pdfDoc, renderAllPages, renderScale])

  // Pre-compute page wrapper dimensions so offsetTop is correct for every page from the start. Without this, unrendered pages have height 0 and TOC jumps to far pages land at the wrong scroll position.
  useEffect(() => {
    if (!pdfDoc) {
      setPlaceholderSize(null)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const firstPage = await pdfDoc.getPage(1)
        try {
          const viewport = firstPage.getViewport({ scale: renderScale, rotation: DEFAULT_ROTATION })
          if (!cancelled) {
            setPlaceholderSize({ width: viewport.width, height: viewport.height })
          }
        } finally {
          if (typeof firstPage.cleanup === 'function') {
            firstPage.cleanup()
          }
        }
      } catch (placeholderError) {
        if (!cancelled) {
          console.error('Failed measuring PDF placeholder dimensions', placeholderError)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [pdfDoc, renderScale])

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
        if (nextPage !== currentPageRef.current) {
          currentPageRef.current = nextPage
          setCurrentPage(nextPage)
          setPageInputValue(String(nextPage))
          onVisiblePageChange?.(nextPage)
          emitVisibleLocation(nextPage)
        }
      },
      { root: containerRef.current, threshold: [0, 0.25, 0.5, 0.75, 1.0] }
    )

    pageRefs.current.forEach((el) => {
      if (el) {
        observerRef.current?.observe(el)
      }
    })

    return () => observerRef.current?.disconnect()
  }, [emitVisibleLocation, onVisiblePageChange, renderedPages])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !onVisibleLocationChange) return

    let frameId: number | null = null

    const handleScroll = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId)
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null
        emitVisibleLocation(currentPageRef.current)
      })
    }

    const markUserScroll = () => { userScrollingRef.current = true }

    container.addEventListener('scroll', handleScroll, { passive: true })
    container.addEventListener('wheel', markUserScroll, { passive: true })
    container.addEventListener('touchstart', markUserScroll, { passive: true })
    container.addEventListener('keydown', markUserScroll)
    // Don't fire handleScroll on mount: scrollTop=0 at load would emit page 1 before the user has done anything.

    return () => {
      container.removeEventListener('scroll', handleScroll)
      container.removeEventListener('wheel', markUserScroll)
      container.removeEventListener('touchstart', markUserScroll)
      container.removeEventListener('keydown', markUserScroll)
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId)
      }
    }
  }, [emitVisibleLocation, onVisibleLocationChange, renderedPages])

  useEffect(() => {
    if (!page || page < 1) return
    setCurrentPage(page)
    setPageInputValue(String(page))
    const el = pageRefs.current[page - 1]
    const container = containerRef.current
    if (!el || !container) return

    // External jump (TOC click) — silence reverse-sync until the user scrolls again.
    userScrollingRef.current = false

    const normalizedTargetY = pageY == null ? null : clampNormalizedY(pageY)
    const targetTop = normalizedTargetY == null
      ? el.offsetTop
      : Math.max(
          el.offsetTop
            + el.offsetHeight * normalizedTargetY
            - Math.max(container.clientHeight * Math.max(visibleLocationBias, 0), 0)
            - 12,
          0,
        )

    // Smooth scroll across many pages takes longer than the reverse-sync suppress window, so IntersectionObserver fires mid-flight and yanks activeSection. Jump instantly for far targets to avoid that race.
    const distancePx = Math.abs(targetTop - container.scrollTop)
    const behavior: ScrollBehavior = distancePx > container.clientHeight * 2 ? 'auto' : 'smooth'
    container.scrollTo({ top: targetTop, behavior })
  }, [page, pageJumpKey, pageY, renderedPages, visibleLocationBias])

  const reload = () => {
    setReloadKey((value) => value + 1)
  }

  const zoomOut = () => {
    setZoomLevel((value) => clampZoomLevel(value - 1))
  }

  const zoomIn = () => {
    setZoomLevel((value) => clampZoomLevel(value + 1))
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
      userScrollingRef.current = false
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

  const isMinZoom = zoomLevel <= MIN_ZOOM_LEVEL
  const isMaxZoom = zoomLevel >= MAX_ZOOM_LEVEL

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

        <button
          className="pdf-tb-btn"
          onClick={zoomOut}
          disabled={!pdfDoc || isMinZoom}
          title="Thu nhỏ"
        >
          <ZoomOut size={16} />
        </button>
        <button
          className="pdf-tb-btn"
          onClick={zoomIn}
          disabled={!pdfDoc || isMaxZoom}
          title="Phóng to"
        >
          <ZoomIn size={16} />
        </button>

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
        {renderedPages.map((pageNumber) => {
          const pageHighlights = highlightsByPage.get(pageNumber - 1)
          return (
            <div
              key={pageNumber}
              className="pdf-page-wrapper"
              ref={(el) => {
                pageRefs.current[pageNumber - 1] = el
              }}
              style={placeholderSize ? {
                width: `${placeholderSize.width}px`,
                minHeight: `${placeholderSize.height}px`,
              } : undefined}
            >
              <canvas
                ref={(el) => {
                  canvasRefs.current[pageNumber - 1] = el
                }}
              />
              {pageHighlights && highlightKey != null && (
                <div
                  key={`hl-${pageNumber}-${highlightKey}`}
                  className="pdf-highlight-layer"
                >
                  {pageHighlights.heading && (
                    <div
                      className="pdf-highlight pdf-highlight--heading"
                      style={bboxStyle(pageHighlights.heading)}
                    />
                  )}
                  {pageHighlights.contents.map((bbox, index) => (
                    <div
                      key={`content-${index}`}
                      className="pdf-highlight pdf-highlight--content"
                      style={bboxStyle(bbox)}
                    />
                  ))}
                </div>
              )}
              <span className="pdf-page-label">{pageNumber}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
