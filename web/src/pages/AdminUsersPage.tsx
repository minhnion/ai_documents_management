import { useEffect, useMemo, useState } from 'react'
import { UserPlus } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import type {
  UserResponse,
  UserListResponse,
  AvailableRoleResponse,
  CreateUserRequest,
  UpdateUserRoleRequest,
} from '../lib/types'

const CUSTOM_PARENT_VALUE = '__custom_parent__'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin',
  health_department: 'Sở y tế',
  hospital: 'Bệnh viện',
  doctor: 'Bác sĩ',
}

function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role
}

function accountName(user: UserResponse | null | undefined) {
  if (!user) return '-'
  return user.full_name || user.email
}

export default function AdminUsersPage() {
  const { user: currentUser } = useAuth()

  const [users, setUsers] = useState<UserResponse[]>([])
  const [roles, setRoles] = useState<AvailableRoleResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [showCreateForm, setShowCreateForm] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [form, setForm] = useState<CreateUserRequest>({
    email: '',
    full_name: null,
    password: '',
    role: 'health_department',
    parent_id: null,
    parent_name: null,
    parent_parent_id: null,
    is_active: true,
  })
  const [parentChoice, setParentChoice] = useState('')
  const [customParentName, setCustomParentName] = useState('')
  const [doctorDepartmentChoice, setDoctorDepartmentChoice] = useState('')

  const [updatingUserId, setUpdatingUserId] = useState<number | null>(null)
  const [roleError, setRoleError] = useState('')

  const departments = useMemo(
    () => users.filter(u => u.role === 'health_department'),
    [users],
  )
  const hospitals = useMemo(
    () => users.filter(u => u.role === 'hospital'),
    [users],
  )

  const loadData = async () => {
    setLoading(true)
    setError('')
    try {
      const [uRes, rRes] = await Promise.all([
        api.get<UserListResponse>('/auth/users'),
        api.get<AvailableRoleResponse[]>('/auth/roles'),
      ])
      setUsers(uRes.data.items)
      setRoles(rRes.data)
      const defaultRole = rRes.data[0]?.name ?? 'health_department'
      setForm(prev => ({ ...prev, role: defaultRole }))
    } catch {
      setError('Không thể tải dữ liệu tài khoản.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  useEffect(() => {
    if (form.role === 'hospital' && currentUser?.role === 'admin') {
      setParentChoice(departments[0]?.user_id ? String(departments[0].user_id) : CUSTOM_PARENT_VALUE)
    } else if (form.role === 'doctor' && currentUser?.role === 'admin') {
      setDoctorDepartmentChoice(departments[0]?.user_id ? String(departments[0].user_id) : '')
      const visibleHospitals = hospitals.filter(h => !departments[0]?.user_id || h.parent_id === departments[0].user_id)
      setParentChoice(visibleHospitals[0]?.user_id ? String(visibleHospitals[0].user_id) : CUSTOM_PARENT_VALUE)
    } else {
      setParentChoice('')
      setCustomParentName('')
      setDoctorDepartmentChoice('')
    }
  }, [form.role, currentUser?.role, departments, hospitals])

  const availableRoles = roles.map(r => r.name)

  const buildCreatePayload = (): CreateUserRequest => {
    const payload: CreateUserRequest = {
      email: form.email,
      full_name: form.full_name,
      password: form.password,
      role: form.role,
      is_active: form.is_active,
    }

    if (currentUser?.role !== 'admin') {
      return payload
    }

    if (form.role === 'hospital') {
      if (parentChoice === CUSTOM_PARENT_VALUE) {
        payload.parent_name = customParentName.trim()
      } else if (parentChoice) {
        payload.parent_id = Number(parentChoice)
      }
    }

    if (form.role === 'doctor') {
      if (parentChoice === CUSTOM_PARENT_VALUE) {
        payload.parent_name = customParentName.trim()
        if (doctorDepartmentChoice) payload.parent_parent_id = Number(doctorDepartmentChoice)
      } else if (parentChoice) {
        payload.parent_id = Number(parentChoice)
      }
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
      setShowCreateForm(false)
      setForm({
        email: '',
        full_name: null,
        password: '',
        role: availableRoles[0] ?? 'health_department',
        parent_id: null,
        parent_name: null,
        parent_parent_id: null,
        is_active: true,
      })
      setCustomParentName('')
    } catch (err: any) {
      setCreateError(err.response?.data?.detail || 'Không thể tạo tài khoản.')
    } finally {
      setCreating(false)
    }
  }

  const handleUserAccessChange = async (targetUser: UserResponse, patch: UpdateUserRoleRequest) => {
    setUpdatingUserId(targetUser.user_id)
    setRoleError('')
    try {
      const payload: UpdateUserRoleRequest = {
        role: patch.role ?? targetUser.role,
        parent_id: patch.parent_id,
        is_active: patch.is_active,
      }
      const res = await api.patch<UserResponse>(`/auth/users/${targetUser.user_id}/role`, payload)
      setUsers(prev => prev.map(u => u.user_id === targetUser.user_id ? res.data : u))
    } catch (err: any) {
      setRoleError(err.response?.data?.detail || 'Không thể cập nhật tài khoản.')
    } finally {
      setUpdatingUserId(null)
    }
  }

  const filteredHospitals = form.role === 'doctor' && doctorDepartmentChoice
    ? hospitals.filter(h => h.parent_id === Number(doctorDepartmentChoice))
    : hospitals

  const canInlineUpdate = currentUser?.role === 'admin'

  return (
    <div className="list-page flex-col">
      <div className="page-header">
        <div>
          <h1 className="page-title">Quản lý tài khoản</h1>
          <p className="page-subtitle">Tổng số: {users.length} tài khoản</p>
        </div>
        {availableRoles.length > 0 && (
          <button className="btn btn-primary" onClick={() => setShowCreateForm(v => !v)}>
            <UserPlus size={16} /> Tạo tài khoản
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {showCreateForm && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h2 className="form-section-title">Tạo tài khoản mới</h2>
          {createError && <div className="alert alert-error">{createError}</div>}
          <form onSubmit={handleCreateUser}>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Email *</label>
                <input type="email" className="form-input" required value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Tên hiển thị *</label>
                <input type="text" className="form-input" required value={form.full_name ?? ''}
                  onChange={e => setForm(f => ({ ...f, full_name: e.target.value || null }))}
                  placeholder="Tên sở, bệnh viện hoặc bác sĩ" />
              </div>
              <div className="form-group">
                <label className="form-label">Mật khẩu * (tối thiểu 8 ký tự)</label>
                <input type="password" className="form-input" required minLength={8} value={form.password}
                  onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Vai trò</label>
                <select className="form-select" value={form.role}
                  onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                  {roles.map(r => (
                    <option key={r.name} value={r.name}>{roleLabel(r.name)}</option>
                  ))}
                </select>
              </div>

              {currentUser?.role === 'admin' && form.role === 'hospital' && (
                <div className="form-group">
                  <label className="form-label">Sở y tế cha *</label>
                  <select className="form-select" value={parentChoice} onChange={e => setParentChoice(e.target.value)}>
                    {departments.map(item => (
                      <option key={item.user_id} value={item.user_id}>{accountName(item)}</option>
                    ))}
                    <option value={CUSTOM_PARENT_VALUE}>Khác...</option>
                  </select>
                </div>
              )}

              {currentUser?.role === 'admin' && form.role === 'doctor' && (
                <div className="form-group">
                  <label className="form-label">Sở y tế</label>
                  <select className="form-select" value={doctorDepartmentChoice} onChange={e => setDoctorDepartmentChoice(e.target.value)}>
                    {departments.map(item => (
                      <option key={item.user_id} value={item.user_id}>{accountName(item)}</option>
                    ))}
                  </select>
                </div>
              )}

              {currentUser?.role === 'admin' && form.role === 'doctor' && (
                <div className="form-group">
                  <label className="form-label">Bệnh viện cha *</label>
                  <select className="form-select" value={parentChoice} onChange={e => setParentChoice(e.target.value)}>
                    {filteredHospitals.map(item => (
                      <option key={item.user_id} value={item.user_id}>{accountName(item)}</option>
                    ))}
                    <option value={CUSTOM_PARENT_VALUE}>Khác...</option>
                  </select>
                </div>
              )}

              {currentUser?.role === 'admin' && parentChoice === CUSTOM_PARENT_VALUE && ['hospital', 'doctor'].includes(form.role) && (
                <div className="form-group">
                  <label className="form-label">Tên cấp cha mới *</label>
                  <input
                    type="text"
                    className="form-input"
                    required
                    value={customParentName}
                    onChange={e => setCustomParentName(e.target.value)}
                    placeholder={form.role === 'hospital' ? 'Nhập tên sở y tế' : 'Nhập tên bệnh viện'}
                  />
                </div>
              )}

              {currentUser?.role !== 'admin' && (
                <div className="form-group">
                  <label className="form-label">Cấp cha</label>
                  <input className="form-input" value={accountName(currentUser as UserResponse)} disabled />
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
                <th>Tên hiển thị</th>
                <th>Vai trò</th>
                <th>Cấp cha</th>
                <th>Trạng thái</th>
                <th>Ngày tạo</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Không có tài khoản nào.</td></tr>
              )}
              {users.map(u => (
                <tr key={u.user_id}>
                  <td className="font-medium">{u.email}</td>
                  <td>{u.full_name || '-'}</td>
                  <td>
                    <span className="badge badge-default">{roleLabel(u.role)}</span>
                  </td>
                  <td>{u.parent ? accountName(u.parent as UserResponse) : <span className="text-muted">-</span>}</td>
                  <td>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <input
                        type="checkbox"
                        checked={u.is_active}
                        disabled={!canInlineUpdate || u.user_id === currentUser?.user_id || updatingUserId === u.user_id}
                        onChange={e => handleUserAccessChange(u, {
                          role: u.role,
                          parent_id: u.parent_id,
                          is_active: e.target.checked,
                        })}
                      />
                      <span className={`badge ${u.is_active ? 'badge-active' : 'badge-inactive'}`}>
                        {u.is_active ? 'Hoạt động' : 'Vô hiệu'}
                      </span>
                    </label>
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
