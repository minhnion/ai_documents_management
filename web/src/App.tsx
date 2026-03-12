import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './store/auth'
import Layout from './components/Layout'

import LoginPage from './pages/LoginPage'
import ListPage from './pages/ListPage'
import ViewPage from './pages/ViewPage'
import InsertPage from './pages/InsertPage'
import UpdatePage from './pages/UpdatePage'
import AdminUsersPage from './pages/AdminUsersPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <Layout>{children}</Layout>
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, isAuthenticated } = useAuth()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  if (!user) return null
  if (user.role !== 'admin') return <Navigate to="/guidelines" replace />
  return <Layout>{children}</Layout>
}

// Global default export wrapped with providers
export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route path="/" element={<Navigate to="/guidelines" replace />} />

          <Route path="/guidelines" element={<ProtectedRoute><ListPage /></ProtectedRoute>} />
          <Route path="/guidelines/new" element={<ProtectedRoute><InsertPage /></ProtectedRoute>} />
          <Route path="/guidelines/:guidelineId/update" element={<ProtectedRoute><UpdatePage /></ProtectedRoute>} />
          <Route path="/guidelines/:guidelineId/versions/:versionId" element={<ProtectedRoute><ViewPage /></ProtectedRoute>} />

          <Route path="/admin/users" element={<AdminRoute><AdminUsersPage /></AdminRoute>} />

          <Route path="*" element={<Navigate to="/guidelines" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
