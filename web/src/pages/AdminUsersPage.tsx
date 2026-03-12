import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserPlus } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import type {
  UserResponse,
  UserListResponse,
  AvailableRoleResponse,
  CreateUserRequest,
} from '../lib/types'

export default function AdminUsersPage() {
  const { user: currentUser } = useAuth()
  const navigate = useNavigate()

  const [users, setUsers] = useState<UserResponse[]>([])
  const [roles, setRoles] = useState<AvailableRoleResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Create user form
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [form, setForm] = useState<CreateUserRequest>({
    email: '',
    full_name: null,
    password: '',
    role: 'viewer',
    is_active: true,
  })

  // Role update
  const [updatingRoleFor, setUpdatingRoleFor] = useState<number | null>(null)

  useEffect(() => {
    if (currentUser?.role !== 'admin') {
      navigate('/guidelines', { replace: true })
      return
    }
    Promise.all([
      api.get<UserListResponse>('/auth/users'),
      api.get<AvailableRoleResponse[]>('/auth/roles'),
    ]).then(([uRes, rRes]) => {
      setUsers(uRes.data.items)
      setRoles(rRes.data)
    }).catch(() => setError('Không thể tải dữ liệu.'))
      .finally(() => setLoading(false))
  }, [currentUser, navigate])

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    setCreateError('')
    try {
      const res = await api.post<UserResponse>('/auth/users', form)
      setUsers(prev => [...prev, res.data])
      setShowCreateForm(false)
      setForm({ email: '', full_name: null, password: '', role: 'viewer', is_active: true })
    } catch (err: any) {
      setCreateError(err.response?.data?.detail || 'Không thể tạo người dùng.')
    } finally {
      setCreating(false)
    }
  }

  const handleRoleChange = async (userId: number, newRole: string) => {
    setUpdatingRoleFor(userId)
    try {
      const res = await api.patch<UserResponse>(`/auth/users/${userId}/role`, { role: newRole })
      setUsers(prev => prev.map(u => u.user_id === userId ? res.data : u))
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Không thể đổi role.')
    } finally {
      setUpdatingRoleFor(null)
    }
  }

  return (
    <div className="list-page flex-col">
      <div className="page-header">
        <div>
          <h1 className="page-title">Quản lý Người dùng</h1>
          <p className="page-subtitle">Tổng số: {users.length} người dùng</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreateForm(v => !v)}>
          <UserPlus size={16} /> Tạo người dùng
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {showCreateForm && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h2 className="form-section-title">Tạo người dùng mới</h2>
          {createError && <div className="alert alert-error">{createError}</div>}
          <form onSubmit={handleCreateUser}>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Email *</label>
                <input type="email" className="form-input" required value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Họ tên</label>
                <input type="text" className="form-input" value={form.full_name ?? ''}
                  onChange={e => setForm(f => ({ ...f, full_name: e.target.value || null }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Mật khẩu * (tối thiểu 8 ký tự)</label>
                <input type="password" className="form-input" required minLength={8} value={form.password}
                  onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Role</label>
                <select className="form-select" value={form.role}
                  onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                  {roles.map(r => (
                    <option key={r.name} value={r.name}>{r.name} — {r.description}</option>
                  ))}
                </select>
              </div>
              <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <input type="checkbox" checked={form.is_active}
                    onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))} />
                  Kích hoạt tài khoản ngay
                </label>
              </div>
            </div>
            <div className="form-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setShowCreateForm(false)}>Hủy</button>
              <button type="submit" className="btn btn-primary" disabled={creating}>
                {creating ? <span className="loading-spinner" style={{ width: 14, height: 14 }} /> : 'Tạo tài khoản'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="table-wrapper">
        {loading ? (
          <div className="loading-center"><span className="loading-spinner" /></div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Họ tên</th>
                <th>Role</th>
                <th>Trạng thái</th>
                <th>Ngày tạo</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.user_id}>
                  <td className="font-medium">{u.email}</td>
                  <td>{u.full_name || '-'}</td>
                  <td>
                    {u.user_id === currentUser?.user_id ? (
                      <span className="badge badge-active">{u.role}</span>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <select
                          className="form-select"
                          style={{ width: 'auto', padding: '4px 8px', fontSize: 13 }}
                          value={u.role}
                          disabled={updatingRoleFor === u.user_id}
                          onChange={e => handleRoleChange(u.user_id, e.target.value)}
                        >
                          {roles.map(r => <option key={r.name} value={r.name}>{r.name}</option>)}
                        </select>
                        {updatingRoleFor === u.user_id && <span className="loading-spinner" style={{ width: 12, height: 12 }} />}
                      </div>
                    )}
                  </td>
                  <td>
                    <span className={`badge ${u.is_active ? 'badge-active' : 'badge-inactive'}`}>
                      {u.is_active ? 'Hoạt động' : 'Vô hiệu'}
                    </span>
                  </td>
                  <td className="text-sm text-muted">
                    {new Date(u.created_at).toLocaleDateString('vi-VN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
