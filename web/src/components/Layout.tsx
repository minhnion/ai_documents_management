import { BookOpen, LogOut } from 'lucide-react'
import { Link } from 'react-router-dom'
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
        {isAuthenticated && (
          <div className="navbar-user">
            <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
              {user?.full_name ?? user?.email}
            </span>
            <span className="badge badge-default text-sm">{user?.role}</span>
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
