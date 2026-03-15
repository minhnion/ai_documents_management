import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ChevronLeft, AlertTriangle, Check, X } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import TocTree from '../components/TocTree'
import TextContent from '../components/TextContent'
import PdfViewer from '../components/PdfViewer'
import type { VersionWorkspaceResponse, WorkspaceSectionNode, GuidelineVersionItem } from '../lib/types'

export default function ViewPage() {
  const { guidelineId, versionId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()

  const [workspace, setWorkspace] = useState<VersionWorkspaceResponse | null>(null)
  const [targetVersionId, setTargetVersionId] = useState(versionId)
  const [versions, setVersions] = useState<GuidelineVersionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeSection, setActiveSection] = useState<WorkspaceSectionNode | null>(null)
  const [sectionEdits, setSectionEdits] = useState<Record<number, { content: string }>>({})
  const [savingSections, setSavingSections] = useState<Record<number, boolean>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  const canEdit = user?.role === 'editor' || user?.role === 'admin'

  useEffect(() => {
    setTargetVersionId(versionId)
  }, [versionId])

  useEffect(() => {
    if (!guidelineId || !targetVersionId) return

    setSectionEdits({})
    setLoading(true)
    Promise.all([
      api.get<VersionWorkspaceResponse>(`/versions/${targetVersionId}/workspace`),
      api.get<{ items: GuidelineVersionItem[] }>(`/guidelines/${guidelineId}/versions`)
    ]).then(([wsRes, vRes]) => {
      setWorkspace(wsRes.data)
      setVersions(vRes.data.items)
      if (targetVersionId !== versionId) {
        navigate(`/guidelines/${guidelineId}/versions/${targetVersionId}`, { replace: true })
      }
    }).catch(console.error)
      .finally(() => setLoading(false))
  }, [guidelineId, targetVersionId, navigate, versionId])

  const handleSaveSection = async (sectionId: number) => {
    if (!workspace) return
    setSavingSections(prev => ({ ...prev, [sectionId]: true }))
    setSaveError('')
    try {
      await api.patch(`/versions/${workspace.version.version_id}/sections/content`, {
        updates: [{
          section_id: sectionId,
          content: sectionEdits[sectionId]?.content ?? null,
          heading: null,
        }]
      })
      const wsRes = await api.get<VersionWorkspaceResponse>(`/versions/${workspace.version.version_id}/workspace`)
      setWorkspace(wsRes.data)
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
    if (!workspace) return
    const updates = Object.entries(sectionEdits).map(([id, val]) => ({
      section_id: Number(id),
      content: val.content,
      heading: null,
    }))
    if (updates.length === 0) return
    setSaving(true)
    setSaveError('')
    try {
      await api.patch(`/versions/${workspace.version.version_id}/sections/content`, { updates })
      const wsRes = await api.get<VersionWorkspaceResponse>(`/versions/${workspace.version.version_id}/workspace`)
      setWorkspace(wsRes.data)
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

  const handleSectionEditStart = (sectionId: number, currentContent: string) => {
    setSectionEdits(prev => ({
      ...prev,
      [sectionId]: { content: currentContent },
    }))
  }

  const handleSectionEditChange = (sectionId: number, value: string) => {
    setSectionEdits(prev => ({
      ...prev,
      [sectionId]: { content: value },
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

  const documentId = workspace?.documents[0]?.document_id ?? null

  if (loading) return <div className="loading-center"><span className="loading-spinner" /></div>
  if (!workspace) return <div className="empty-state">Không tìm thấy dữ liệu.</div>

  return (
    <div className="view-layout">
      {/* LEFT PANE: TOC */}
      <div className="toc-sidebar">
        <div className="toc-header">
          <button className="btn btn-ghost btn-xs" onClick={() => navigate('/guidelines')} style={{ marginRight: 8 }}>
            <ChevronLeft size={16} /> Quay lại
          </button>
          Mục lục
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
            <div className="p-4 text-sm text-muted">Chưa có mục lục.</div>
          ) : (
            <TocTree
              nodes={workspace.toc}
              activeId={activeSection?.section_id ?? null}
              onSelect={setActiveSection}
            />
          )}
        </div>
      </div>

      {/* CENTER PANE: TEXT */}
      <div className="content-pane">
        <div className="content-toolbar">
          <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>
            {workspace.guideline.title}
          </span>
          {workspace.version.status === 'active' && (
            <span className="badge badge-active" style={{ marginLeft: 8 }}>Active</span>
          )}
          {workspace.suspect_section_count > 0 && (
            <span className="badge badge-draft" title={`Ngưỡng: ${workspace.suspect_score_threshold}`}>
              <AlertTriangle size={11} /> {workspace.suspect_section_count} mục cần kiểm tra
            </span>
          )}
          {Object.keys(sectionEdits).length > 0 && (
            <>
                <button
                className="btn btn-primary btn-xs"
                disabled={saving}
                onClick={handleSaveAll}
                style={{ marginLeft: 'auto' }}
                >
                {saving
                  ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
                  : <><Check size={12} /> Lưu tất cả ({Object.keys(sectionEdits).length})</>
                }
                </button>
              <button
                className="btn btn-secondary btn-xs"
                disabled={saving}
                onClick={() => { setSectionEdits({}); setSaveError('') }}
              >
                <X size={12} /> Hủy
              </button>
            </>
          )}
        </div>
        {saveError && <div className="alert alert-error" style={{ margin: '8px 20px 0' }}>{saveError}</div>}
        <TextContent
          toc={workspace.toc}
          canEdit={canEdit}
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
      </div>

      {/* RIGHT PANE: PDF */}
      <div className="pdf-pane">
        <PdfViewer documentId={documentId} page={activeSection?.page_start ?? undefined} />
      </div>
    </div>
  )
}
