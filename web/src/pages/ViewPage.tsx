import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  AlertTriangle,
  Check,
  Edit3,
  Eye,
  EyeOff,
  LoaderCircle,
  LocateFixed,
  X,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import TocTree from '../components/TocTree'
import TextContent from '../components/TextContent'
import PdfViewer from '../components/PdfViewer'
import { normalizeSectionContent } from '../components/sectionContent'
import type {
  GuidelineVersionItem,
  RebuildVersionChunksResponse,
  VersionChunkRebuildStatusResponse,
  VersionIngestionStatusResponse,
  VersionWorkspaceResponse,
  WorkspaceSectionNode,
} from '../lib/types'

type SectionEditDraft = {
  heading: string
  content: string
}

const JOB_POLL_INTERVAL_MS = 3000
const PDF_SYNC_SUPPRESS_MS = 1200
// Active band used by the PDF viewer to decide which section is currently
// being read. Tuned so the section highlighted in the TOC / middle pane
// matches the section that visually fills the centre of the PDF viewport.
const SPATIAL_VISIBLE_LOCATION_BIAS = 0.32
const OCR_VISIBLE_LOCATION_BIAS = 0.25
const SPATIAL_SECTION_HYSTERESIS = 0.015
const EMPTY_LEAF_NOTICE_COLLAPSED_LIMIT = 1

function clampNormalizedY(value: number | null | undefined): number {
  if (value == null || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(1, value))
}

function getPdfPageStart(node: WorkspaceSectionNode): number | null {
  return node.pdf_page_start ?? node.page_start ?? null
}

function getPdfPageEnd(node: WorkspaceSectionNode): number | null {
  return node.pdf_page_end ?? node.page_end ?? getPdfPageStart(node)
}

function getPdfStartY(node: WorkspaceSectionNode): number | null {
  return node.pdf_start_y ?? node.start_y ?? null
}

function getPdfEndY(node: WorkspaceSectionNode): number | null {
  return node.pdf_end_y ?? node.end_y ?? null
}

function getPdfAnchor(node: WorkspaceSectionNode): { page: number; y: number | null } | null {
  const page = getPdfPageStart(node)
  if (page == null || page <= 0) return null
  return { page, y: getPdfStartY(node) }
}

function getNearestPdfAnchor(
  node: WorkspaceSectionNode,
  nodes: WorkspaceSectionNode[],
): { page: number; y: number | null } | null {
  const directAnchor = getPdfAnchor(node)
  if (directAnchor) return directAnchor

  const currentIndex = nodes.findIndex(item => item.section_id === node.section_id)
  if (currentIndex < 0) return null

  for (let distance = 1; distance < nodes.length; distance += 1) {
    const previous = nodes[currentIndex - distance]
    if (previous) {
      const previousAnchor = getPdfAnchor(previous)
      if (previousAnchor) return previousAnchor
    }

    const next = nodes[currentIndex + distance]
    if (next) {
      const nextAnchor = getPdfAnchor(next)
      if (nextAnchor) return nextAnchor
    }
  }

  return null
}

function hasRenderableLandingAsset(node: WorkspaceSectionNode): boolean {
  if (!Array.isArray(node.landing_chunks)) return false
  return node.landing_chunks.some(entry => {
    if (!entry || typeof entry !== 'object') return false
    const imageUrl = (entry as Record<string, unknown>).image_url
    return typeof imageUrl === 'string' && imageUrl.trim().length > 0
  })
}

function isEmptyLeafSection(
  node: WorkspaceSectionNode,
  draft: SectionEditDraft | undefined,
): boolean {
  if ((node.children?.length ?? 0) > 0) return false
  if (hasRenderableLandingAsset(node)) return false
  return normalizeSectionContent(draft?.content ?? node.content).length === 0
}

function isSectionDraftDirty(
  node: WorkspaceSectionNode | undefined,
  draft: SectionEditDraft | undefined,
): boolean {
  if (!node || !draft) return false
  return draft.heading !== (node.heading ?? '') || draft.content !== (node.content ?? '')
}

function getSectionTitle(node: WorkspaceSectionNode): string {
  return node.heading?.trim() || `Mục ${node.section_id}`
}

function getSectionLocator(node: WorkspaceSectionNode): string {
  return node.section_path?.trim() || node.node_id?.trim() || `#${node.section_id}`
}

function formatChunkTimestamp(value: string | null | undefined): string {
  if (!value) return 'Chưa tạo chunk'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Chưa rõ lần tạo chunk gần nhất'
  return `Tạo chunk gần nhất: ${date.toLocaleString('vi-VN', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })}`
}

interface SpatialHighlightBbox {
  page: number
  left: number
  top: number
  right: number
  bottom: number
}

// Spatial PDFs only carry (page_start, page_end, start_y, end_y) per node and
// have no per-page bbox tree. Synthesise a full-width content bbox for each
// page in the section's range so the PDF viewer can pulse-highlight it the
// same way OCR mode highlights heading + content bboxes.
function buildSpatialHighlightBboxes(
  node: WorkspaceSectionNode | null,
): SpatialHighlightBbox[] {
  if (!node || node.page_start == null) return []
  const startPage = node.page_start
  const endPage = node.page_end ?? startPage
  const startY = clampNormalizedY(node.start_y)
  const endY = node.end_y == null ? 1 : clampNormalizedY(node.end_y)

  const out: SpatialHighlightBbox[] = []
  for (let page = startPage; page <= endPage; page += 1) {
    const top = page === startPage ? startY : 0
    const bottom = page === endPage ? endY : 1
    if (bottom <= top) continue
    out.push({
      page: page - 1, // PdfViewer expects 0-indexed page numbers
      left: 0,
      top,
      right: 1,
      bottom,
    })
  }
  return out
}

