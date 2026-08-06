import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { BarChart3, CheckCircle2, Database, Filter, Layers3, Save, Zap } from 'lucide-react'
import { api } from '../lib/api'
import type {
  CostModelType,
  CostPricingResponse,
  CostStatisticsResponse,
  CostUsageEventItem,
} from '../lib/types'

const PAGE_SIZE = 12
const MODEL_ORDER: CostModelType[] = ['ocr', 'llm', 'embedding']
const MODEL_LABELS: Record<CostModelType, string> = {
  ocr: 'OCR',
  llm: 'LLM',
  embedding: 'Embedding',
}

function emptyPricing(): CostPricingResponse {
  return {
    models: MODEL_ORDER.map(model_type => ({
      model_type,
      display_name: MODEL_LABELS[model_type],
      billing_mode: 'usage',
      rates: [],
    })),
  }
}

function toUiPricing(response: CostPricingResponse): CostPricingResponse {
  return {
    models: response.models.map(model => ({
      ...model,
      rates: model.rates.map(rate => ({ ...rate, price_usd: Number(rate.price_usd || 0).toFixed(2) })),
    })),
  }
}

function formatCurrency(value: string | number) {
  const numberValue = typeof value === 'number' ? value : Number(value || 0)
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(numberValue)
}

function formatInteger(value: number | undefined) {
  return new Intl.NumberFormat('vi-VN').format(value ?? 0)
}

function unitSuffix(divisor: number, unit: string) {
  if (unit.includes('token')) return `USD / ${(divisor / 1_000_000).toLocaleString('en-US')}M token`
  if (unit === 'output_char') return `USD / ${(divisor / 1_000_000).toLocaleString('en-US')}M ký tự`
  if (divisor === 1000) return 'USD / 1K lần'
  return 'USD / đơn vị'
}

function displayName(item: { full_name: string | null; email: string }) {
  return item.full_name || item.email
}

function getApiErrorMessage(error: unknown, fallback: string) {
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
}

