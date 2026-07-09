import { useEffect, useMemo, useState } from 'react'
import { KeyRound, UserPlus, X } from 'lucide-react'
import { api } from '../lib/api'
import {
  ROLE_ADMIN,
  ROLE_CENTRAL_HOSPITAL,
  ROLE_DOCTOR,
  ROLE_HEALTH_DEPARTMENT,
  ROLE_HEALTH_STATION,
  ROLE_HOSPITAL,
  isTopLevelUnitRole,
  roleLabel,
} from '../lib/roles'
import { useAuth } from '../store/auth'
import type {
  UserResponse,
  UserListResponse,
  DeleteUserResponse,
  AvailableRoleResponse,
  CreateUserRequest,
  ResetUserPasswordRequest,
  UpdateUserRoleRequest,
} from '../lib/types'

function accountName(user: UserResponse | null | undefined) {
  if (!user) return '-'
  return user.full_name || user.email
}

function followsGlobalDocuments(user: UserResponse | null | undefined) {
  if (!user) return false
  return user.inherits_global_documents !== false
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
    inherits_global_documents: true,
  })
  const [parentChoice, setParentChoice] = useState('')

  const [updatingUserId, setUpdatingUserId] = useState<number | null>(null)
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null)
  const [resettingUserId, setResettingUserId] = useState<number | null>(null)
  const [resetTargetUser, setResetTargetUser] = useState<UserResponse | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetConfirmPassword, setResetConfirmPassword] = useState('')
  const [roleError, setRoleError] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const [resetError, setResetError] = useState('')
  const [resetSuccess, setResetSuccess] = useState('')

  const departments = useMemo(
    () => users.filter(u => u.role === ROLE_HEALTH_DEPARTMENT && u.is_active),
    [users],
  )
  const doctorParents = useMemo(
    () => users.filter(u => [ROLE_CENTRAL_HOSPITAL, ROLE_HOSPITAL, ROLE_HEALTH_STATION].includes(u.role) && u.is_active),
    [users],
  )
  const selectedParentForCreate = useMemo(() => {
    if (currentUser?.role !== ROLE_ADMIN) return currentUser
    if ([ROLE_HOSPITAL, ROLE_HEALTH_STATION].includes(form.role)) {
      return departments.find(item => String(item.user_id) === parentChoice) ?? null
    }
    if (form.role === ROLE_DOCTOR) {
      return doctorParents.find(item => String(item.user_id) === parentChoice) ?? null
    }
    return null
  }, [currentUser, departments, doctorParents, form.role, parentChoice])
  const effectiveInheritsGlobalDocuments = useMemo(() => {
    if (form.role === ROLE_ADMIN) return true
    if (isTopLevelUnitRole(form.role)) return form.inherits_global_documents
    return followsGlobalDocuments(selectedParentForCreate)
  }, [form.inherits_global_documents, form.role, selectedParentForCreate])
  const canChooseGlobalDocuments = currentUser?.role === ROLE_ADMIN && isTopLevelUnitRole(form.role)

  const parentSelectionError = useMemo(() => {
    if (currentUser?.role !== ROLE_ADMIN) return ''
    if ([ROLE_HOSPITAL, ROLE_HEALTH_STATION].includes(form.role) && !parentChoice) {
      return 'Cần có ít nhất một tài khoản sở y tế hoạt động để tạo đơn vị này.'
    }
    if (form.role === ROLE_DOCTOR && !parentChoice) {
      return 'Cần có ít nhất một bệnh viện, trạm y tế hoặc bệnh viện trung ương hoạt động để tạo bác sĩ.'
    }
    return ''
  }, [currentUser?.role, form.role, parentChoice])

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

  const refreshUsers = async () => {
    const res = await api.get<UserListResponse>('/auth/users')
    setUsers(res.data.items)
  }

  useEffect(() => {
    void loadData()
  }, [])

  useEffect(() => {
    if (currentUser?.role !== ROLE_ADMIN) {
      setParentChoice('')
      return
    }
    const parentOptions = form.role === ROLE_DOCTOR
      ? doctorParents
      : [ROLE_HOSPITAL, ROLE_HEALTH_STATION].includes(form.role)
        ? departments
        : []
    setParentChoice(prev => {
      if (parentOptions.some(item => String(item.user_id) === prev)) {
        return prev
      }
      return parentOptions[0]?.user_id ? String(parentOptions[0].user_id) : ''
    })
  }, [form.role, currentUser?.role, departments, doctorParents])

  const availableRoles = roles.map(r => r.name)

  const buildCreatePayload = (): CreateUserRequest => {
    const payload: CreateUserRequest = {
      email: form.email,
      full_name: form.full_name,
      password: form.password,
      role: form.role,
      is_active: form.is_active,
      inherits_global_documents: effectiveInheritsGlobalDocuments,
    }

    if (currentUser?.role === ROLE_ADMIN && [ROLE_HOSPITAL, ROLE_HEALTH_STATION, ROLE_DOCTOR].includes(form.role) && parentChoice) {
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
        inherits_global_documents: true,
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
        inherits_global_documents: patch.inherits_global_documents,
      }
      const res = await api.patch<UserResponse>(`/auth/users/${targetUser.user_id}/role`, payload)
      if (patch.inherits_global_documents !== undefined) {
        await refreshUsers()
      } else {
        setUsers(prev => prev.map(u => u.user_id === targetUser.user_id ? res.data : u))
      }
    } catch (err: unknown) {
      setRoleError(getApiErrorMessage(err, 'Không thể cập nhật tài khoản.'))
    } finally {
      setUpdatingUserId(null)
    }
  }

  const canManagePassword = (targetUser: UserResponse) => {
    if (!currentUser) return false
    if (targetUser.user_id === currentUser.user_id) return false
    if (currentUser.role === ROLE_ADMIN) return true
    return targetUser.parent_id === currentUser.user_id
  }

  const openResetPasswordModal = (targetUser: UserResponse) => {
    if (!canManagePassword(targetUser)) return
    setResetTargetUser(targetUser)
    setResetPassword('')
    setResetConfirmPassword('')
    setResetError('')
    setResetSuccess('')
  }

  const closeResetPasswordModal = () => {
    if (resettingUserId !== null) return
    setResetTargetUser(null)
    setResetPassword('')
    setResetConfirmPassword('')
    setResetError('')
  }

  const handleResetPassword = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!resetTargetUser) return

    setResetError('')
    setResetSuccess('')
    if (resetPassword !== resetConfirmPassword) {
      setResetError('Mật khẩu mới và xác nhận mật khẩu không khớp.')
      return
    }

    setResettingUserId(resetTargetUser.user_id)
    try {
      const payload: ResetUserPasswordRequest = { new_password: resetPassword }
      const res = await api.patch<UserResponse>(`/auth/users/${resetTargetUser.user_id}/password`, payload)
      setUsers(prev => prev.map(u => u.user_id === resetTargetUser.user_id ? res.data : u))
      setResetSuccess(`Đã đặt lại mật khẩu cho ${accountName(resetTargetUser)}.`)
      setResetTargetUser(null)
      setResetPassword('')
      setResetConfirmPassword('')
    } catch (err: unknown) {
      setResetError(getApiErrorMessage(err, 'Không thể đặt lại mật khẩu.'))
    } finally {
      setResettingUserId(null)
    }
  }

  const canDeleteUser = (targetUser: UserResponse) => {
    if (!currentUser) return false
    if (targetUser.user_id === currentUser.user_id) return false
    if (targetUser.role === 'admin') return false
    if (currentUser.role === ROLE_ADMIN) return true
    return targetUser.parent_id === currentUser.user_id
  }

  const getDeleteUserTitle = (targetUser: UserResponse) => {
    if (targetUser.user_id === currentUser?.user_id) return 'Không thể xóa chính tài khoản đang đăng nhập'
    if (targetUser.role === 'admin') return 'Không xóa tài khoản admin tại màn hình này'
    if (!canDeleteUser(targetUser)) return 'Bạn chỉ có thể xóa tài khoản con trực tiếp'
    return 'Xóa cứng tài khoản này'
  }

  const getResetPasswordTitle = (targetUser: UserResponse) => {
    if (targetUser.user_id === currentUser?.user_id) return 'Dùng màn hình đổi mật khẩu để cập nhật tài khoản hiện tại'
    if (!canManagePassword(targetUser)) return 'Bạn chỉ có thể đặt lại mật khẩu tài khoản con trực tiếp'
    return 'Đặt lại mật khẩu tài khoản này'
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

  const canInlineUpdate = currentUser?.role === ROLE_ADMIN
  const canEditGlobalDocuments = (targetUser: UserResponse) => (
    currentUser?.role === ROLE_ADMIN
    && isTopLevelUnitRole(targetUser.role)
    && updatingUserId !== targetUser.user_id
  )
  const getGlobalDocumentsTitle = (targetUser: UserResponse) => {
    if (targetUser.role === ROLE_ADMIN) return 'Tài khoản admin luôn là tài liệu chung'
    if (!isTopLevelUnitRole(targetUser.role)) return 'Tài khoản này kế thừa lựa chọn từ cấp cha'
    if (currentUser?.role !== ROLE_ADMIN) return 'Chỉ admin được chỉnh sửa'
    return followsGlobalDocuments(targetUser)
      ? 'Bỏ theo tài liệu chung của Bộ Y tế'
      : 'Theo tài liệu chung của Bộ Y tế'
  }

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
      {resetSuccess && <div className="alert alert-success">{resetSuccess}</div>}

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
                    <option key={r.name} value={r.name}>{r.label ?? roleLabel(r.name)}</option>
                  ))}
                </select>
              </div>

              {currentUser?.role === ROLE_ADMIN && [ROLE_HOSPITAL, ROLE_HEALTH_STATION].includes(form.role) && (
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

              {currentUser?.role === ROLE_ADMIN && form.role === ROLE_DOCTOR && (
                <div className="form-group">
                  <label className="form-label">Đơn vị cha *</label>
                  <select
                    className="form-select"
                    value={parentChoice}
                    disabled={doctorParents.length === 0}
                    onChange={e => setParentChoice(e.target.value)}
                  >
                    {doctorParents.length === 0 && <option value="">Chưa có đơn vị nhận bác sĩ</option>}
                    {doctorParents.map(item => (
                      <option key={item.user_id} value={item.user_id}>
                        {accountName(item)} - {roleLabel(item.role)}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {parentSelectionError && (
                <div className="alert alert-info" style={{ gridColumn: '1 / -1' }}>
                  {parentSelectionError}
                </div>
              )}

              {currentUser?.role !== ROLE_ADMIN && (
                <div className="form-group">
                  <label className="form-label">Cấp cha</label>
                  <input className="form-input" value={accountName(currentUser as UserResponse)} disabled />
                </div>
              )}

              {form.role !== ROLE_ADMIN && (
                <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: canChooseGlobalDocuments ? 'pointer' : 'not-allowed' }}>
                    <input
                      type="checkbox"
                      checked={effectiveInheritsGlobalDocuments}
                      disabled={!canChooseGlobalDocuments}
                      onChange={e => setForm(f => ({ ...f, inherits_global_documents: e.target.checked }))}
                    />
                    Theo tài liệu chung của Bộ Y tế
                  </label>
                  {isTopLevelUnitRole(form.role) ? (
                    <div className="form-hint">
                      Nếu bỏ chọn, tài khoản cấp này sẽ quản lý độc lập và không có cấp cha admin.
                    </div>
                  ) : (
                    <div className="form-hint">
                      Cấp này kế thừa lựa chọn tài liệu chung từ tài khoản cha.
                    </div>
                  )}
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
                <th>Tài liệu chung</th>
                <th>Trạng thái</th>
                <th>Ngày tạo</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 && (
                <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Không có tài khoản nào.</td></tr>
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
                    <label
                      title={getGlobalDocumentsTitle(u)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 8,
                        cursor: canEditGlobalDocuments(u) ? 'pointer' : 'not-allowed',
                      }}
                    >
                      {isTopLevelUnitRole(u.role) && (
                        <input
                          type="checkbox"
                          checked={followsGlobalDocuments(u)}
                          disabled={!canEditGlobalDocuments(u)}
                          onChange={e => handleUserAccessChange(u, {
                            role: u.role,
                            parent_id: u.parent_id,
                            inherits_global_documents: e.target.checked,
                          })}
                        />
                      )}
                      <span className={`badge ${followsGlobalDocuments(u) ? 'badge-active' : 'badge-inactive'}`}>
                        {followsGlobalDocuments(u) ? 'Theo' : 'Độc lập'}
                      </span>
                    </label>
                  </td>
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
                    <div className="table-actions">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={!canManagePassword(u) || resettingUserId === u.user_id}
                        title={getResetPasswordTitle(u)}
                        onClick={() => openResetPasswordModal(u)}
                      >
                        {resettingUserId === u.user_id
                          ? <span className="loading-spinner" style={{ width: 14, height: 14 }} />
                          : <><KeyRound size={14} /> Mật khẩu</>}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {resetTargetUser && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="reset-password-title">
          <div className="modal-container password-modal">
            <div className="modal-header">
              <KeyRound size={18} />
              <div className="flex-1">
                <h2 id="reset-password-title" className="modal-title">Đặt lại mật khẩu</h2>
                <p className="modal-subtitle">{accountName(resetTargetUser)} - {resetTargetUser.email}</p>
              </div>
              <button type="button" className="modal-close-btn" onClick={closeResetPasswordModal} title="Đóng">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleResetPassword}>
              <div className="modal-body flex-col gap-4">
                {resetError && <div className="alert alert-error">{resetError}</div>}
                <div className="form-group">
                  <label className="form-label" htmlFor="reset-password">Mật khẩu mới</label>
                  <input
                    id="reset-password"
                    type="password"
                    className="form-input"
                    minLength={8}
                    maxLength={512}
                    value={resetPassword}
                    onChange={e => setResetPassword(e.target.value)}
                    required
                    autoFocus
                  />
                </div>
                <div className="form-group">
                  <label className="form-label" htmlFor="reset-password-confirm">Xác nhận mật khẩu mới</label>
                  <input
                    id="reset-password-confirm"
                    type="password"
                    className="form-input"
                    minLength={8}
                    maxLength={512}
                    value={resetConfirmPassword}
                    onChange={e => setResetConfirmPassword(e.target.value)}
                    required
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={closeResetPasswordModal}>Hủy</button>
                <button type="submit" className="btn btn-primary" disabled={resettingUserId !== null}>
                  {resettingUserId !== null
                    ? <span className="loading-spinner" style={{ width: 14, height: 14 }} />
                    : <><KeyRound size={15} /> Đặt lại mật khẩu</>}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