function flattenSectionNodes(nodes: WorkspaceSectionNode[]): WorkspaceSectionNode[] {
  const result: WorkspaceSectionNode[] = []
  for (const node of nodes) {
    result.push(node)
    if (node.children.length > 0) {
      result.push(...flattenSectionNodes(node.children))
    }
  }
  return result
}

function getPageSpan(node: WorkspaceSectionNode): number {
  const start = getPdfPageStart(node) ?? Number.MAX_SAFE_INTEGER
  const end = getPdfPageEnd(node) ?? start
  return end - start
}

function workspaceHasRenderableSections(data: VersionWorkspaceResponse | null | undefined): boolean {
  return Boolean(data && (data.section_count > 0 || data.toc.length > 0))
}

function nodeContainsLocation(
  node: WorkspaceSectionNode,
  page: number,
  normalizedY: number,
): boolean {
  const startPage = getPdfPageStart(node)
  if (startPage == null) {
    return false
  }

  const endPage = getPdfPageEnd(node) ?? startPage
  if (page < startPage || page > endPage) {
    return false
  }

  const startYRaw = getPdfStartY(node)
  const endYRaw = getPdfEndY(node)
  const hasStartY = startYRaw != null
  const hasEndY = endYRaw != null
  const startY = clampNormalizedY(startYRaw)
  const endY = clampNormalizedY(endYRaw)
  const currentY = clampNormalizedY(normalizedY)

  if (startPage === endPage) {
    if (!hasStartY && !hasEndY) {
      return true
    }
    return startY <= currentY && currentY <= endY
  }

  if (page === startPage && hasStartY && currentY < startY) {
    return false
  }

  if (page === endPage && hasEndY && currentY > endY) {
    return false
  }

  return true
}

function getLocationSpan(node: WorkspaceSectionNode): number {
  const startPage = getPdfPageStart(node)
  if (startPage == null) {
    return Number.MAX_SAFE_INTEGER
  }

  const endPage = getPdfPageEnd(node) ?? startPage
  if (startPage !== endPage) {
    return endPage - startPage + 1
  }

  const startY = getPdfStartY(node)
  const endY = getPdfEndY(node)
  if (startY == null && endY == null) {
    return 1
  }

  return Math.max(clampNormalizedY(endY) - clampNormalizedY(startY), 0)
}

function findBestSectionForPage(nodes: WorkspaceSectionNode[], page: number): WorkspaceSectionNode | null {
  const pagedNodes = nodes.filter(node => getPdfPageStart(node) != null)
  if (pagedNodes.length === 0) {
    return null
  }

  const containingNodes = pagedNodes
    .filter(node => {
      const start = getPdfPageStart(node) ?? 0
      const end = getPdfPageEnd(node) ?? start
      return start <= page && page <= end
    })
    .sort((left, right) => {
      const spanDiff = getPageSpan(left) - getPageSpan(right)
      if (spanDiff !== 0) return spanDiff
      const levelDiff = (right.level ?? 0) - (left.level ?? 0)
      if (levelDiff !== 0) return levelDiff
      return (getPdfPageStart(right) ?? 0) - (getPdfPageStart(left) ?? 0)
    })

  if (containingNodes.length > 0) {
    return containingNodes[0]
  }

  const precedingNodes = pagedNodes
    .filter(node => (getPdfPageStart(node) ?? 0) <= page)
    .sort((left, right) => {
      const startDiff = (getPdfPageStart(right) ?? 0) - (getPdfPageStart(left) ?? 0)
      if (startDiff !== 0) return startDiff
      return (right.level ?? 0) - (left.level ?? 0)
    })
  if (precedingNodes.length > 0) {
    return precedingNodes[0]
  }

  // No preceding section: user is before the first real section (e.g. cover page). Don't auto-select a future section — that would yank both panes onto a section the user hasn't asked for.
  return null
}

function findBestSectionForLocation(
  nodes: WorkspaceSectionNode[],
  page: number,
  normalizedY: number,
): WorkspaceSectionNode | null {
  const pagedNodes = nodes.filter(node => getPdfPageStart(node) != null)
  if (pagedNodes.length === 0) {
    return null
  }

  const containingNodes = pagedNodes
    .filter(node => nodeContainsLocation(node, page, normalizedY))
    .sort((left, right) => {
      const pageSpanDiff = getPageSpan(left) - getPageSpan(right)
      if (pageSpanDiff !== 0) return pageSpanDiff

      const locationSpanDiff = getLocationSpan(left) - getLocationSpan(right)
      if (locationSpanDiff !== 0) return locationSpanDiff

      const levelDiff = (right.level ?? 0) - (left.level ?? 0)
      if (levelDiff !== 0) return levelDiff

      const startPageDiff = (getPdfPageStart(right) ?? 0) - (getPdfPageStart(left) ?? 0)
      if (startPageDiff !== 0) return startPageDiff

      return clampNormalizedY(getPdfStartY(right)) - clampNormalizedY(getPdfStartY(left))
    })

  if (containingNodes.length > 0) {
    return containingNodes[0]
  }

  return findBestSectionForPage(nodes, page)
}

