import { useEffect, useMemo, useState } from 'react'
import { Trash2, UserPlus } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import type {
  UserResponse,
  UserListResponse,
  DeleteUserResponse,
  AvailableRoleResponse,
  CreateUserRequest,
  UpdateUserRoleRequest,
} from '../lib/types'

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

function getApiErrorMessage(error: unknown, fallback: string) {
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
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
    is_active: true,
  })
  const [parentChoice, setParentChoice] = useState('')
  const [doctorDepartmentChoice, setDoctorDepartmentChoice] = useState('')

  const [updatingUserId, setUpdatingUserId] = useState<number | null>(null)
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null)
  const [roleError, setRoleError] = useState('')
  const [deleteError, setDeleteError] = useState('')

  const departments = useMemo(
    () => users.filter(u => u.role === 'health_department' && u.is_active),
    [users],
  )
  const hospitals = useMemo(
    () => users.filter(u => u.role === 'hospital' && u.is_active),
    [users],
  )

  const filteredHospitals = useMemo(
    () => form.role === 'doctor' && doctorDepartmentChoice
      ? hospitals.filter(h => h.parent_id === Number(doctorDepartmentChoice))
      : hospitals,
    [doctorDepartmentChoice, form.role, hospitals],
  )

  const parentSelectionError = useMemo(() => {
    if (currentUser?.role !== 'admin') return ''
    if (form.role === 'hospital' && !parentChoice) {
      return 'Cần có ít nhất một tài khoản sở y tế hoạt động để tạo bệnh viện.'
    }
    if (form.role === 'doctor' && !parentChoice) {
      return doctorDepartmentChoice
        ? 'Cần có ít nhất một bệnh viện hoạt động thuộc sở y tế đã chọn để tạo bác sĩ.'
        : 'Cần có sở y tế và bệnh viện hoạt động trước khi tạo bác sĩ.'
    }
    return ''
  }, [currentUser?.role, doctorDepartmentChoice, form.role, parentChoice])

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
      setParentChoice(departments[0]?.user_id ? String(departments[0].user_id) : '')
      setDoctorDepartmentChoice('')
    } else if (form.role === 'doctor' && currentUser?.role === 'admin') {
      setDoctorDepartmentChoice(departments[0]?.user_id ? String(departments[0].user_id) : '')
    } else {
      setParentChoice('')
      setDoctorDepartmentChoice('')
    }
  }, [form.role, currentUser?.role, departments])

  useEffect(() => {
    if (form.role !== 'doctor' || currentUser?.role !== 'admin') return
    setParentChoice(prev => {
      if (filteredHospitals.some(hospital => String(hospital.user_id) === prev)) {
        return prev
      }
      return filteredHospitals[0]?.user_id ? String(filteredHospitals[0].user_id) : ''
    })
  }, [form.role, currentUser?.role, filteredHospitals])

  const availableRoles = roles.map(r => r.name)

  const buildCreatePayload = (): CreateUserRequest => {
    const payload: CreateUserRequest = {
      email: form.email,
      full_name: form.full_name,
      password: form.password,
      role: form.role,
      is_active: form.is_active,
    }

    if (currentUser?.role === 'admin' && ['hospital', 'doctor'].includes(form.role) && parentChoice) {
      payload.parent_id = Number(parentChoice)
    }

    return payload
  }

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreateError('')
    if (parentSelectionError) {
      setCreateError(parentSelectionError)
      return
    }

    setCreating(true)
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
        is_active: true,
      })
    } catch (err: unknown) {
      setCreateError(getApiErrorMessage(err, 'Không thể tạo tài khoản.'))
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
    } catch (err: unknown) {
      setRoleError(getApiErrorMessage(err, 'Không thể cập nhật tài khoản.'))
    } finally {
      setUpdatingUserId(null)
    }
  }

  const canDeleteUser = (targetUser: UserResponse) => {
    if (!currentUser) return false
    if (targetUser.user_id === currentUser.user_id) return false
    if (targetUser.role === 'admin') return false
    if (currentUser.role === 'admin') return true
    return targetUser.parent_id === currentUser.user_id
  }

  const getDeleteUserTitle = (targetUser: UserResponse) => {
    if (targetUser.user_id === currentUser?.user_id) return 'Không thể xóa chính tài khoản đang đăng nhập'
    if (targetUser.role === 'admin') return 'Không xóa tài khoản admin tại màn hình này'
    if (!canDeleteUser(targetUser)) return 'Bạn chỉ có thể xóa tài khoản con trực tiếp'
    return 'Xóa cứng tài khoản này'
  }

  const handleDeleteUser = async (targetUser: UserResponse) => {
    if (!canDeleteUser(targetUser)) return
    const confirmed = window.confirm(
      `Xóa tài khoản "${accountName(targetUser)}"?\n\n`
      + 'Thao tác này sẽ xóa cứng tài khoản, các tài khoản con nếu có, và toàn bộ tài liệu do các tài khoản đó quản lý.'
    )
    if (!confirmed) return

    setDeletingUserId(targetUser.user_id)
    setDeleteError('')
    try {
      const res = await api.delete<DeleteUserResponse>(`/auth/users/${targetUser.user_id}`)
      const deletedIds = new Set(res.data.deleted_user_ids)
      setUsers(prev => prev.filter(item => !deletedIds.has(item.user_id)))
    } catch (err: unknown) {
      setDeleteError(getApiErrorMessage(err, 'Không thể xóa tài khoản.'))
    } finally {
      setDeletingUserId(null)
    }
  }

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
                  <select
                    className="form-select"
                    value={parentChoice}
                    disabled={departments.length === 0}
                    onChange={e => setParentChoice(e.target.value)}
                  >
                    {departments.length === 0 && <option value="">Chưa có sở y tế</option>}
                    {departments.map(item => (
                      <option key={item.user_id} value={item.user_id}>{accountName(item)}</option>
                    ))}
                  </select>
                </div>
              )}

              {currentUser?.role === 'admin' && form.role === 'doctor' && (
                <div className="form-group">
                  <label className="form-label">Sở y tế</label>
                  <select
                    className="form-select"
                    value={doctorDepartmentChoice}
                    disabled={departments.length === 0}
                    onChange={e => setDoctorDepartmentChoice(e.target.value)}
                  >
                    {departments.length === 0 && <option value="">Chưa có sở y tế</option>}
                    {departments.map(item => (
                      <option key={item.user_id} value={item.user_id}>{accountName(item)}</option>
                    ))}
                  </select>
                </div>
              )}

              {currentUser?.role === 'admin' && form.role === 'doctor' && (
                <div className="form-group">
                  <label className="form-label">Bệnh viện cha *</label>
                  <select
                    className="form-select"
                    value={parentChoice}
                    disabled={filteredHospitals.length === 0}
                    onChange={e => setParentChoice(e.target.value)}
                  >
                    {filteredHospitals.length === 0 && <option value="">Chưa có bệnh viện</option>}
                    {filteredHospitals.map(item => (
                      <option key={item.user_id} value={item.user_id}>{accountName(item)}</option>
                    ))}
                  </select>
                </div>
              )}

              {parentSelectionError && (
                <div className="alert alert-info" style={{ gridColumn: '1 / -1' }}>
                  {parentSelectionError}
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
              <button type="submit" className="btn btn-primary" disabled={creating || Boolean(parentSelectionError)}>
                {creating ? <span className="loading-spinner" style={{ width: 14, height: 14 }} /> : 'Tạo tài khoản'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="table-wrapper">
        {roleError && <div className="alert alert-error" style={{ marginBottom: 8 }}>{roleError}</div>}
        {deleteError && <div className="alert alert-error" style={{ marginBottom: 8 }}>{deleteError}</div>}
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
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 && (
                <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Không có tài khoản nào.</td></tr>
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
                  <td>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      disabled={!canDeleteUser(u) || deletingUserId === u.user_id}
                      title={getDeleteUserTitle(u)}
                      onClick={() => handleDeleteUser(u)}
                    >
                      {deletingUserId === u.user_id
                        ? <span className="loading-spinner" style={{ width: 14, height: 14 }} />
                        : <><Trash2 size={14} /> Xóa</>
                      }
                    </button>
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
