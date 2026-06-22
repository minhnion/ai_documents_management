import { useState } from 'react'
import { CheckCircle2, KeyRound, Lock } from 'lucide-react'
import { api } from '../lib/api'
import type { ChangePasswordRequest, PasswordActionResponse } from '../lib/types'

function getApiErrorMessage(error: unknown, fallback: string) {
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
}

export default function ChangePasswordPage() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    setSuccess('')

    if (newPassword !== confirmPassword) {
      setError('Mật khẩu mới và xác nhận mật khẩu không khớp.')
      return
    }

    setLoading(true)
    try {
      const payload: ChangePasswordRequest = {
        current_password: currentPassword,
        new_password: newPassword,
      }
      await api.patch<PasswordActionResponse>('/auth/password', payload)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSuccess('Đã đổi mật khẩu thành công.')
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, 'Không thể đổi mật khẩu.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="form-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Đổi mật khẩu</h1>
          <p className="page-subtitle">Cập nhật mật khẩu đăng nhập của tài khoản hiện tại</p>
        </div>
      </div>

      <div className="card">
        <h2 className="form-section-title">Mật khẩu đăng nhập</h2>
        {error && <div className="alert alert-error">{error}</div>}
        {success && (
          <div className="alert alert-success">
            <CheckCircle2 size={16} /> {success}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex-col gap-4">
          <div className="form-group">
            <label className="form-label" htmlFor="current-password">Mật khẩu hiện tại</label>
            <div style={{ position: 'relative' }}>
              <Lock size={16} style={{ position: 'absolute', top: 12, left: 12, color: 'var(--text-muted)' }} />
              <input
                id="current-password"
                type="password"
                className="form-input"
                style={{ paddingLeft: 36 }}
                minLength={8}
                maxLength={512}
                value={currentPassword}
                onChange={event => setCurrentPassword(event.target.value)}
                required
              />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="new-password">Mật khẩu mới</label>
            <div style={{ position: 'relative' }}>
              <KeyRound size={16} style={{ position: 'absolute', top: 12, left: 12, color: 'var(--text-muted)' }} />
              <input
                id="new-password"
                type="password"
                className="form-input"
                style={{ paddingLeft: 36 }}
                minLength={8}
                maxLength={512}
                value={newPassword}
                onChange={event => setNewPassword(event.target.value)}
                required
              />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="confirm-password">Xác nhận mật khẩu mới</label>
            <div style={{ position: 'relative' }}>
              <KeyRound size={16} style={{ position: 'absolute', top: 12, left: 12, color: 'var(--text-muted)' }} />
              <input
                id="confirm-password"
                type="password"
                className="form-input"
                style={{ paddingLeft: 36 }}
                minLength={8}
                maxLength={512}
                value={confirmPassword}
                onChange={event => setConfirmPassword(event.target.value)}
                required
              />
            </div>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="loading-spinner" style={{ width: 14, height: 14 }} /> : <><KeyRound size={15} /> Đổi mật khẩu</>}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