function modelColor(model: CostModelType) {
  return model === 'ocr' ? '#2563eb' : model === 'llm' ? '#0f766e' : '#7c3aed'
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

function lastSevenDays() {
  const date = new Date()
  date.setDate(date.getDate() - 6)
  return date.toISOString().slice(0, 10)
}

export default function AdminCostManagementPage() {
  const [pricing, setPricing] = useState<CostPricingResponse>(emptyPricing())
  const [stats, setStats] = useState<CostStatisticsResponse | null>(null)
  const [events, setEvents] = useState<CostUsageEventItem[]>([])
  const [totalEvents, setTotalEvents] = useState(0)
  const [dateFrom, setDateFrom] = useState(lastSevenDays)
  const [dateTo, setDateTo] = useState(today)
  const [accountId, setAccountId] = useState('')
  const [groupId, setGroupId] = useState('')
  const [modelType, setModelType] = useState('')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const queryParams = useCallback(() => {
    const params = new URLSearchParams()
    if (dateFrom) params.set('from', dateFrom)
    if (dateTo) params.set('to', dateTo)
    if (accountId) params.set('accountId', accountId)
    if (groupId) params.set('groupId', groupId)
    if (modelType) params.set('modelType', modelType)
    return params
  }, [accountId, dateFrom, dateTo, groupId, modelType])

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = queryParams()
      const historyParams = new URLSearchParams(params)
      historyParams.set('page', String(page))
      historyParams.set('page_size', String(PAGE_SIZE))
      const [pricingRes, statsRes, historyRes] = await Promise.all([
        api.get<CostPricingResponse>('/admin/cost/pricing'),
        api.get<CostStatisticsResponse>(`/admin/cost/statistics?${params.toString()}`),
        api.get<{ items: CostUsageEventItem[]; total: number }>(`/admin/cost/history?${historyParams.toString()}`),
      ])
      setPricing(toUiPricing(pricingRes.data))
      setStats(statsRes.data)
      setEvents(historyRes.data.items)
      setTotalEvents(historyRes.data.total)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Không thể tải dữ liệu quản lý chi phí.'))
    } finally {
      setLoading(false)
    }
  }, [page, queryParams])

  useEffect(() => { void fetchData() }, [fetchData])

  const updateRate = (modelTypeValue: CostModelType, unit: string, value: string) => {
    setPricing(current => ({
      models: current.models.map(model => model.model_type !== modelTypeValue ? model : {
        ...model,
        rates: model.rates.map(rate => rate.unit === unit ? { ...rate, price_usd: value } : rate),
      }),
    }))
  }

  const savePricing = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      const response = await api.put<CostPricingResponse>('/admin/cost/pricing', pricing)
      setPricing(toUiPricing(response.data))
      setSuccess('Đã lưu đơn giá.')
    } catch (err) {
      setError(getApiErrorMessage(err, 'Không thể lưu đơn giá.'))
    } finally {
      setSaving(false)
    }
  }

  const chartData = useMemo(() => stats?.timeseries.map(point => ({
    period: point.period,
    OCR: Number(point.ocr_cost),
    LLM: Number(point.llm_cost),
    Embedding: Number(point.embedding_cost),
    Total: Number(point.total_cost),
  })) ?? [], [stats])

  const totalPages = Math.max(1, Math.ceil(totalEvents / PAGE_SIZE))

  return (
    <div className="page-container cost-page">
      <div className="page-header cost-page-header">
        <div>
          <h1>Quản lý chi phí</h1>
          <p className="text-muted">Cấu hình đơn giá và theo dõi chi phí theo từng loại model.</p>
        </div>
      </div>

      {error && <div className="alert alert-danger mb-4">{error}</div>}
      {success && <div className="alert alert-success mb-4">{success}</div>}

      <form onSubmit={savePricing} className="cost-pricing-grid cost-model-grid">
        {MODEL_ORDER.map(modelTypeValue => {
          const model = pricing.models.find(item => item.model_type === modelTypeValue)
          return (
            <section className="card cost-card cost-model-card" key={modelTypeValue}>
              <div className="cost-model-heading">
                <span className="cost-model-icon" style={{ background: `${modelColor(modelTypeValue)}18`, color: modelColor(modelTypeValue) }}>
                  {modelTypeValue === 'ocr' ? <Layers3 size={18} /> : modelTypeValue === 'llm' ? <Zap size={18} /> : <Database size={18} />}
                </span>
                <div>
                  <h2>{MODEL_LABELS[modelTypeValue]}</h2>
                  <span className="text-muted text-sm">Đơn giá sử dụng</span>
                </div>
              </div>
              <div className={`cost-rate-list cost-rate-list-${modelTypeValue}`}>
                {model?.rates.map(rate => (
                  <label className="cost-rate-row" key={rate.unit}>
                    <span>
                      <span className="form-label">{rate.label}</span>
                      <small>{unitSuffix(rate.unit_divisor, rate.unit)}</small>
                    </span>
                    <input
                      className="form-input cost-rate-input"
                      type="number"
                      min="0"
                      step="0.01"
                      max="1000000000"
                      value={rate.price_usd}
                      onChange={event => updateRate(modelTypeValue, rate.unit, event.target.value)}
                      disabled={loading || saving}
                    />
                  </label>
                ))}
              </div>
            </section>
          )
        })}
        <div className="cost-form-actions">
          <button className="btn btn-primary" type="submit" disabled={loading || saving}>
            <Save size={16} /> {saving ? 'Đang lưu...' : 'Lưu đơn giá'}
          </button>
        </div>
      </form>

      <section className="card cost-card cost-filter-card">
        <div className="cost-filter-header"><Filter size={17} /><span className="font-semibold">Bộ lọc thống kê</span></div>
        <div className="cost-filter-grid">
          <label className="form-group"><span className="form-label">Từ ngày</span><input className="form-input" type="date" value={dateFrom} onChange={event => { setDateFrom(event.target.value); setPage(1) }} /></label>
          <label className="form-group"><span className="form-label">Đến ngày</span><input className="form-input" type="date" value={dateTo} onChange={event => { setDateTo(event.target.value); setPage(1) }} /></label>
          <label className="form-group"><span className="form-label">Loại model</span><select className="form-select" value={modelType} onChange={event => { setModelType(event.target.value); setPage(1) }}><option value="">Tất cả</option><option value="ocr">OCR</option><option value="llm">LLM</option><option value="embedding">Embedding</option></select></label>
          <label className="form-group"><span className="form-label">Tài khoản</span><select className="form-select" value={accountId} onChange={event => { setAccountId(event.target.value); setPage(1) }}><option value="">Tất cả tài khoản</option>{stats?.filters.accounts.map(account => <option key={account.user_id} value={account.user_id}>{displayName(account)}</option>)}</select></label>
          <label className="form-group"><span className="form-label">Nhóm tài khoản</span><select className="form-select" value={groupId} onChange={event => { setGroupId(event.target.value); setPage(1) }}><option value="">Tất cả nhóm</option>{stats?.filters.groups.map(group => <option key={group.user_id} value={group.user_id}>{displayName(group)}</option>)}</select></label>
        </div>
      </section>

      <section className="stats-grid cost-summary-grid mb-4">
        <div className="stat-card cost-stat-card"><div className="stat-card-icon"><BarChart3 size={18} /></div><div><div className="stat-card-value">{formatCurrency(stats?.summary.total_cost ?? 0)}</div><div className="stat-card-label">Tổng chi phí</div></div></div>
        <div className="stat-card cost-stat-card"><div className="stat-card-icon"><Layers3 size={18} /></div><div><div className="stat-card-value">{formatCurrency(stats?.summary.ocr_cost ?? 0)}</div><div className="stat-card-label">Chi phí OCR</div></div></div>
        <div className="stat-card cost-stat-card"><div className="stat-card-icon"><Zap size={18} /></div><div><div className="stat-card-value">{formatCurrency(stats?.summary.llm_cost ?? 0)}</div><div className="stat-card-label">Chi phí LLM</div></div></div>
        <div className="stat-card cost-stat-card"><div className="stat-card-icon"><Database size={18} /></div><div><div className="stat-card-value">{formatCurrency(stats?.summary.embedding_cost ?? 0)}</div><div className="stat-card-label">Chi phí Embedding</div></div></div>
        <div className="stat-card cost-stat-card"><div className="stat-card-icon"><CheckCircle2 size={18} /></div><div><div className="stat-card-value">{formatInteger(stats?.summary.documents_processed)}</div><div className="stat-card-label">Tài liệu xử lý</div></div></div>
      </section>

      <section className="cost-charts-grid mb-4">
        <div className="chart-card cost-chart-card"><div className="chart-header"><div className="chart-title">Xu hướng chi phí</div><span className="text-muted text-sm">USD theo ngày</span></div><div className="account-chart cost-chart-area">{chartData.length ? <ResponsiveContainer width="100%" height="100%"><LineChart data={chartData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="period" tick={{ fontSize: 11 }} /><YAxis tickFormatter={value => `$${Number(value).toFixed(2)}`} tick={{ fontSize: 11 }} width={58} /><Tooltip formatter={(value) => formatCurrency(Number(value))} /><Line type="monotone" dataKey="OCR" stroke={modelColor('ocr')} strokeWidth={2} dot={false} /><Line type="monotone" dataKey="LLM" stroke={modelColor('llm')} strokeWidth={2} dot={false} /><Line type="monotone" dataKey="Embedding" stroke={modelColor('embedding')} strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer> : <div className="chart-empty">{loading ? 'Đang tải...' : 'Chưa có dữ liệu.'}</div>}</div></div>
        <div className="chart-card cost-chart-card"><div className="chart-header"><div className="chart-title">Cơ cấu chi phí</div><span className="text-muted text-sm">Theo loại model</span></div><div className="account-chart cost-chart-area">{chartData.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="period" tick={{ fontSize: 11 }} /><YAxis tickFormatter={value => `$${Number(value).toFixed(2)}`} tick={{ fontSize: 11 }} width={58} /><Tooltip formatter={(value) => formatCurrency(Number(value))} /><Bar dataKey="OCR" stackId="cost" fill={modelColor('ocr')} /><Bar dataKey="LLM" stackId="cost" fill={modelColor('llm')} /><Bar dataKey="Embedding" stackId="cost" fill={modelColor('embedding')} /></BarChart></ResponsiveContainer> : <div className="chart-empty">{loading ? 'Đang tải...' : 'Chưa có dữ liệu.'}</div>}</div></div>
      </section>

      <section className="table-wrapper cost-events-table">
        <div className="cost-table-heading"><div><h2>Lịch sử usage</h2><p className="text-muted text-sm">Mỗi dòng tương ứng một request hoặc một batch model thực tế.</p></div><span className="badge badge-default">{formatInteger(stats?.summary.usage_events)} events</span></div>
        <table className="data-table"><thead><tr><th>Thời gian</th><th>Model</th><th>Hoạt động</th><th>Tài liệu</th><th>Usage</th><th className="text-right">Chi phí</th></tr></thead><tbody>
          {events.map(event => <tr key={event.event_id}><td>{new Date(event.occurred_at).toLocaleString('vi-VN')}</td><td><span className="cost-model-badge" style={{ color: modelColor(event.model_type), background: `${modelColor(event.model_type)}14` }}>{MODEL_LABELS[event.model_type]}</span></td><td>{event.operation}</td><td>{event.document_id ? `#${event.document_id}` : '-'}</td><td className="text-sm text-muted">{event.model_type === 'ocr' ? `${formatInteger(event.page_count)} trang · ${formatInteger(event.request_count)} request` : `${formatInteger(event.input_tokens)} input · ${formatInteger(event.output_tokens)} output`}</td><td className="text-right font-semibold">{formatCurrency(event.cost_usd)}</td></tr>)}
          {!events.length && <tr><td colSpan={6} className="text-center text-muted">{loading ? 'Đang tải...' : 'Chưa có lịch sử usage.'}</td></tr>}
        </tbody></table>
        <div className="pagination-bar"><span className="text-sm text-muted">Trang {page} / {totalPages}</span><div className="flex gap-2"><button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(current => Math.max(1, current - 1))}>Trước</button><button className="btn btn-secondary btn-sm" disabled={page >= totalPages} onClick={() => setPage(current => Math.min(totalPages, current + 1))}>Sau</button></div></div>
      </section>
    </div>
  )
}
