import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { TooltipProvider } from '@/components/ui/tooltip';
import Toast from './components/shared/Toast';
import ProtectedRoute from './components/auth/ProtectedRoute';
import AdminRoute from './components/auth/AdminRoute';
import AppLayout from './components/layout/AppLayout';
import LoginPage from './pages/LoginPage';
import SignupPage from './pages/SignupPage';
import DashboardPage from './pages/DashboardPage';
import WizardPage from './pages/WizardPage';
import PreviewPage from './pages/PreviewPage';
import QuestionBankPage from './pages/QuestionBankPage';
import AddQuestionPage from './pages/admin/AddQuestionPage';
import AdminQuestionBankPage from './pages/admin/AdminQuestionBankPage';
import TextbookIngestorPage from './pages/admin/TextbookIngestorPage';

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <TooltipProvider>
          <Toast />
          <Routes>
            {/* Public routes */}
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />

            {/* Protected routes with layout */}
            <Route
              element={
                <ProtectedRoute>
                  <AppLayout />
                </ProtectedRoute>
              }
            >
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/wizard" element={<WizardPage />} />
              <Route path="/preview/:paper_id" element={<PreviewPage />} />
              <Route path="/question-bank" element={<QuestionBankPage />} />

              {/* Admin routes */}
              <Route
                path="/admin/add-question"
                element={
                  <AdminRoute>
                    <AddQuestionPage />
                  </AdminRoute>
                }
              />
              <Route
                path="/admin/question-bank"
                element={
                  <AdminRoute>
                    <AdminQuestionBankPage />
                  </AdminRoute>
                }
              />
              <Route
                path="/admin/textbook-ingestor"
                element={
                  <AdminRoute>
                    <TextbookIngestorPage />
                  </AdminRoute>
                }
              />
            </Route>

            {/* Default redirect */}
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </TooltipProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
