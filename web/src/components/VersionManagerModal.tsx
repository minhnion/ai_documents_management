import { useEffect, useState } from 'react'
import { X, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import type { GuidelineVersionItem, DeleteGuidelineVersionResponse } from '../lib/types'

interface Props {
  guidelineId: number
  guidelineTitle: string
  onClose: () => void
  onVersionsChanged: () => void
}

export default function VersionManagerModal({ guidelineId, guidelineTitle, onClose, onVersionsChanged }: Props) {
  const [versions, setVersions] = useState<GuidelineVersionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [error, setError] = useState('')

  const fetchVersions = () => {
    setLoading(true)
    setError('')
    api.get<{ items: GuidelineVersionItem[] }>(`/guidelines/${guidelineId}/versions`)
      .then(res => setVersions(res.data.items))
      .catch(() => setError('Không thể tải danh sách phiên bản.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchVersions()
  }, [guidelineId])

  const handleDelete = async (version: GuidelineVersionItem) => {
    const label = version.version_label || `v${version.version_id}`
    if (!window.confirm(`Xóa phiên bản "${label}"? Thao tác này không thể hoàn tác.`)) return
    setDeletingId(version.version_id)
    setError('')
    try {
      const res = await api.delete<DeleteGuidelineVersionResponse>(`/versions/${version.version_id}`)
      if (res.data.remaining_version_count === 0) {
        onVersionsChanged()
        onClose()
      } else {
        fetchVersions()
        onVersionsChanged()
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Không thể xóa phiên bản.')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">Quản lý phiên bản</span>
          <span className="modal-subtitle">{guidelineTitle}</span>
          <button className="modal-close-btn" onClick={onClose} title="Đóng">
            <X size={16} />
          </button>
        </div>
        <div className="modal-body">
          {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
          {loading ? (
            <div className="loading-center"><span className="loading-spinner" /></div>
          ) : versions.length === 0 ? (
            <div className="empty-state">Không có phiên bản nào.</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Phiên bản</th>
                  <th>Ngày phát hành</th>
                  <th>Trạng thái</th>
                  <th className="text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {versions.map(v => (
                  <tr key={v.version_id}>
                    <td className="font-medium">{v.version_label || `v${v.version_id}`}</td>
                    <td>{v.release_date || '-'}</td>
                    <td>
                      {v.status === 'active'
                        ? <span className="badge badge-active">Hiện hành</span>
                        : <span className="badge badge-default">{v.status || '-'}</span>
                      }
                    </td>
                    <td>
                      <div className="actions-cell" style={{ justifyContent: 'flex-end' }}>
                        <button
                          className="btn btn-danger btn-sm"
                          title="Xóa phiên bản"
                          disabled={deletingId === v.version_id}
                          onClick={() => handleDelete(v)}
                        >
                          {deletingId === v.version_id
                            ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
                            : <Trash2 size={14} />
                          }
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary btn-sm" onClick={onClose}>Đóng</button>
        </div>
      </div>
    </div>
  )
}
