import { useEffect, useState } from 'react'
import { Save, X } from 'lucide-react'
import { api } from '../lib/api'
import { SPECIALTY_OPTIONS } from '../lib/specialties'
import SelectOrCustomInputField from './SelectOrCustomInputField'
import useGuidelineFilterOptions from '../hooks/useGuidelineFilterOptions'
import type { GuidelineListItem, UpdateGuidelineMetadataResponse } from '../lib/types'

interface Props {
  guideline: GuidelineListItem
  onClose: () => void
  onSaved: (updated: UpdateGuidelineMetadataResponse) => void
}

export default function GuidelineMetadataModal({ guideline, onClose, onSaved }: Props) {
  const filterOptions = useGuidelineFilterOptions()
  const [title, setTitle] = useState(guideline.title)
  const [tenBenh, setTenBenh] = useState(guideline.ten_benh ?? '')
  const [publisher, setPublisher] = useState(guideline.publisher ?? '')
  const [chuyenKhoa, setChuyenKhoa] = useState(guideline.chuyen_khoa ?? '')
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
      title,
      ten_benh: tenBenh,
      publisher,
      chuyen_khoa: chuyenKhoa,
    }

    try {
      const response = await api.patch<UpdateGuidelineMetadataResponse>(
        `/guidelines/${guideline.guideline_id}`,
        payload,
      )
      onSaved(response.data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Không thể cập nhật metadata guideline.')
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
          <span className="modal-title">Cập nhật metadata guideline</span>
          <span className="modal-subtitle">{guideline.title}</span>
          <button className="modal-close-btn" onClick={onClose} title="Đóng" disabled={submitting}>
            <X size={16} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="modal-body metadata-modal-body">
          {error && <div className="alert alert-error">{error}</div>}
          <div className="metadata-form-grid">
            <div className="form-group">
              <label className="form-label">Tên văn bản</label>
              <input
                type="text"
                className="form-input"
                value={title}
                onChange={event => setTitle(event.target.value)}
                required
                disabled={submitting}
              />
            </div>
            <SelectOrCustomInputField
              label="Tên bệnh"
              options={filterOptions.ten_benhs}
              value={tenBenh}
              onChange={setTenBenh}
              disabled={submitting}
              selectPlaceholder="-- Chọn tên bệnh --"
              customPlaceholder="Nhập tên bệnh"
            />
            <SelectOrCustomInputField
              label="Đơn vị ban hành"
              options={filterOptions.publishers}
              value={publisher}
              onChange={setPublisher}
              disabled={submitting}
              selectPlaceholder="-- Chọn đơn vị ban hành --"
              customPlaceholder="Nhập đơn vị ban hành"
            />
            <div className="form-group">
              <label className="form-label">Chuyên khoa</label>
              <select
                className="form-select"
                value={chuyenKhoa}
                onChange={event => setChuyenKhoa(event.target.value)}
                disabled={submitting}
              >
                <option value="">-- Chọn chuyên khoa --</option>
                {SPECIALTY_OPTIONS.map(option => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {/* <div className="metadata-modal-footnote">
            Các trường metadata sẽ được lưu theo nội dung hiện tại của form. Trường để trống sẽ được backend chuẩn hóa về rỗng hoặc null tùy field.
          </div> */}
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
                : <><Save size={14} /> Lưu metadata</>
              }
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
