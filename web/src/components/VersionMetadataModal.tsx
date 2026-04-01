import { useEffect, useState } from 'react'
import { Save, X } from 'lucide-react'
import { api } from '../lib/api'
import type {
  GuidelineVersionItem,
  UpdateGuidelineVersionMetadataResponse,
} from '../lib/types'

interface Props {
  guidelineTitle: string
  version: GuidelineVersionItem
  onClose: () => void
  onSaved: (updated: UpdateGuidelineVersionMetadataResponse) => void
}

const VERSION_STATUS_OPTIONS = ['active', 'inactive']

export default function VersionMetadataModal({ guidelineTitle, version, onClose, onSaved }: Props) {
  const [versionLabel, setVersionLabel] = useState(version.version_label ?? '')
  const [releaseDate, setReleaseDate] = useState(version.release_date ?? '')
  const [effectiveFrom, setEffectiveFrom] = useState(version.effective_from ?? '')
  const [effectiveTo, setEffectiveTo] = useState(version.effective_to ?? '')
  const [status, setStatus] = useState(version.status === 'active' ? 'active' : 'inactive')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, submitting])

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')

    const payload = {
      version_label: versionLabel,
      release_date: releaseDate || null,
      effective_from: effectiveFrom || null,
      effective_to: effectiveTo || null,
      status: status || null,
    }

    try {
      const response = await api.patch<UpdateGuidelineVersionMetadataResponse>(
        `/versions/${version.version_id}`,
        payload,
      )
      onSaved(response.data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Không thể cập nhật metadata phiên bản.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="modal-overlay"
      onClick={event => {
        event.stopPropagation()
        if (!submitting) onClose()
      }}
    >
      <div className="modal-container metadata-modal" onClick={event => event.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">Cập nhật metadata phiên bản</span>
          <span className="modal-subtitle">{guidelineTitle}</span>
          <button className="modal-close-btn" onClick={onClose} title="Đóng" disabled={submitting}>
            <X size={16} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="modal-body metadata-modal-body">
          {error && <div className="alert alert-error">{error}</div>}
          <div className="metadata-form-grid">
            <div className="form-group">
              <label className="form-label">Nhãn phiên bản</label>
              <input
                type="text"
                className="form-input"
                value={versionLabel}
                onChange={event => setVersionLabel(event.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Trạng thái</label>
              <select
                className="form-select"
                value={status}
                onChange={event => setStatus(event.target.value)}
                disabled={submitting}
              >
                {VERSION_STATUS_OPTIONS.map(option => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Ngày phát hành</label>
              <input
                type="date"
                className="form-input"
                value={releaseDate}
                onChange={event => setReleaseDate(event.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Hiệu lực từ</label>
              <input
                type="date"
                className="form-input"
                value={effectiveFrom}
                onChange={event => setEffectiveFrom(event.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Hiệu lực đến</label>
              <input
                type="date"
                className="form-input"
                value={effectiveTo}
                onChange={event => setEffectiveTo(event.target.value)}
                disabled={submitting}
              />
            </div>
          </div>
          <div className="metadata-modal-footnote">
            Khi chuyển phiên bản này thành <code>active</code>, các phiên bản active khác sẽ bị hạ xuống
            <code> inactive</code>. Khi hạ từ active xuống inactive, hệ thống sẽ tự promote phiên bản gần nhất còn lại nếu có lên active.
          </div>
          <div className="modal-footer metadata-modal-footer">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onClose}
              disabled={submitting}
            >
              Hủy
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={submitting}>
              {submitting
                ? <span className="loading-spinner" style={{ width: 14, height: 14 }} />
                : <><Save size={14} /> Lưu phiên bản</>
              }
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
