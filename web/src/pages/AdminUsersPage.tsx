import { useEffect, useState } from 'react'
import { UserPlus } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import type {
  UserResponse,
  UserListResponse,
  AvailableRoleResponse,
  CreateUserRequest,
  OrganizationListResponse,
  OrganizationResponse,
  UpdateUserRoleRequest,
} from '../lib/types'

const CUSTOM_ORGANIZATION_VALUE = '__custom__'

export default function AdminUsersPage() {
  const { user: currentUser } = useAuth()

  const [users, setUsers] = useState<UserResponse[]>([])
  const [roles, setRoles] = useState<AvailableRoleResponse[]>([])
  const [organizations, setOrganizations] = useState<OrganizationResponse[]>([])
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
    role: 'user',
    organization_id: null,
    organization_name: null,
    is_active: true,
  })
  const [organizationChoice, setOrganizationChoice] = useState('')
  const [customOrganizationName, setCustomOrganizationName] = useState('')

  // Role update
  const [updatingRoleFor, setUpdatingRoleFor] = useState<number | null>(null)
  const [roleError, setRoleError] = useState('')

  useEffect(() => {
    Promise.all([
      api.get<UserListResponse>('/auth/users'),
      api.get<AvailableRoleResponse[]>('/auth/roles'),
      api.get<OrganizationListResponse>('/organizations'),
    ]).then(([uRes, rRes, orgRes]) => {
      setUsers(uRes.data.items)
      setRoles(rRes.data)
      setOrganizations(orgRes.data.items)
      setOrganizationChoice(orgRes.data.items[0]?.organization_id
        ? String(orgRes.data.items[0].organization_id)
        : CUSTOM_ORGANIZATION_VALUE)
    }).catch(() => setError('Không thể tải dữ liệu.'))
      .finally(() => setLoading(false))
  }, [])

  const buildCreatePayload = (): CreateUserRequest => {
    const payload: CreateUserRequest = {
      email: form.email,
      full_name: form.full_name,
      password: form.password,
      role: form.role,
      is_active: form.is_active,
    }
    if (form.role !== 'user') {
      return payload
    }
    if (organizationChoice === CUSTOM_ORGANIZATION_VALUE) {
      payload.organization_name = customOrganizationName.trim()
    } else if (organizationChoice) {
      payload.organization_id = Number(organizationChoice)
    }
    return payload
  }

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    setCreateError('')
    try {
      const res = await api.post<UserResponse>('/auth/users', buildCreatePayload())
      setUsers(prev => [...prev, res.data])
      if (
        res.data.organization
        && !organizations.some(org => org.organization_id === res.data.organization?.organization_id)
      ) {
        setOrganizations(prev => [...prev, res.data.organization!])
      }
      setShowCreateForm(false)
      setForm({
        email: '',
        full_name: null,
        password: '',
        role: 'user',
        organization_id: null,
        organization_name: null,
        is_active: true,
      })
      setCustomOrganizationName('')
    } catch (err: any) {
      setCreateError(err.response?.data?.detail || 'Không thể tạo người dùng.')
    } finally {
      setCreating(false)
    }
  }

  const handleUserAccessChange = async (targetUser: UserResponse, patch: UpdateUserRoleRequest) => {
    setUpdatingRoleFor(targetUser.user_id)
    setRoleError('')
    try {
      const payload: UpdateUserRoleRequest = {
        role: patch.role ?? targetUser.role,
        organization_id: patch.organization_id,
        organization_name: patch.organization_name,
      }
      if (payload.role === 'user' && payload.organization_id === undefined && !payload.organization_name) {
        payload.organization_id = targetUser.organization_id ?? organizations[0]?.organization_id ?? null
      }
      const res = await api.patch<UserResponse>(`/auth/users/${targetUser.user_id}/role`, payload)
      setUsers(prev => prev.map(u => u.user_id === targetUser.user_id ? res.data : u))
    } catch (err: any) {
      setRoleError(err.response?.data?.detail || 'Không thể cập nhật quyền.')
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
                    <option key={r.name} value={r.name}>{r.name} - {r.description}</option>
                  ))}
                </select>
              </div>
              {form.role === 'user' && (
                <div className="form-group">
                  <label className="form-label">Organization *</label>
                  <select
                    className="form-select"
                    value={organizationChoice}
                    onChange={e => setOrganizationChoice(e.target.value)}
                  >
                    {organizations.map(org => (
                      <option key={org.organization_id} value={org.organization_id}>
                        {org.name}
                      </option>
                    ))}
                    <option value={CUSTOM_ORGANIZATION_VALUE}>Khác...</option>
                  </select>
                </div>
              )}
              {form.role === 'user' && organizationChoice === CUSTOM_ORGANIZATION_VALUE && (
                <div className="form-group">
                  <label className="form-label">Organization mới *</label>
                  <input
                    type="text"
                    className="form-input"
                    required
                    value={customOrganizationName}
                    onChange={e => setCustomOrganizationName(e.target.value)}
                    placeholder="Nhập tên organization"
                  />
                </div>
              )}
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
        {roleError && <div className="alert alert-error" style={{ marginBottom: 8 }}>{roleError}</div>}
        {loading ? (
          <div className="loading-center"><span className="loading-spinner" /></div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Họ tên</th>
                <th>Role</th>
                <th>Organization</th>
                <th>Trạng thái</th>
                <th>Ngày tạo</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Không có người dùng nào.</td></tr>
              )}
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
                          onChange={e => {
                            const nextRole = e.target.value
                            handleUserAccessChange(u, {
                              role: nextRole,
                              organization_id: nextRole === 'user'
                                ? (u.organization_id ?? organizations[0]?.organization_id ?? null)
                                : null,
                            })
                          }}
                        >
                          {roles.map(r => <option key={r.name} value={r.name}>{r.name}</option>)}
                        </select>
                        {updatingRoleFor === u.user_id && <span className="loading-spinner" style={{ width: 12, height: 12 }} />}
                      </div>
                    )}
                  </td>
                  <td>
                    {u.role === 'admin' ? (
                      <span className="text-muted">Tất cả</span>
                    ) : u.user_id === currentUser?.user_id ? (
                      <span>{u.organization?.name || '-'}</span>
                    ) : (
                      <select
                        className="form-select"
                        style={{ width: 'auto', minWidth: 140, padding: '4px 8px', fontSize: 13 }}
                        value={u.organization_id ?? ''}
                        disabled={updatingRoleFor === u.user_id}
                        onChange={e => handleUserAccessChange(u, {
                          role: 'user',
                          organization_id: Number(e.target.value),
                        })}
                      >
                        {organizations.map(org => (
                          <option key={org.organization_id} value={org.organization_id}>
                            {org.name}
                          </option>
                        ))}
                      </select>
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
