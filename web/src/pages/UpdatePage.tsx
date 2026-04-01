import { useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { ChevronLeft, Save } from 'lucide-react'
import { api } from '../lib/api'
import type { CreateGuidelineVersionResponse } from '../lib/types'

export default function UpdatePage() {
  const { guidelineId } = useParams()
  const navigate = useNavigate()

  const [versionLabel, setVersionLabel] = useState('')
  const [releaseDate, setReleaseDate] = useState('')
  const [effectiveFrom, setEffectiveFrom] = useState('')
  const [effectiveTo, setEffectiveTo] = useState('')
  const [deactivateOld, setDeactivateOld] = useState(true)
  const [file, setFile] = useState<File | null>(null)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    setLoading(true)
    setError('')

    try {
      const formData = new FormData()
      if (file) formData.append('file', file)
      if (versionLabel) formData.append('version_label', versionLabel)
      if (releaseDate) formData.append('release_date', releaseDate)
      if (effectiveFrom) formData.append('effective_from', effectiveFrom)
      if (effectiveTo) formData.append('effective_to', effectiveTo)

      formData.append('status', deactivateOld ? 'active' : 'inactive')

      const res = await api.post<CreateGuidelineVersionResponse>(`/guidelines/${guidelineId}/versions`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })

      navigate(`/guidelines/${res.data.guideline_id}/versions/${res.data.version_id}`)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Có lỗi xảy ra khi cập nhật văn bản.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="form-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">
            Tạo phiên bản mới
          </h1>
          <p className="page-subtitle">Tạo phiên bản tài liệu mới cho guideline hiện tại</p>
        </div>
        <Link to="/guidelines" className="btn btn-secondary">
          <ChevronLeft size={16} /> Quay lại
        </Link>
      </div>

      <div className="card">
        {error && <div className="alert alert-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-section">
            <h2 className="form-section-title">Thông tin phiên bản cập nhật</h2>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Số hiệu / Nhãn phiên bản *</label>
                <input
                  type="text"
                  className="form-input"
                  required
                  value={versionLabel}
                  onChange={e => setVersionLabel(e.target.value)}
                  placeholder="Ví dụ: 1245/QĐ-BYT"
                />
              </div>
              <div className="form-group">
                <label className="form-label">Ngày ban hành</label>
                <input
                  type="date"
                  className="form-input"
                  value={releaseDate}
                  onChange={e => setReleaseDate(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Hiệu lực từ</label>
                <input
                  type="date"
                  className="form-input"
                  value={effectiveFrom}
                  onChange={e => setEffectiveFrom(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Hiệu lực đến</label>
                <input
                  type="date"
                  className="form-input"
                  value={effectiveTo}
                  onChange={e => setEffectiveTo(e.target.value)}
                />
              </div>
            </div>
            <div className="form-group mt-4">
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  style={{ width: 16, height: 16 }}
                  checked={deactivateOld}
                  onChange={e => setDeactivateOld(e.target.checked)}
                />
                Đặt phiên bản mới là hiện hành
              </label>
              <span className="form-hint" style={{ marginLeft: 24 }}>
                Nếu chọn, các phiên bản active trước đó sẽ bị hạ xuống inactive.
              </span>
            </div>
          </div>

          <div className="form-section" style={{ marginBottom: 0 }}>
            <h2 className="form-section-title">Tài liệu cập nhật (PDF) *</h2>
            <label className="form-file-wrapper" style={{ display: 'block' }}>
              <input
                type="file"
                accept="application/pdf"
                onChange={e => setFile(e.target.files?.[0] || null)}
                required
              />
              <div style={{ color: file ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {file ? `Đã chọn: ${file.name}` : 'Click hoặc kéo thả file PDF vào đây'}
              </div>
            </label>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="loading-spinner" style={{ width: 14, height: 14 }} /> : <><Save size={16} /> Tạo phiên bản mới</>}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
