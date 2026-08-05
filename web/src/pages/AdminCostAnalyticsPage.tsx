import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, ChevronLeft, ChevronRight, Filter } from 'lucide-react'
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
import { api } from '../lib/api'
import type { CostHistoryResponse, CostStatisticsResponse } from '../lib/types'

const PAGE_SIZE = 10

type TimePreset = 'today' | 'last5' | 'last7' | 'month' | 'custom'

function toDateInput(date: Date) {
  return date.toISOString().slice(0, 10)
}

function currency(value: string | number) {
  const numberValue = typeof value === 'number' ? value : Number(value || 0)
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(numberValue)
}

function displayName(item: { full_name: string | null; email: string }) {
  return item.full_name || item.email
}

function buildPresetRange(preset: TimePreset) {
  const today = new Date()
  const start = new Date(today)
  if (preset === 'last5') start.setDate(today.getDate() - 4)
  if (preset === 'last7') start.setDate(today.getDate() - 6)
  if (preset === 'month') start.setDate(1)
  return { from: toDateInput(start), to: toDateInput(today) }
}

export default function AdminCostAnalyticsPage() {
  const [preset, setPreset] = useState<TimePreset>('last7')
  const defaultRange = useMemo(() => buildPresetRange('last7'), [])
  const [dateFrom, setDateFrom] = useState(defaultRange.from)
  const [dateTo, setDateTo] = useState(defaultRange.to)
  const [accountId, setAccountId] = useState('')
  const [groupId, setGroupId] = useState('')
  const [stats, setStats] = useState<CostStatisticsResponse | null>(null)
  const [history, setHistory] = useState<CostHistoryResponse | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (preset === 'custom') return
    const range = buildPresetRange(preset)
    setDateFrom(range.from)
    setDateTo(range.to)
    setPage(1)
  }, [preset])

  const buildParams = useCallback(() => {
    const params = new URLSearchParams()
    if (dateFrom) params.set('from', dateFrom)
    if (dateTo) params.set('to', dateTo)
    if (accountId) params.set('accountId', accountId)
    if (groupId) params.set('groupId', groupId)
    return params
  }, [accountId, dateFrom, dateTo, groupId])

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = buildParams()
      const historyParams = new URLSearchParams(params)
      historyParams.set('page', String(page))
      historyParams.set('page_size', String(PAGE_SIZE))
      const [statsRes, historyRes] = await Promise.all([
        api.get<CostStatisticsResponse>(`/admin/cost/statistics?${params.toString()}`),
        api.get<CostHistoryResponse>(`/admin/cost/history?${historyParams.toString()}`),
      ])
      setStats(statsRes.data)
      setHistory(historyRes.data)
    } catch {
      setError('Không thể tải thống kê chi phí.')
    } finally {
      setLoading(false)
    }
  }, [buildParams, page])

  useEffect(() => {
    void fetchData()
  }, [fetchData])

  const chartData = useMemo(() => (
    stats?.timeseries.map(point => ({
      period: point.period,
      OCR: Number(point.ocr_cost),
      VLM: Number(point.vlm_cost),
      Total: Number(point.total_cost),
    })) ?? []
  ), [stats])

  const totalPages = Math.max(1, Math.ceil((history?.total ?? 0) / PAGE_SIZE))

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Thống kê chi phí</h1>
          <p className="text-muted">Theo dõi chi phí mô hình OCR và mô hình VLM theo thời gian.</p>
        </div>
      </div>

      {error && <div className="alert alert-danger mb-4">{error}</div>}

      <section className="card cost-card mb-4">
        <div className="cost-filter-header">
          <Filter size={17} />
          <span className="font-semibold">Bộ lọc</span>
        </div>
        <div className="cost-filter-grid">
          <label className="form-group">
            <span className="form-label">Thời gian</span>
            <select className="form-select" value={preset} onChange={event => setPreset(event.target.value as TimePreset)}>
              <option value="today">Hôm nay</option>
              <option value="last5">5 ngày gần nhất</option>
              <option value="last7">7 ngày gần nhất</option>
              <option value="month">Tháng này</option>
              <option value="custom">Khoảng tùy chọn</option>
            </select>
          </label>
          <label className="form-group">
            <span className="form-label">Từ ngày</span>
            <input className="form-input" type="date" value={dateFrom} onChange={event => {
              setPreset('custom')
              setDateFrom(event.target.value)
              setPage(1)
            }} />
          </label>
          <label className="form-group">
            <span className="form-label">Đến ngày</span>
            <input className="form-input" type="date" value={dateTo} onChange={event => {
              setPreset('custom')
              setDateTo(event.target.value)
              setPage(1)
            }} />
          </label>
          <label className="form-group">
            <span className="form-label">Tài khoản</span>
            <select className="form-select" value={accountId} onChange={event => {
              setAccountId(event.target.value)
              setPage(1)
            }}>
              <option value="">Tất cả tài khoản</option>
              {stats?.filters.accounts.map(account => (
                <option key={account.user_id} value={account.user_id}>{displayName(account)}</option>
              ))}
            </select>
          </label>
          <label className="form-group">
            <span className="form-label">Nhóm tài khoản</span>
            <select className="form-select" value={groupId} onChange={event => {
              setGroupId(event.target.value)
              setPage(1)
            }}>
              <option value="">Tất cả nhóm</option>
              {stats?.filters.groups.map(group => (
                <option key={group.user_id} value={group.user_id}>{displayName(group)}</option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section className="stats-grid mb-4">
        <div className="stat-card cost-stat-card">
          <div className="stat-card-icon"><BarChart3 size={18} /></div>
          <div>
            <div className="stat-card-value">{currency(stats?.summary.total_cost ?? 0)}</div>
            <div className="stat-card-label">Tổng chi phí</div>
          </div>
        </div>
        <div className="stat-card cost-stat-card">
          <div className="stat-card-icon"><BarChart3 size={18} /></div>
          <div>
            <div className="stat-card-value">{currency(stats?.summary.ocr_cost ?? 0)}</div>
            <div className="stat-card-label">Chi phí OCR</div>
          </div>
        </div>
        <div className="stat-card cost-stat-card">
          <div className="stat-card-icon"><BarChart3 size={18} /></div>
          <div>
            <div className="stat-card-value">{currency(stats?.summary.vlm_cost ?? 0)}</div>
            <div className="stat-card-label">Chi phí VLM</div>
          </div>
        </div>
        <div className="stat-card cost-stat-card">
          <div className="stat-card-icon"><BarChart3 size={18} /></div>
          <div>
            <div className="stat-card-value">{stats?.summary.documents_processed ?? 0}</div>
            <div className="stat-card-label">Tài liệu đã xử lý</div>
          </div>
        </div>
      </section>

      <section className="cost-charts-grid mb-4">
        <div className="chart-card">
          <div className="chart-header">
            <div className="chart-title">Xu hướng chi phí</div>
          </div>
          <div className="account-chart">
            {chartData.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis tickFormatter={value => currency(Number(value))} />
                  <Tooltip formatter={(value) => currency(Number(value))} />
                  <Line type="monotone" dataKey="OCR" stroke="#0969da" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="VLM" stroke="#1a7f37" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="Total" name="Tổng" stroke="#9a6700" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="chart-empty">{loading ? 'Đang tải...' : 'Chưa có dữ liệu.'}</div>}
          </div>
        </div>
        <div className="chart-card">
          <div className="chart-header">
            <div className="chart-title">Cơ cấu chi phí</div>
          </div>
          <div className="account-chart">
            {chartData.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis tickFormatter={value => currency(Number(value))} />
                  <Tooltip formatter={(value) => currency(Number(value))} />
                  <Bar dataKey="OCR" fill="#0969da" />
                  <Bar dataKey="VLM" fill="#1a7f37" />
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="chart-empty">{loading ? 'Đang tải...' : 'Chưa có dữ liệu.'}</div>}
          </div>
        </div>
      </section>

      <section className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th>Thời gian</th>
              <th>Tài liệu</th>
              <th>Tài khoản</th>
              <th>Nhóm</th>
              <th className="text-right">OCR</th>
              <th className="text-right">VLM</th>
              <th className="text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {history?.items.map(item => (
              <tr key={item.cost_history_id}>
                <td>{new Date(item.created_at).toLocaleString('vi-VN')}</td>
                <td>#{item.document_id}</td>
                <td>{item.account ? displayName(item.account) : '-'}</td>
                <td>{item.group ? displayName(item.group) : '-'}</td>
                <td className="text-right">{currency(item.ocr_cost)}</td>
                <td className="text-right">{currency(item.vlm_cost)}</td>
                <td className="text-right font-semibold">{currency(item.total_cost)}</td>
              </tr>
            ))}
            {!history?.items.length && (
              <tr>
                <td colSpan={7} className="text-center text-muted">{loading ? 'Đang tải...' : 'Chưa có lịch sử chi phí.'}</td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="pagination-bar">
          <span className="text-sm text-muted">Trang {page} / {totalPages}</span>
          <div className="flex gap-2">
            <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(prev => Math.max(1, prev - 1))}>
              <ChevronLeft size={14} /> Trước
            </button>
            <button className="btn btn-secondary btn-sm" disabled={page >= totalPages} onClick={() => setPage(prev => Math.min(totalPages, prev + 1))}>
              Sau <ChevronRight size={14} />
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
