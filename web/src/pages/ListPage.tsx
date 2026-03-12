import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Search, Eye, Edit2, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import type { GuidelineListResponse } from '../lib/types'
import { useAuth } from '../store/auth'

export default function ListPage() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [data, setData] = useState<GuidelineListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [chuyenKhoa, setChuyenKhoa] = useState('')
  const [deletingId, setDeletingId] = useState<number | null>(null)
  // Optional: Add debounce for search, pagination states, etc.

  const handleDelete = async (guidelineId: number, title: string) => {
    if (!window.confirm(`Xóa "${title}" và tất cả phiên bản? Thao tác này không thể hoàn tác.`)) return
    setDeletingId(guidelineId)
    try {
      await api.delete(`/guidelines/${guidelineId}`)
      setData(prev => prev ? {
        ...prev,
        items: prev.items.filter(i => i.guideline_id !== guidelineId),
        total: prev.total - 1
      } : prev)
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Không thể xóa văn bản.')
    } finally {
      setDeletingId(null)
    }
  }

  const fetchGuidelines = () => {
    setLoading(true)
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (chuyenKhoa) params.set('chuyen_khoa', chuyenKhoa)

    api.get<GuidelineListResponse>(`/guidelines?${params.toString()}`)
      .then(res => setData(res.data))
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchGuidelines()
  }, [search, chuyenKhoa])

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
        <div className="filter-bar">
          <div className="form-group" style={{ flex: 1 }}>
            <div style={{ position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', top: 11, left: 12, color: 'var(--text-muted)' }} />
              <input
                type="text"
                className="form-input"
                placeholder="Tìm kiếm theo tiêu đề hoặc nhà xuất bản..."
                style={{ paddingLeft: 36, maxWidth: '100%' }}
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
          </div>
          <div className="form-group" style={{ width: 200 }}>
            <select
              className="form-select"
              value={chuyenKhoa}
              onChange={e => setChuyenKhoa(e.target.value)}
            >
              <option value="">Tất cả chuyên khoa</option>
              <option value="Nội khoa">Nội khoa</option>
              <option value="Ngoại khoa">Ngoại khoa</option>
              <option value="Nhi khoa">Nhi khoa</option>
              <option value="Sản khoa">Sản khoa</option>
              <option value="Tim mạch">Tim mạch</option>
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
                         <Link
                           to={`/guidelines/${item.guideline_id}/update`}
                           className="btn btn-secondary btn-sm"
                           title="Cập nhật phiên bản mới"
                         >
                           <Edit2 size={14} /> Cập nhật
                         </Link>
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
      </div>
    </div>
  )
}
