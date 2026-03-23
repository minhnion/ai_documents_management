import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ChevronLeft, AlertTriangle, Check, LoaderCircle, X, PanelRightClose, PanelRightOpen } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import TocTree from '../components/TocTree'
import TextContent from '../components/TextContent'
import PdfViewer from '../components/PdfViewer'
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

export default function ViewPage() {
  const { guidelineId, versionId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const contentPaneRef = useRef<HTMLDivElement | null>(null)
  const contentToggleButtonRef = useRef<HTMLButtonElement | null>(null)
  const focusWasInContentPaneRef = useRef(false)
  const pipelinePollTimerRef = useRef<number | null>(null)
  const chunkPollTimerRef = useRef<number | null>(null)

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
  const [chunkProgress, setChunkProgress] = useState<VersionChunkRebuildStatusResponse | null>(null)
  const [pipelineProgress, setPipelineProgress] = useState<VersionIngestionStatusResponse | null>(null)
  const [pipelineError, setPipelineError] = useState('')
  const [isContentPaneCollapsed, setIsContentPaneCollapsed] = useState(false)

  const canEdit = user?.role === 'editor' || user?.role === 'admin'
  const unsavedEditCount = useMemo(() => Object.keys(sectionEdits).length, [sectionEdits])
  const pipelineStatus = pipelineProgress?.status ?? 'idle'
  const pipelineIsActive = pipelineStatus === 'queued' || pipelineStatus === 'running'

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

  const loadWorkspaceData = useCallback(async (
    nextVersionId: string,
    options?: { suppressLoading?: boolean },
  ) => {
    if (!guidelineId) return

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
    } catch (error) {
      console.error(error)
      setWorkspace(null)
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
        pipelinePollTimerRef.current = window.setTimeout(() => {
          void pollPipelineStatus(nextVersionId, { refreshWorkspaceOnFinish: true })
        }, JOB_POLL_INTERVAL_MS)
        return
      }

      if ((data.status === 'succeeded' || data.status === 'failed') && refreshWorkspaceOnFinish) {
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
    const updates = Object.entries(sectionEdits).map(([id, val]) => ({
      section_id: Number(id),
      content: val.content,
      heading: val.heading,
    }))
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
              onSelect={setActiveSection}
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
          {workspace.version.status === 'processing' && (
            <span className="badge badge-draft" style={{ marginLeft: 8 }}>
              <LoaderCircle size={11} className="spin" /> Đang xử lý
            </span>
          )}
          {workspace.version.status === 'failed' && (
            <span className="badge badge-draft" style={{ marginLeft: 8 }}>Xử lý lỗi</span>
          )}
          {workspace.suspect_section_count > 0 && (
            <span className="badge badge-draft" title={`Ngưỡng: ${workspace.suspect_score_threshold}`}>
              <AlertTriangle size={11} /> {workspace.suspect_section_count} mục cần kiểm tra
            </span>
          )}
          <div className="content-toolbar-actions">
            {canEdit && (
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
        {!pipelineIsActive && workspace.toc.length === 0 ? (
          <div className="empty-state" style={{ minHeight: 240 }}>
            {workspace.version.status === 'failed'
              ? 'Pipeline tạo sections đã thất bại. Kiểm tra lỗi ở thông báo phía trên rồi thử lại tài liệu.'
              : 'Chưa có sections để hiển thị.'}
          </div>
        ) : (
          <TextContent
            toc={workspace.toc}
            canEdit={canEdit && !pipelineIsActive}
            activeSectionId={activeSection?.section_id ?? null}
            sectionEdits={sectionEdits}
            savingSections={
              saving
                ? Object.fromEntries(Object.keys(sectionEdits).map(id => [id, true]))
                : savingSections
            }
            onSectionEditStart={handleSectionEditStart}
            onSectionEditChange={handleSectionEditChange}
            onSaveSection={handleSaveSection}
            onCancelSection={handleCancelSection}
          />
        )}
      </div>

      <div className="pdf-pane">
        <PdfViewer
          documentId={documentId}
          page={activeSection?.page_start ?? undefined}
          pageJumpKey={activeSection?.section_id ?? null}
        />
      </div>
    </div>
  )
}
