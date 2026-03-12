import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ChevronLeft, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api'
import TocTree from '../components/TocTree'
import TextContent from '../components/TextContent'
import PdfViewer from '../components/PdfViewer'
import type { VersionWorkspaceResponse, WorkspaceSectionNode, GuidelineVersionItem } from '../lib/types'

export default function ViewPage() {
  const { guidelineId, versionId } = useParams()
  const navigate = useNavigate()

  const [workspace, setWorkspace] = useState<VersionWorkspaceResponse | null>(null)
  const [targetVersionId, setTargetVersionId] = useState(versionId)
  const [versions, setVersions] = useState<GuidelineVersionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeSection, setActiveSection] = useState<WorkspaceSectionNode | null>(null)

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
            <span className="badge badge-active ml-2">Đang hiệu lực</span>
          )}
          {(workspace.suspect_section_count ?? 0) > 0 && (
            <span className="badge badge-draft" title={`Ngưỡng: ${workspace.suspect_score_threshold}`}>
              <AlertTriangle size={11} /> {workspace.suspect_section_count} mục cần kiểm tra
            </span>
          )}
        </div>
        <TextContent
          fullText={workspace.full_text}
          activeSection={activeSection}
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
