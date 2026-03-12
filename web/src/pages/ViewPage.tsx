import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ChevronLeft, AlertTriangle, Trash2, Edit3, Check, X } from 'lucide-react'
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
  const [deletingVersion, setDeletingVersion] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [sectionEdits, setSectionEdits] = useState<Record<number, { content: string | null; heading: string | null }>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  useEffect(() => {
    setTargetVersionId(versionId)
  }, [versionId])

  useEffect(() => {
    if (!guidelineId || !targetVersionId) return

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

  const handleDeleteVersion = async () => {
    if (!workspace) return
    const versionLabel = workspace.version.version_label || `v${workspace.version.version_id}`
    if (!window.confirm(`Xóa phiên bản "${versionLabel}"? Thao tác này không thể hoàn tác.`)) return
    setDeletingVersion(true)
    try {
      const res = await api.delete<{ guideline_id: number; promoted_version_id: number | null; remaining_version_count: number }>(
        `/versions/${workspace.version.version_id}`
      )
      if (res.data.remaining_version_count === 0 || res.data.promoted_version_id === null) {
        navigate('/guidelines')
      } else {
        navigate(`/guidelines/${res.data.guideline_id}/versions/${res.data.promoted_version_id}`, { replace: true })
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Không thể xóa phiên bản.')
      setDeletingVersion(false)
    }
  }

  const handleSaveSectionEdits = async () => {
    if (!workspace) return
    const updates = Object.entries(sectionEdits).map(([id, val]) => ({
      section_id: Number(id),
      content: val.content,
      heading: val.heading,
    }))
    if (updates.length === 0) { setEditMode(false); return }
    setSaving(true)
    setSaveError('')
    try {
      await api.patch(`/versions/${workspace.version.version_id}/sections/content`, { updates })
      // Re-fetch workspace after save
      const wsRes = await api.get(`/versions/${workspace.version.version_id}/workspace`)
      setWorkspace(wsRes.data)
      setEditMode(false)
      setSectionEdits({})
    } catch (err: any) {
      setSaveError(err.response?.data?.detail || 'Lỗi khi lưu nội dung.')
    } finally {
      setSaving(false)
    }
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
          {(user?.role === 'editor' || user?.role === 'admin') && (
            <button
              className="btn btn-danger btn-xs"
              disabled={deletingVersion}
              onClick={handleDeleteVersion}
              title="Xóa phiên bản này"
            >
              {deletingVersion ? <span className="loading-spinner" style={{ width: 12, height: 12 }} /> : <Trash2 size={13} />}
            </button>
          )}
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
            <span className="badge badge-active" style={{ marginLeft: 8 }}>Đang hiệu lực</span>
          )}
          {workspace.suspect_section_count > 0 && (
            <span className="badge badge-draft" title={`Ngưỡng: ${workspace.suspect_score_threshold}`}>
              <AlertTriangle size={11} /> {workspace.suspect_section_count} mục cần kiểm tra
            </span>
          )}
          {(user?.role === 'editor' || user?.role === 'admin') && !editMode && (
            <button className="btn btn-secondary btn-xs" onClick={() => setEditMode(true)}>
              <Edit3 size={12} /> Chỉnh sửa
            </button>
          )}
          {editMode && (
            <>
              <button className="btn btn-primary btn-xs" disabled={saving} onClick={handleSaveSectionEdits}>
                {saving ? <span className="loading-spinner" style={{ width: 12, height: 12 }} /> : <><Check size={12} /> Lưu thay đổi</>}
              </button>
              <button className="btn btn-secondary btn-xs" disabled={saving} onClick={() => { setEditMode(false); setSectionEdits({}) }}>
                <X size={12} /> Hủy
              </button>
            </>
          )}
        </div>
        {saveError && <div className="alert alert-error" style={{ margin: '8px 20px 0' }}>{saveError}</div>}
        <TextContent
          fullText={workspace.full_text}
          activeSection={activeSection}
          editMode={editMode}
          sectionEdits={sectionEdits}
          onSectionEdit={(sectionId, field, value) =>
            setSectionEdits(prev => ({
              ...prev,
              [sectionId]: { ...prev[sectionId], content: prev[sectionId]?.content ?? null, heading: prev[sectionId]?.heading ?? null, [field]: value }
            }))
          }
          toc={workspace.toc}
        />
      </div>

      {/* RIGHT PANE: PDF */}
      <div className="pdf-pane">
        <div className="pdf-toolbar">
          <span>Tài liệu gốc (PDF)</span>
        </div>
        <PdfViewer documentId={documentId} />
      </div>
    </div>
  )
}