function nodeStartsBeforeLocation(
  node: WorkspaceSectionNode,
  page: number,
  normalizedY: number,
  hysteresis: number = 0,
): boolean {
  const startPage = getPdfPageStart(node)
  if (startPage == null) {
    return false
  }

  if (page > startPage) {
    return true
  }

  if (page < startPage) {
    return false
  }

  return clampNormalizedY(normalizedY) + hysteresis >= clampNormalizedY(getPdfStartY(node))
}

function findBestSpatialSectionForLocation(
  nodes: WorkspaceSectionNode[],
  page: number,
  normalizedY: number,
): WorkspaceSectionNode | null {
  const pagedNodes = nodes.filter(node => getPdfPageStart(node) != null)
  if (pagedNodes.length === 0) {
    return null
  }

  const startedNodes = pagedNodes
    .filter(node => nodeStartsBeforeLocation(node, page, normalizedY, SPATIAL_SECTION_HYSTERESIS))
    .sort((left, right) => {
      const startPageDiff = (getPdfPageStart(right) ?? 0) - (getPdfPageStart(left) ?? 0)
      if (startPageDiff !== 0) return startPageDiff

      const startYDiff = clampNormalizedY(getPdfStartY(right)) - clampNormalizedY(getPdfStartY(left))
      if (startYDiff !== 0) return startYDiff

      const levelDiff = (right.level ?? 0) - (left.level ?? 0)
      if (levelDiff !== 0) return levelDiff

      return getPageSpan(left) - getPageSpan(right)
    })

  if (startedNodes.length > 0) {
    return startedNodes[0]
  }

  // Before all sections → no auto-jump (consistent with findBestSectionForPage).
  return findBestSectionForPage(nodes, page)
}

interface EmptyLeafSectionsNoticeProps {
  sections: WorkspaceSectionNode[]
  activeSectionId: number | null
  canEdit: boolean
  onSelect: (node: WorkspaceSectionNode) => void
}

