import { BarChart3, BookOpen, DollarSign, KeyRound, LogOut, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import { roleLabel, isAccountManagerRole, isAdminRole } from '../lib/roles'
import { useAuth } from '../store/auth'

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout, isAuthenticated } = useAuth()

  return (
    <div className="flex-col h-full">
      <nav className="navbar">
        <Link to="/guidelines" className="navbar-brand" style={{ textDecoration: 'none' }}>
          <BookOpen size={18} />
          <span>Quản lý tài liệu</span>
        </Link>
        
        <div className="navbar-spacer" />
        {user && isAccountManagerRole(user.role) && (
          <Link to="/admin/users" className="btn btn-ghost btn-sm">
            <Users size={15} /> Tài khoản
          </Link>
        )}
        {user && isAdminRole(user.role) && (
          <>
            <Link to="/admin/cost/pricing" className="btn btn-ghost btn-sm">
              <DollarSign size={15} /> Quản lý chi phí
            </Link>
            <Link to="/admin/cost/analytics" className="btn btn-ghost btn-sm">
              <BarChart3 size={15} /> Thống kê chi phí
            </Link>
          </>
        )}
        {isAuthenticated && (
          <div className="navbar-user">
            <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
              {user?.full_name ?? user?.email}
            </span>
            <span className="badge badge-default text-sm">{user ? roleLabel(user.role) : ''}</span>
            {user?.parent && (
              <span className="badge badge-default text-sm">Thuộc: {user.parent.full_name ?? user.parent.email}</span>
            )}
            <Link to="/account/password" className="btn btn-ghost btn-sm" title="Đổi mật khẩu">
              <KeyRound size={14} />
            </Link>
            <button className="btn btn-ghost btn-sm" onClick={logout} title="Đăng xuất">
              <LogOut size={14} />
            </button>
          </div>
        )}
      </nav>
      <main style={{ flex: 1, overflow: 'auto' }}>
        {children}
      </main>
    </div>
  )
}
