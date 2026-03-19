import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Search, Eye, Edit2, Trash2, Layers } from 'lucide-react'
import { api } from '../lib/api'
import { SPECIALTY_OPTIONS } from '../lib/specialties'
import type { GuidelineListResponse } from '../lib/types'
import { useAuth } from '../store/auth'
import VersionManagerModal from '../components/VersionManagerModal'

interface FilterOptions {
  publishers: string[]
  ten_benhs: string[]
}

const DEFAULT_PAGE_SIZE = 10

export default function ListPage() {
  const { user } = useAuth()
  const [data, setData] = useState<GuidelineListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [chuyenKhoa, setChuyenKhoa] = useState('')
  const [publisher, setPublisher] = useState('')
  const [tenBenh, setTenBenh] = useState('')
  const [page, setPage] = useState(1)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [versionModalGuideline, setVersionModalGuideline] = useState<{ id: number; title: string } | null>(null)
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({ publishers: [], ten_benhs: [] })

  const fetchFilterOptions = useCallback(async () => {
    try {
      const res = await api.get<FilterOptions>('/guidelines/filter-options')
      setFilterOptions(res.data)
    } catch (err) {
      console.error('Failed to fetch filter options:', err)
    }
  }, [])

  const fetchGuidelines = useCallback(async () => {
    setLoading(true)
    const params = new URLSearchParams()
    params.set('page', String(page))
    params.set('page_size', String(DEFAULT_PAGE_SIZE))
    if (search) params.set('search', search)
    if (chuyenKhoa) params.set('chuyen_khoa', chuyenKhoa)
    if (publisher) params.set('publisher', publisher)
    if (tenBenh) params.set('ten_benh', tenBenh)

    try {
      const res = await api.get<GuidelineListResponse>(`/guidelines?${params.toString()}`)
      setData(res.data)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [chuyenKhoa, publisher, tenBenh, page, search])

  const handleDelete = async (guidelineId: number, title: string) => {
    if (!window.confirm(`Xóa "${title}" và tất cả phiên bản? Thao tác này không thể hoàn tác.`)) return
    setDeletingId(guidelineId)
    try {
      await api.delete(`/guidelines/${guidelineId}`)
      const isLastItemOnPage = (data?.items.length ?? 0) === 1
      if (page > 1 && isLastItemOnPage) {
        setPage(prev => prev - 1)
      } else {
        await fetchGuidelines()
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Không thể xóa văn bản.')
    } finally {
      setDeletingId(null)
    }
  }

  useEffect(() => {
    fetchFilterOptions()
  }, [fetchFilterOptions])

  useEffect(() => {
    fetchGuidelines()
  }, [fetchGuidelines])

  const canEdit = user?.role === 'editor' || user?.role === 'admin'

  return (
    <div className="list-page h-full flex-col">
      <div className="page-header">
        <div>
          <h1 className="page-title">Quản lý Guideline</h1>
          <p className="page-subtitle">Danh sách các văn bản, hướng dẫn chuyên môn ({data?.total || 0})</p>
        </div>
        <Link to="/guidelines/new" className="btn btn-primary">
          <Plus size={16} /> Thêm Guideline
        </Link>
      </div>

      <div className="card flex-1 flex-col" style={{ padding: 20 }}>
        <div className="filter-bar" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
            <div style={{ position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', top: 11, left: 12, color: 'var(--text-muted)' }} />
              <input
                type="text"
                className="form-input"
                placeholder="Tìm theo tiêu đề, tên bệnh, nhà xuất bản..."
                style={{ paddingLeft: 36, maxWidth: '100%' }}
                value={search}
                onChange={e => {
                  setSearch(e.target.value)
                  setPage(1)
                }}
              />
            </div>
          </div>
          <div className="form-group" style={{ width: 220, minWidth: 180 }}>
            <select
              className="form-select"
              value={publisher}
              onChange={e => {
                setPublisher(e.target.value)
                setPage(1)
              }}
            >
              <option value="">Tất cả đơn vị ban hành</option>
              {filterOptions.publishers.map(option => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group" style={{ width: 220, minWidth: 180 }}>
            <select
              className="form-select"
              value={tenBenh}
              onChange={e => {
                setTenBenh(e.target.value)
                setPage(1)
              }}
            >
              <option value="">Tất cả tên bệnh</option>
              {filterOptions.ten_benhs.map(option => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group" style={{ width: 220, minWidth: 180 }}>
            <select
              className="form-select"
              value={chuyenKhoa}
              onChange={e => {
                setChuyenKhoa(e.target.value)
                setPage(1)
              }}
            >
              <option value="">Tất cả chuyên khoa</option>
              {SPECIALTY_OPTIONS.map(option => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="table-wrapper flex-1 overflow-auto">
          {loading ? (
            <div className="loading-center"><span className="loading-spinner" /></div>
          ) : data?.items.length === 0 ? (
            <div className="empty-state">Không tìm thấy tài liệu nào.</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Tên văn bản</th>
                  <th>Tên bệnh</th>
                  <th>Đơn vị ban hành</th>
                  <th>Chuyên khoa</th>
                  <th>Phiên bản hiện hành</th>
                  <th className="text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map(item => (
                  <tr key={item.guideline_id}>
                    <td className="font-medium">{item.title}</td>
                    <td>{item.ten_benh || '-'}</td>
                    <td>{item.publisher || '-'}</td>
                    <td>{item.chuyen_khoa ? <span className="badge badge-default">{item.chuyen_khoa}</span> : '-'}</td>
                    <td>
                      {item.active_version ? (
                        <div>
                          <div>{item.active_version.version_label || `v${item.active_version.version_id}`}</div>
                          <div className="text-sm text-muted">{item.active_version.release_date}</div>
                        </div>
                      ) : (
                        <span className="text-muted">Chưa có</span>
                      )}
                    </td>
                    <td>
                      <div className="actions-cell">
                        {item.active_version ? (
                          <Link
                            to={`/guidelines/${item.guideline_id}/versions/${item.active_version.version_id}`}
                            className="btn btn-secondary btn-sm"
                            title="Xem chi tiết"
                          >
                            <Eye size={14} /> Xem
                          </Link>
                        ) : (
                          <button className="btn btn-secondary btn-sm" disabled title="Không có bản active">
                            <Eye size={14} /> Xem
                          </button>
                        )}
                        {canEdit && (
                          <Link
                            to={`/guidelines/${item.guideline_id}/update`}
                            className="btn btn-secondary btn-sm"
                            title="Cập nhật phiên bản mới"
                          >
                            <Edit2 size={14} /> Cập nhật
                          </Link>
                        )}
                        {canEdit && (
                          <button
                            className="btn btn-secondary btn-sm"
                            title="Quản lý phiên bản"
                            aria-label={`Quản lý phiên bản: ${item.title}`}
                            onClick={() => setVersionModalGuideline({ id: item.guideline_id, title: item.title })}
                          >
                            <Layers size={14} /> Phiên bản
                          </button>
                        )}
                        {user?.role === 'admin' && (
                          <button
                            className="btn btn-danger btn-sm"
                            title="Xóa guideline"
                            disabled={deletingId === item.guideline_id}
                            onClick={() => handleDelete(item.guideline_id, item.title)}
                          >
                            {deletingId === item.guideline_id
                              ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
                              : <Trash2 size={14} />}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {!loading && data && data.total > 0 && (
          <div className="pagination">
            <span>
              Trang {data.page} / {Math.max(1, Math.ceil(data.total / data.page_size))}
            </span>
            <span>
              ({data.total} bản ghi)
            </span>
            <button
              className="btn btn-secondary btn-sm"
              disabled={data.page <= 1}
              onClick={() => setPage(prev => Math.max(1, prev - 1))}
            >
              Trang trước
            </button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={data.page >= Math.max(1, Math.ceil(data.total / data.page_size))}
              onClick={() => setPage(prev => prev + 1)}
            >
              Trang sau
            </button>
          </div>
        )}
      </div>
      {versionModalGuideline && (
        <VersionManagerModal
          guidelineId={versionModalGuideline.id}
          guidelineTitle={versionModalGuideline.title}
          onClose={() => setVersionModalGuideline(null)}
          onVersionsChanged={fetchGuidelines}
        />
      )}
    </div>
  )
}
