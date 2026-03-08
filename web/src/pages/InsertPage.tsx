import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { ChevronLeft, Save } from 'lucide-react'
import { api } from '../lib/api'
import type { CreateGuidelineResponse } from '../lib/types'

export default function InsertPage() {
  const navigate = useNavigate()

  const [title, setTitle] = useState('')
  const [publisher, setPublisher] = useState('')
  const [chuyenKhoa, setChuyenKhoa] = useState('')
  const [versionLabel, setVersionLabel] = useState('')
  const [releaseDate, setReleaseDate] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!file) {
      setError('Vui lòng chọn file PDF.')
      return
    }

    setLoading(true)
    setError('')

    try {
      const formData = new FormData()
      formData.append('title', title)
      formData.append('file', file)
      if (publisher) formData.append('publisher', publisher)
      if (chuyenKhoa) formData.append('chuyen_khoa', chuyenKhoa)
      if (versionLabel) formData.append('version_label', versionLabel)
      if (releaseDate) formData.append('release_date', releaseDate)

      const res = await api.post<CreateGuidelineResponse>('/guidelines', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })

      navigate(`/guidelines/${res.data.guideline_id}/versions/${res.data.version_id}`)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Có lỗi xảy ra khi tạo văn bản.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="form-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">
            Thêm Guideline mới
          </h1>
          <p className="page-subtitle">Nhập thông tin văn bản và tải lên file PDF</p>
        </div>
        <Link to="/guidelines" className="btn btn-secondary">
          <ChevronLeft size={16} /> Hủy
        </Link>
      </div>

      <div className="card">
        {error && <div className="alert alert-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-section">
            <h2 className="form-section-title">Thông tin chung (Metadata)</h2>
            <div className="form-grid">
              <div className="form-group span-full">
                <label className="form-label">Tên văn bản *</label>
                <input
                  type="text"
                  className="form-input"
                  required
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  placeholder="Ví dụ: Hướng dẫn chẩn đoán và điều trị hen phế quản"
                />
              </div>
              <div className="form-group">
                <label className="form-label">Đơn vị ban hành</label>
                <input
                  type="text"
                  className="form-input"
                  value={publisher}
                  onChange={e => setPublisher(e.target.value)}
                  placeholder="Ví dụ: Bộ Y tế"
                />
              </div>
              <div className="form-group">
                <label className="form-label">Chuyên khoa</label>
                <select className="form-select" value={chuyenKhoa} onChange={e => setChuyenKhoa(e.target.value)}>
                  <option value="">-- Chọn chuyên khoa --</option>
                  <option value="Nội khoa">Nội khoa</option>
                  <option value="Ngoại khoa">Ngoại khoa</option>
                  <option value="Nhi khoa">Nhi khoa</option>
                  <option value="Sản khoa">Sản khoa</option>
                  <option value="Tim mạch">Tim mạch</option>
                </select>
              </div>
            </div>
          </div>

          <div className="form-section">
            <h2 className="form-section-title">Thông tin phiên bản xuất bản</h2>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Số hiệu / Nhãn phiên bản</label>
                <input
                  type="text"
                  className="form-input"
                  value={versionLabel}
                  onChange={e => setVersionLabel(e.target.value)}
                  placeholder="Ví dụ: 1234/QĐ-BYT"
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
            </div>
          </div>

          <div className="form-section" style={{ marginBottom: 0 }}>
            <h2 className="form-section-title">Tài liệu gốc (PDF) *</h2>
            <label className="form-file-wrapper" style={{ display: 'block' }}>
              <input
                type="file"
                accept="application/pdf"
                onChange={e => setFile(e.target.files?.[0] || null)}
              />
              <div style={{ color: file ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {file ? `Đã chọn: ${file.name}` : 'Click hoặc kéo thả file PDF vào đây'}
              </div>
            </label>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="loading-spinner" style={{ width: 14, height: 14 }} /> : <><Save size={16} /> Lưu văn bản</>}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