function EmptyLeafSectionsNotice({
  sections,
  activeSectionId,
  canEdit,
  onSelect,
}: EmptyLeafSectionsNoticeProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  if (sections.length === 0) return null

  const visibleSections = isExpanded
    ? sections
    : sections.slice(0, EMPTY_LEAF_NOTICE_COLLAPSED_LIMIT)
  const hiddenCount = Math.max(0, sections.length - visibleSections.length)

  return (
    <div className="empty-leaf-notice" role="region" aria-label="Mục chưa có nội dung">
      <div className="empty-leaf-notice-header">
        <span className="empty-leaf-notice-icon" aria-hidden>
          <AlertTriangle size={16} />
        </span>
        <div className="empty-leaf-notice-copy">
          <div className="empty-leaf-notice-title">
            Có {sections.length} mục lá chưa có nội dung
          </div>
          <div className="empty-leaf-notice-subtitle">
            Chọn một mục để mở đúng vị trí trong nội dung và PDF.
          </div>
        </div>
      </div>

      <div className="empty-leaf-list">
        {visibleSections.map(node => (
          <button
            key={node.section_id}
            type="button"
            className={`empty-leaf-item${node.section_id === activeSectionId ? ' empty-leaf-item--active' : ''}`}
            onClick={() => onSelect(node)}
            title={getSectionTitle(node)}
          >
            <span className="empty-leaf-item-main">
              <span className="empty-leaf-item-locator">{getSectionLocator(node)}</span>
              <span className="empty-leaf-item-title">{getSectionTitle(node)}</span>
            </span>
            <span className="empty-leaf-item-action">
              {canEdit ? <Edit3 size={12} /> : <LocateFixed size={12} />}
              {canEdit ? 'Sửa' : 'Mở'}
            </span>
          </button>
        ))}
      </div>

      {sections.length > EMPTY_LEAF_NOTICE_COLLAPSED_LIMIT && (
        <button
          type="button"
          className="btn btn-ghost btn-xs empty-leaf-toggle"
          onClick={() => setIsExpanded(prev => !prev)}
        >
          {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {isExpanded ? 'Thu gọn' : `Xem thêm ${hiddenCount} mục`}
        </button>
      )}
    </div>
  )
}

export default function ViewPage() {
  const { guidelineId, versionId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const contentPaneRef = useRef<HTMLDivElement | null>(null)
  const contentToggleButtonRef = useRef<HTMLButtonElement | null>(null)
  const focusWasInContentPaneRef = useRef(false)
  const pipelinePollTimerRef = useRef<number | null>(null)
  const chunkPollTimerRef = useRef<number | null>(null)
  const suppressPdfSyncUntilRef = useRef(0)
  const workspaceRef = useRef<VersionWorkspaceResponse | null>(null)
  const jumpRequestSequenceRef = useRef(0)

  const [workspace, setWorkspace] = useState<VersionWorkspaceResponse | null>(null)
  const [targetVersionId, setTargetVersionId] = useState(versionId)
  const [versions, setVersions] = useState<GuidelineVersionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeSection, setActiveSection] = useState<WorkspaceSectionNode | null>(null)
  const [sectionEdits, setSectionEdits] = useState<Record<number, SectionEditDraft>>({})
  const [savingSections, setSavingSections] = useState<Record<number, boolean>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [chunking, setChunking] = useState(false)
  const [chunkError, setChunkError] = useState('')
  const [chunkSuccess, setChunkSuccess] = useState('')
  const [showEmptyLeafNotice, setShowEmptyLeafNotice] = useState(true)
  const [chunkProgress, setChunkProgress] = useState<VersionChunkRebuildStatusResponse | null>(null)
  const [pipelineProgress, setPipelineProgress] = useState<VersionIngestionStatusResponse | null>(null)
  const [pipelineError, setPipelineError] = useState('')
  const [isContentPaneCollapsed, setIsContentPaneCollapsed] = useState(false)
  const [contentScrollBehavior, setContentScrollBehavior] = useState<ScrollBehavior | 'none'>('smooth')
  const [contentScrollKey, setContentScrollKey] = useState(0)
  const [tocRevealTarget, setTocRevealTarget] = useState<{ sectionId: number; key: number } | null>(null)
  const [pdfJumpState, setPdfJumpState] = useState<{ page?: number; y?: number | null; key: number | null }>({
    page: undefined,
    y: null,
    key: null,
  })

  const canEdit = Boolean(workspace?.can_edit)
  const pipelineStatus = pipelineProgress?.status ?? 'idle'
  const pipelineIsActive = pipelineStatus === 'queued' || pipelineStatus === 'running'
  const canEditSections = canEdit && !pipelineIsActive
  const flattenedSections = useMemo(() => flattenSectionNodes(workspace?.toc ?? []), [workspace?.toc])
  const sectionById = useMemo(() => {
    const next = new Map<number, WorkspaceSectionNode>()
    for (const node of flattenedSections) {
      next.set(node.section_id, node)
    }
    return next
  }, [flattenedSections])
  const dirtySectionUpdates = useMemo(
    () => Object.entries(sectionEdits)
      .map(([id, draft]) => ({
        section_id: Number(id),
        content: draft.content,
        heading: draft.heading,
        isDirty: isSectionDraftDirty(sectionById.get(Number(id)), draft),
      }))
      .filter(update => update.isDirty)
      .map(update => ({
        section_id: update.section_id,
        content: update.content,
        heading: update.heading,
      })),
    [sectionById, sectionEdits],
  )
  const dirtySectionIds = useMemo(
    () => new Set(dirtySectionUpdates.map(update => update.section_id)),
    [dirtySectionUpdates],
  )
  const unsavedEditCount = dirtySectionUpdates.length
  const emptyLeafSections = useMemo(
    () => flattenedSections.filter(node => isEmptyLeafSection(node, sectionEdits[node.section_id])),
    [flattenedSections, sectionEdits],
  )
  const positioningMode = workspace?.positioning_mode ?? 'page_range'
  const isSpatialPositioning = positioningMode === 'spatial_heading_anchor'
  const lastChunkCreatedAt = chunkProgress?.last_succeeded_at
    ?? (chunkProgress?.status === 'succeeded' ? chunkProgress.finished_at : null)

  const nextJumpRequestKey = () => {
    jumpRequestSequenceRef.current += 1
    return jumpRequestSequenceRef.current
  }

  const clearPipelinePolling = useCallback(() => {
    if (pipelinePollTimerRef.current !== null) {
      window.clearTimeout(pipelinePollTimerRef.current)
      pipelinePollTimerRef.current = null
    }
  }, [])

  const clearChunkPolling = useCallback(() => {
    if (chunkPollTimerRef.current !== null) {
      window.clearTimeout(chunkPollTimerRef.current)
      chunkPollTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    setTargetVersionId(versionId)
  }, [versionId])

  useEffect(() => {
    workspaceRef.current = workspace
  }, [workspace])

  const handleTocSelect = useCallback((
    node: WorkspaceSectionNode,
    options?: { revealInToc?: boolean },
  ) => {
    const jumpKey = nextJumpRequestKey()
    if (options?.revealInToc) {
      setTocRevealTarget({ sectionId: node.section_id, key: jumpKey })
    }
    setContentScrollBehavior('smooth')
    setContentScrollKey(jumpKey)
    setActiveSection(node)
    const anchor = getNearestPdfAnchor(node, flattenedSections)
    if (anchor) {
      suppressPdfSyncUntilRef.current = Date.now() + PDF_SYNC_SUPPRESS_MS
      setPdfJumpState({
        page: anchor.page,
        y: anchor.y,
        key: jumpKey,
      })
    }
  }, [flattenedSections])

  const handleContentVisibleSectionChange = useCallback((sectionId: number) => {
    if (unsavedEditCount > 0) return
    if (sectionId === activeSection?.section_id) return
    const next = flattenedSections.find(node => node.section_id === sectionId)
    if (!next) return
    // The middle pane already shows this section at the top of the viewport,
    // so don't fight it with another scrollIntoView.
    const jumpKey = nextJumpRequestKey()
    setContentScrollBehavior('none')
    setActiveSection(next)
    const anchor = getNearestPdfAnchor(next, flattenedSections)
    if (anchor) {
      suppressPdfSyncUntilRef.current = Date.now() + PDF_SYNC_SUPPRESS_MS
      setPdfJumpState({
        page: anchor.page,
        y: anchor.y,
        key: jumpKey,
      })
    }
  }, [activeSection?.section_id, flattenedSections, unsavedEditCount])

  const loadWorkspaceData = useCallback(async (
    nextVersionId: string,
    options?: { suppressLoading?: boolean },
  ): Promise<VersionWorkspaceResponse | null> => {
    if (!guidelineId) return null

    const suppressLoading = options?.suppressLoading ?? false
    if (!suppressLoading) {
      setLoading(true)
    }

    try {
      const [wsRes, vRes] = await Promise.all([
        api.get<VersionWorkspaceResponse>(`/versions/${nextVersionId}/workspace`),
        api.get<{ items: GuidelineVersionItem[] }>(`/guidelines/${guidelineId}/versions`),
      ])
      setWorkspace(wsRes.data)
      setVersions(vRes.data.items)
      setActiveSection(prev => {
        if (!prev) return null
        const stack = [...wsRes.data.toc]
        while (stack.length > 0) {
          const node = stack.pop()
          if (!node) continue
          if (node.section_id === prev.section_id) {
            return node
          }
          stack.push(...node.children)
        }
        return null
      })
      if (nextVersionId !== versionId) {
        navigate(`/guidelines/${guidelineId}/versions/${nextVersionId}`, { replace: true })
      }
      return wsRes.data
    } catch (error) {
      console.error(error)
      setWorkspace(null)
      return null
    } finally {
      if (!suppressLoading) {
        setLoading(false)
      }
    }
  }, [guidelineId, navigate, versionId])

  const pollPipelineStatus = useCallback(async (
    nextVersionId: string,
    options?: { refreshWorkspaceOnFinish?: boolean },
  ) => {
    clearPipelinePolling()
    const refreshWorkspaceOnFinish = options?.refreshWorkspaceOnFinish ?? false

    try {
      const response = await api.get<VersionIngestionStatusResponse>(
        `/versions/${nextVersionId}/pipeline/status`
      )
      const data = response.data
      setPipelineProgress(data)
      setPipelineError(data.status === 'failed' ? (data.error_message || 'Xử lý tài liệu thất bại.') : '')

      if (data.status === 'queued' || data.status === 'running') {
        if (workspaceHasRenderableSections(workspaceRef.current)) {
          setPipelineProgress({
            ...data,
            status: 'succeeded',
            version_status: workspaceRef.current?.version.status ?? data.version_status,
          })
          setPipelineError('')
          return
        }

        if (refreshWorkspaceOnFinish) {
          const refreshedWorkspace = await loadWorkspaceData(nextVersionId, { suppressLoading: true })
          if (workspaceHasRenderableSections(refreshedWorkspace)) {
            setPipelineProgress({
              ...data,
              status: 'succeeded',
              version_status: refreshedWorkspace?.version.status ?? data.version_status,
            })
            setPipelineError('')
            return
          }
        }

        pipelinePollTimerRef.current = window.setTimeout(() => {
          void pollPipelineStatus(nextVersionId, { refreshWorkspaceOnFinish: true })
        }, JOB_POLL_INTERVAL_MS)
        return
      }

      if (data.status === 'succeeded' || data.status === 'failed') {
        await loadWorkspaceData(nextVersionId, { suppressLoading: true })
      }
    } catch (error: any) {
      console.error(error)
      setPipelineError(error.response?.data?.detail || 'Không thể kiểm tra trạng thái xử lý tài liệu.')
    }
  }, [clearPipelinePolling, loadWorkspaceData])

  const pollChunkRebuildStatus = useCallback(async (
    nextVersionId: string,
    options?: { showTerminalMessage?: boolean },
  ) => {
    clearChunkPolling()
    const showTerminalMessage = options?.showTerminalMessage ?? true

    try {
      const response = await api.get<VersionChunkRebuildStatusResponse>(
        `/versions/${nextVersionId}/chunks/status`
      )
      const data = response.data
      setChunkProgress(data)

      if (data.status === 'queued' || data.status === 'running') {
        setChunking(true)
        setChunkError('')
        setChunkSuccess('')
        chunkPollTimerRef.current = window.setTimeout(() => {
          void pollChunkRebuildStatus(nextVersionId, { showTerminalMessage: true })
        }, JOB_POLL_INTERVAL_MS)
        return
      }

      setChunking(false)
      if (!showTerminalMessage) {
        return
      }

      if (data.status === 'failed') {
        setChunkError(data.error_message || 'Tạo chunks thất bại.')
        setChunkSuccess('')
      } else if (data.status === 'succeeded') {
        setChunkError('')
        setChunkSuccess(
          `Đã tạo chunks thành công. Xóa ${data.deleted_chunk_count} chunks cũ, tạo ${data.created_chunk_count} chunks mới.`
        )
      }
    } catch (error: any) {
      console.error(error)
      setChunking(false)
      setChunkError(error.response?.data?.detail || 'Không thể kiểm tra trạng thái tạo chunks.')
    }
  }, [clearChunkPolling])

  useEffect(() => {
    if (!guidelineId || !targetVersionId) return

    setSectionEdits({})
    setSaveError('')
    setChunkError('')
    setChunkSuccess('')
    setPipelineError('')
    setPipelineProgress(null)
    setChunkProgress(null)
    jumpRequestSequenceRef.current = 0
    setContentScrollKey(0)
    setPdfJumpState({ page: undefined, y: null, key: null })
    setContentScrollBehavior('smooth')
    suppressPdfSyncUntilRef.current = 0
    clearPipelinePolling()
    clearChunkPolling()

    void (async () => {
      await loadWorkspaceData(targetVersionId)
      await Promise.all([
        pollPipelineStatus(targetVersionId),
        pollChunkRebuildStatus(targetVersionId, { showTerminalMessage: false }),
      ])
    })()

    return () => {
      clearPipelinePolling()
      clearChunkPolling()
    }
  }, [guidelineId, targetVersionId, loadWorkspaceData, pollPipelineStatus, pollChunkRebuildStatus, clearPipelinePolling, clearChunkPolling])

  const handlePdfVisibleLocationChange = useCallback((visiblePage: number, normalizedY: number) => {
    if (Date.now() < suppressPdfSyncUntilRef.current) return
    if (unsavedEditCount > 0) return
    const nextSection = isSpatialPositioning
      ? findBestSpatialSectionForLocation(flattenedSections, visiblePage, normalizedY)
      : findBestSectionForLocation(flattenedSections, visiblePage, normalizedY)
    if (!nextSection) return
    if (nextSection.section_id === activeSection?.section_id) return
    setContentScrollBehavior('auto')
    setActiveSection(nextSection)
  }, [activeSection?.section_id, flattenedSections, isSpatialPositioning, unsavedEditCount])

  useEffect(() => {
    if (!pipelineIsActive || !workspaceHasRenderableSections(workspace)) return

    clearPipelinePolling()
    setPipelineError('')
    setPipelineProgress(prev => prev ? ({
      ...prev,
      status: 'succeeded',
      version_status: workspace?.version.status ?? prev.version_status,
    }) : prev)
  }, [clearPipelinePolling, pipelineIsActive, workspace])

  useEffect(() => {
    const pane = contentPaneRef.current
    if (!pane) return

    if (isContentPaneCollapsed) {
      pane.setAttribute('inert', '')
      return
    }

    pane.removeAttribute('inert')
  }, [isContentPaneCollapsed])

  const handleSaveSection = async (sectionId: number) => {
    if (!workspace || pipelineIsActive) return
    const draft = sectionEdits[sectionId]
    if (!draft) return
    if (!isSectionDraftDirty(sectionById.get(sectionId), draft)) {
      setSectionEdits(prev => {
        const next = { ...prev }
        delete next[sectionId]
        return next
      })
      return
    }
    setSavingSections(prev => ({ ...prev, [sectionId]: true }))
    setSaveError('')
    setChunkError('')
    setChunkSuccess('')
    try {
      await api.patch(`/versions/${workspace.version.version_id}/sections/content`, {
        updates: [{
          section_id: sectionId,
          content: draft.content,
          heading: draft.heading,
        }],
      })
      await loadWorkspaceData(String(workspace.version.version_id), { suppressLoading: true })
      setSectionEdits(prev => {
        const next = { ...prev }
        delete next[sectionId]
        return next
      })
    } catch (err: any) {
      setSaveError(err.response?.data?.detail || 'Lỗi khi lưu nội dung.')
    } finally {
      setSavingSections(prev => {
        const next = { ...prev }
        delete next[sectionId]
        return next
      })
    }
  }

  const handleSaveAll = async () => {
    if (!workspace || pipelineIsActive) return
    const updates = dirtySectionUpdates
    if (updates.length === 0) return
    setSaving(true)
    setSaveError('')
    setChunkError('')
    setChunkSuccess('')
    try {
      await api.patch(`/versions/${workspace.version.version_id}/sections/content`, { updates })
      await loadWorkspaceData(String(workspace.version.version_id), { suppressLoading: true })
      const submittedIds = new Set(updates.map(u => u.section_id))
      setSectionEdits(prev => {
        const next = { ...prev }
        for (const id of submittedIds) delete next[id]
        return next
      })
    } catch (err: any) {
      setSaveError(err.response?.data?.detail || 'Lỗi khi lưu nội dung.')
    } finally {
      setSaving(false)
    }
  }

  const handleRebuildChunks = async () => {
    if (!workspace) return
    if (pipelineIsActive) {
      setChunkError('Hãy đợi hệ thống xử lý xong OCR, TOC và sections trước khi tạo chunks.')
      setChunkSuccess('')
      return
    }
    if (unsavedEditCount > 0) {
      setChunkError('Hãy lưu hoặc hủy các chỉnh sửa hiện tại trước khi tạo chunks.')
      setChunkSuccess('')
      return
    }
    setChunking(true)
    setChunkError('')
    setChunkSuccess('')
    try {
      const response = await api.post<RebuildVersionChunksResponse>(
        `/versions/${workspace.version.version_id}/chunks/rebuild`
      )
      setChunkProgress(response.data)
      if (!response.data.accepted && (response.data.status === 'queued' || response.data.status === 'running')) {
        chunkPollTimerRef.current = window.setTimeout(() => {
          void pollChunkRebuildStatus(String(workspace.version.version_id), { showTerminalMessage: true })
        }, JOB_POLL_INTERVAL_MS)
        return
      }
      await pollChunkRebuildStatus(String(workspace.version.version_id), { showTerminalMessage: true })
    } catch (err: any) {
      setChunking(false)
      setChunkError(err.response?.data?.detail || 'Lỗi khi tạo chunks từ dữ liệu sections.')
    }
  }

  const handleSectionEditStart = (
    sectionId: number,
    currentHeading: string,
    currentContent: string,
  ) => {
    setSectionEdits(prev => ({
      ...prev,
      [sectionId]: {
        heading: currentHeading,
        content: currentContent,
      },
    }))
  }

  const handleSectionEditChange = (
    sectionId: number,
    field: keyof SectionEditDraft,
    value: string,
  ) => {
    const applyDraftChange = (draft: SectionEditDraft): SectionEditDraft => (
      field === 'heading'
        ? { ...draft, heading: value }
        : { ...draft, content: value }
    )

    setSectionEdits(prev => ({
      ...prev,
      [sectionId]: applyDraftChange(prev[sectionId] ?? { heading: '', content: '' }),
    }))
  }

  const handleCancelSection = (sectionId: number) => {
    setSectionEdits(prev => {
      const next = { ...prev }
      delete next[sectionId]
      return next
    })
    setSaveError('')
  }

  const handleEmptyLeafSectionSelect = (node: WorkspaceSectionNode) => {
    handleTocSelect(node, { revealInToc: true })
    if (!canEditSections || sectionEdits[node.section_id]) return
    handleSectionEditStart(
      node.section_id,
      node.heading ?? '',
      node.content ?? '',
    )
  }

  const handleContentTogglePointerDown = () => {
    focusWasInContentPaneRef.current = contentPaneRef.current?.contains(document.activeElement) ?? false
  }

  const handleContentToggle = () => {
    const focusWasInContentPane = focusWasInContentPaneRef.current
      || (contentPaneRef.current?.contains(document.activeElement) ?? false)

    setIsContentPaneCollapsed(prev => !prev)

    if (focusWasInContentPane && !isContentPaneCollapsed) {
      window.requestAnimationFrame(() => {
        contentToggleButtonRef.current?.focus()
      })
    }

    focusWasInContentPaneRef.current = false
  }

  const documentId = workspace?.documents[0]?.document_id ?? null

  if (loading) return <div className="loading-center"><span className="loading-spinner" /></div>
  if (!workspace) return <div className="empty-state">Không tìm thấy dữ liệu.</div>

  return (
    <div className={isContentPaneCollapsed ? 'view-layout view-layout--content-collapsed' : 'view-layout'}>
      <div className="toc-sidebar">
        <div className="toc-header">
          <button className="btn btn-ghost btn-xs toc-header-back" onClick={() => navigate('/guidelines')}>
            <ChevronLeft size={16} /> Quay lại
          </button>
          <h2 className="toc-header-title">Mục lục</h2>
          <div className="toc-header-actions">
            <button
              ref={contentToggleButtonRef}
              type="button"
              className="btn btn-ghost btn-xs toc-header-toggle"
              aria-label="Bật/tắt panel nội dung"
              aria-controls="viewer-content-pane"
              aria-pressed={isContentPaneCollapsed}
              title={isContentPaneCollapsed ? 'Hiện nội dung' : 'Ẩn nội dung'}
              onPointerDown={handleContentTogglePointerDown}
              onClick={handleContentToggle}
            >
              {isContentPaneCollapsed ? <PanelRightOpen size={16} /> : <PanelRightClose size={16} />}
            </button>
          </div>
        </div>
        <div className="version-bar">
          <span className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>Phiên bản:</span>
          <select
            value={targetVersionId}
            onChange={e => setTargetVersionId(e.target.value)}
            style={{ flex: 1 }}
          >
            {versions.map(v => (
              <option key={v.version_id} value={v.version_id}>
                {v.version_label || `v${v.version_id}`} {v.status === 'active' ? '(Hiện hành)' : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="toc-body">
          {workspace.toc.length === 0 ? (
            <div className="p-4 text-sm text-muted">
              {pipelineIsActive ? 'Đang xử lý OCR, TOC và sections cho tài liệu này.' : 'Chưa có mục lục.'}
            </div>
          ) : (
            <TocTree
              nodes={workspace.toc}
              activeId={activeSection?.section_id ?? null}
              onSelect={handleTocSelect}
              revealTargetId={tocRevealTarget?.sectionId ?? null}
              revealRequestKey={tocRevealTarget?.key ?? null}
            />
          )}
        </div>
      </div>

      <div
        id="viewer-content-pane"
        ref={contentPaneRef}
        className="content-pane"
        role="region"
        aria-label="Nội dung hướng dẫn"
        aria-hidden={isContentPaneCollapsed}
      >
        <div className="content-toolbar">
          <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>
            {workspace.guideline.title}
          </span>
          {workspace.version.status === 'active' && (
            <span className="badge badge-active" style={{ marginLeft: 8 }}>Active</span>
          )}
          {pipelineIsActive && (
            <span className="badge badge-draft" style={{ marginLeft: 8 }}>
              <LoaderCircle size={11} className="spin" /> Đang xử lý
            </span>
          )}
          {!pipelineIsActive && (pipelineStatus === 'failed' || workspace.version.status === 'failed') && (
            <span className="badge badge-draft" style={{ marginLeft: 8 }}>Xử lý lỗi</span>
          )}
          {workspace.suspect_section_count > 0 && (
            <span className="badge badge-draft" title={`Ngưỡng: ${workspace.suspect_score_threshold}`}>
              <AlertTriangle size={11} /> {workspace.suspect_section_count} mục cần kiểm tra
            </span>
          )}
          <div className="content-toolbar-actions">
            {!pipelineIsActive && emptyLeafSections.length > 0 && (
              <button
                className="btn btn-secondary btn-xs"
                onClick={() => setShowEmptyLeafNotice(prev => !prev)}
                title={showEmptyLeafNotice ? 'Tắt thông báo mục lá chưa có nội dung' : 'Mở thông báo mục lá chưa có nội dung'}
              >
                {showEmptyLeafNotice
                  ? <><EyeOff size={12} /> Tắt thông báo</>
                  : <><Eye size={12} /> Mở thông báo</>
                }
              </button>
            )}
            {canEdit && (
              <div className="chunk-rebuild-control">
                <button
                  className="btn btn-secondary btn-xs"
                  disabled={chunking || saving || unsavedEditCount > 0 || pipelineIsActive}
                  onClick={handleRebuildChunks}
                  title={
                    pipelineIsActive
                      ? 'Hãy đợi hệ thống xử lý xong OCR, TOC và sections trước khi tạo chunks.'
                      : unsavedEditCount > 0
                        ? 'Hãy lưu hoặc hủy chỉnh sửa trước khi tạo chunks.'
                        : 'Tạo chunks từ dữ liệu sections hiện tại'
                  }
                >
                  {chunking
                    ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
                    : <><Check size={12} /> Tạo chunks</>
                  }
                </button>
                <span className="chunk-rebuild-meta">{formatChunkTimestamp(lastChunkCreatedAt)}</span>
              </div>
            )}
            {unsavedEditCount > 0 && (
              <>
                <button
                  className="btn btn-primary btn-xs"
                  disabled={saving || chunking || pipelineIsActive}
                  onClick={handleSaveAll}
                >
                  {saving
                    ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
                    : <><Check size={12} /> Lưu tất cả ({unsavedEditCount})</>
                  }
                </button>
                <button
                  className="btn btn-secondary btn-xs"
                  disabled={saving || chunking || pipelineIsActive}
                  onClick={() => { setSectionEdits({}); setSaveError('') }}
                >
                  <X size={12} /> Hủy
                </button>
              </>
            )}
          </div>
        </div>
        {pipelineIsActive && (
          <div className="alert alert-info" style={{ margin: '8px 20px 0' }}>
            <LoaderCircle size={16} className="spin" />
            {pipelineStatus === 'queued'
              ? 'Đã tiếp nhận tài liệu. Hệ thống đang xếp hàng xử lý OCR, TOC và sections.'
              : 'Đang xử lý OCR, TOC và sections. Trang này sẽ tự cập nhật khi hoàn tất.'}
          </div>
        )}
        {!pipelineIsActive && pipelineError && (
          <div className="alert alert-error" style={{ margin: '8px 20px 0' }}>{pipelineError}</div>
        )}
        {saveError && <div className="alert alert-error" style={{ margin: '8px 20px 0' }}>{saveError}</div>}
        {chunkError && <div className="alert alert-error" style={{ margin: '8px 20px 0' }}>{chunkError}</div>}
        {chunkSuccess && <div className="alert alert-success" style={{ margin: '8px 20px 0' }}>{chunkSuccess}</div>}
        {!pipelineIsActive && showEmptyLeafNotice && emptyLeafSections.length > 0 && (
          <EmptyLeafSectionsNotice
            sections={emptyLeafSections}
            activeSectionId={activeSection?.section_id ?? null}
            canEdit={canEditSections}
            onSelect={handleEmptyLeafSectionSelect}
          />
        )}
        {!pipelineIsActive && workspace.toc.length === 0 ? (
          <div className="empty-state" style={{ minHeight: 240 }}>
            {workspace.version.status === 'failed'
              ? 'Pipeline tạo sections đã thất bại. Kiểm tra lỗi ở thông báo phía trên rồi thử lại tài liệu.'
              : 'Chưa có sections để hiển thị.'}
          </div>
        ) : (
          <TextContent
            toc={workspace.toc}
            canEdit={canEditSections}
            activeSectionId={activeSection?.section_id ?? null}
            activeSectionScrollKey={contentScrollKey}
            activeSectionScrollBehavior={contentScrollBehavior}
            sectionEdits={sectionEdits}
            savingSections={
              saving
                ? Object.fromEntries(Array.from(dirtySectionIds).map(id => [id, true]))
                : savingSections
            }
            onSectionEditStart={handleSectionEditStart}
            onSectionEditChange={handleSectionEditChange}
            onSaveSection={handleSaveSection}
            onCancelSection={handleCancelSection}
            onVisibleSectionChange={handleContentVisibleSectionChange}
          />
        )}
      </div>

      <div className="pdf-pane">
        <PdfViewer
          documentId={documentId}
          page={pdfJumpState.page}
          pageY={pdfJumpState.y}
          pageJumpKey={pdfJumpState.key}
          visibleLocationBias={isSpatialPositioning ? SPATIAL_VISIBLE_LOCATION_BIAS : OCR_VISIBLE_LOCATION_BIAS}
          highlightKey={activeSection ? `${activeSection.section_id}:${pdfJumpState.key ?? 'active'}` : null}
          highlightHeadingBbox={isSpatialPositioning ? null : (activeSection?.heading_bbox ?? null)}
          highlightContentBboxes={
            isSpatialPositioning
              ? (buildSpatialHighlightBboxes(activeSection) as unknown as Record<string, unknown>[])
              : (activeSection?.content_bboxes ?? [])
          }
          onVisibleLocationChange={handlePdfVisibleLocationChange}
        />
      </div>
    </div>
  )
}
