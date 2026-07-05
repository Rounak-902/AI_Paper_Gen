import { Navigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import { useEffect, useRef } from 'react';

export default function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, profile, loading, isAdmin } = useAuth();
  const toastShown = useRef(false);

  useEffect(() => {
    if (!loading && user && !isAdmin && !toastShown.current) {
      toastShown.current = true;
      toast.error('Access denied. Admin only.');
    }
  }, [loading, user, isAdmin]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#F8FAFC]">
        <div className="flex flex-col items-center gap-3">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[#4F46E5] border-t-transparent" />
          <p className="text-sm text-[#64748B]">Checking permissions...</p>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (!isAdmin || !profile) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}
