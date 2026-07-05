import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import {
  LayoutDashboard,
  PenTool,
  Library,
  PlusCircle,
  Database,
  LogOut,
  FileText,
  ChevronLeft,
  ChevronRight,
  BookOpen,
} from 'lucide-react';
import { useState } from 'react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';

const navItems = [
  { label: 'Dashboard', icon: LayoutDashboard, path: '/dashboard' },
  { label: 'Create Paper', icon: PenTool, path: '/wizard' },
  { label: 'Question Bank', icon: Library, path: '/question-bank' },
];

const adminItems = [
  { label: 'Add Question', icon: PlusCircle, path: '/admin/add-question' },
  { label: 'Manage Bank', icon: Database, path: '/admin/question-bank' },
  { label: 'Textbook Ingestor', icon: BookOpen, path: '/admin/textbook-ingestor' },
];

export default function Sidebar() {
  const { profile, signOut, isAdmin } = useAuth();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);

  const handleSignOut = async () => {
    await signOut();
    navigate('/login');
  };

  const initials = profile?.full_name
    ?.split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2) ?? 'U';

  return (
    <aside
      className={`fixed left-0 top-0 z-40 flex h-screen flex-col bg-[#0F172A] text-white transition-all duration-300 ${
        collapsed ? 'w-[68px]' : 'w-[260px]'
      }`}
    >
      {/* Logo */}
      <div className="flex h-16 items-center gap-3 border-b border-white/10 px-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#4F46E5]">
          <FileText className="h-5 w-5" />
        </div>
        {!collapsed && (
          <span className="text-lg font-bold tracking-tight">PaperGen</span>
        )}
      </div>

      {/* Navigation */}
      <nav className="mt-4 flex flex-1 flex-col gap-1 px-3">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200 ${
                isActive
                  ? 'bg-[#4F46E5] text-white shadow-lg shadow-indigo-500/25'
                  : 'text-gray-400 hover:bg-white/5 hover:text-white'
              } ${collapsed ? 'justify-center' : ''}`
            }
            title={collapsed ? item.label : undefined}
          >
            <item.icon className="h-5 w-5 shrink-0" />
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}

        {/* Admin section */}
        {isAdmin && (
          <>
            <div className="my-3 border-t border-white/10" />
            {!collapsed && (
              <span className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
                Admin
              </span>
            )}
            {adminItems.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200 ${
                    isActive
                      ? 'bg-[#4F46E5] text-white shadow-lg shadow-indigo-500/25'
                      : 'text-gray-400 hover:bg-white/5 hover:text-white'
                  } ${collapsed ? 'justify-center' : ''}`
                }
                title={collapsed ? item.label : undefined}
              >
                <item.icon className="h-5 w-5 shrink-0" />
                {!collapsed && <span>{item.label}</span>}
              </NavLink>
            ))}
          </>
        )}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="mx-3 mb-2 flex items-center justify-center rounded-lg py-2 text-gray-500 transition-colors hover:bg-white/5 hover:text-white"
      >
        {collapsed ? (
          <ChevronRight className="h-4 w-4" />
        ) : (
          <ChevronLeft className="h-4 w-4" />
        )}
      </button>

      {/* User section */}
      <div className="border-t border-white/10 p-3">
        <div
          className={`flex items-center gap-3 ${collapsed ? 'justify-center' : ''}`}
        >
          <Avatar className="h-9 w-9 shrink-0 border border-white/20">
            <AvatarFallback className="bg-[#4F46E5] text-xs font-semibold text-white">
              {initials}
            </AvatarFallback>
          </Avatar>
          {!collapsed && (
            <div className="flex-1 overflow-hidden">
              <p className="truncate text-sm font-medium">
                {profile?.full_name ?? 'User'}
              </p>
              <p className="truncate text-xs text-gray-500">
                {profile?.role === 'admin' ? 'Admin' : 'Teacher'}
              </p>
            </div>
          )}
          <button
            onClick={handleSignOut}
            className="shrink-0 rounded-lg p-2 text-gray-500 transition-colors hover:bg-white/10 hover:text-white"
            title="Sign out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
