import { useEffect, useState } from 'react'
import { DollarSign, Save } from 'lucide-react'
import { api } from '../lib/api'
import type { CostPricingResponse } from '../lib/types'

const EMPTY_PRICING: CostPricingResponse = {
  ocr: {
    input_char_price: '0',
    output_char_price: '0',
    page_price: '0',
  },
  vlm: {
    input_token_price: '0',
    output_token_price: '0',
  },
}

function getApiErrorMessage(error: unknown, fallback: string) {
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
}

export default function AdminCostManagementPage() {
  const [pricing, setPricing] = useState<CostPricingResponse>(EMPTY_PRICING)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  useEffect(() => {
    let mounted = true
    setLoading(true)
    api.get<CostPricingResponse>('/admin/cost/pricing')
      .then(res => {
        if (mounted) setPricing(res.data)
      })
      .catch(() => {
        if (mounted) setError('Không thể tải cấu hình giá.')
      })
      .finally(() => {
        if (mounted) setLoading(false)
      })
    return () => {
      mounted = false
    }
  }, [])

  const updateOcr = (field: keyof CostPricingResponse['ocr'], value: string) => {
    setPricing(prev => ({ ...prev, ocr: { ...prev.ocr, [field]: value } }))
  }

  const updateVlm = (field: keyof CostPricingResponse['vlm'], value: string) => {
    setPricing(prev => ({ ...prev, vlm: { ...prev.vlm, [field]: value } }))
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      const res = await api.put<CostPricingResponse>('/admin/cost/pricing', pricing)
      setPricing(res.data)
      setSuccess('Đã lưu cấu hình giá.')
    } catch (err) {
      setError(getApiErrorMessage(err, 'Không thể lưu cấu hình giá.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Quản lý chi phí</h1>
          <p className="text-muted">Cấu hình đơn giá theo từng loại mô hình.</p>
        </div>
      </div>

      {error && <div className="alert alert-danger mb-4">{error}</div>}
      {success && <div className="alert alert-success mb-4">{success}</div>}

      <form onSubmit={handleSubmit} className="cost-pricing-grid">
        <section className="card cost-card">
          <div className="cost-section-title">
            <DollarSign size={18} />
            <h2>Đơn giá mô hình OCR</h2>
          </div>
          <div className="cost-form-grid">
            <label className="form-group">
              <span className="form-label">Giá ký tự đầu vào (USD / ký tự)</span>
              <input
                className="form-input"
                type="number"
                min="0"
                step="0.000000000001"
                value={pricing.ocr.input_char_price}
                onChange={event => updateOcr('input_char_price', event.target.value)}
                disabled={loading || saving}
              />
            </label>
            <label className="form-group">
              <span className="form-label">Giá ký tự đầu ra (USD / ký tự)</span>
              <input
                className="form-input"
                type="number"
                min="0"
                step="0.000000000001"
                value={pricing.ocr.output_char_price}
                onChange={event => updateOcr('output_char_price', event.target.value)}
                disabled={loading || saving}
              />
            </label>
            <label className="form-group">
              <span className="form-label">Giá theo trang (USD / trang)</span>
              <input
                className="form-input"
                type="number"
                min="0"
                step="0.000000000001"
                value={pricing.ocr.page_price}
                onChange={event => updateOcr('page_price', event.target.value)}
                disabled={loading || saving}
              />
            </label>
          </div>
        </section>

        <section className="card cost-card">
          <div className="cost-section-title">
            <DollarSign size={18} />
            <h2>Đơn giá mô hình VLM</h2>
          </div>
          <div className="cost-form-grid">
            <label className="form-group">
              <span className="form-label">Giá token đầu vào (USD / token)</span>
              <input
                className="form-input"
                type="number"
                min="0"
                step="0.000000000001"
                value={pricing.vlm.input_token_price}
                onChange={event => updateVlm('input_token_price', event.target.value)}
                disabled={loading || saving}
              />
            </label>
            <label className="form-group">
              <span className="form-label">Giá token đầu ra (USD / token)</span>
              <input
                className="form-input"
                type="number"
                min="0"
                step="0.000000000001"
                value={pricing.vlm.output_token_price}
                onChange={event => updateVlm('output_token_price', event.target.value)}
                disabled={loading || saving}
              />
            </label>
          </div>
        </section>

        <div className="cost-form-actions">
          <button className="btn btn-primary" type="submit" disabled={loading || saving}>
            <Save size={16} /> {saving ? 'Đang lưu...' : 'Lưu cấu hình'}
          </button>
        </div>
      </form>
    </div>
  )
}
