import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { FileText, Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

export default function SignupPage() {
  const { signUp } = useAuth();
  const navigate = useNavigate();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [role, setRole] = useState<'teacher' | 'admin'>('teacher');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!fullName || !email || !password || !confirmPassword) {
      toast.error('Please fill in all fields');
      return;
    }
    if (password.length < 6) {
      toast.error('Password must be at least 6 characters');
      return;
    }
    if (password !== confirmPassword) {
      toast.error('Passwords do not match');
      return;
    }

    setLoading(true);
    const { error } = await signUp(email, password, fullName, role);
    setLoading(false);

    if (error) {
      toast.error(error);
    } else {
      toast.success('Account created successfully! Please check your email to verify.');
      navigate('/dashboard');
    }
  };

  return (
    <div className="flex min-h-screen">
      {/* Left side - gradient */}
      <div className="hidden w-1/2 lg:flex lg:flex-col lg:items-center lg:justify-center bg-gradient-to-br from-[#0F172A] via-[#1E293B] to-[#4F46E5]">
        <div className="px-12 text-center">
          <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-2xl bg-white/10 backdrop-blur-sm">
            <FileText className="h-10 w-10 text-white" />
          </div>
          <h1 className="mb-3 text-4xl font-bold text-white">PaperGen</h1>
          <p className="text-lg text-gray-300">
            AI-Powered Question Paper Generator
          </p>
          <div className="mt-8 space-y-3 text-left text-sm text-gray-400">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10">
                ✨
              </div>
              <span>Generate papers using AI in minutes</span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10">
                📚
              </div>
              <span>Growing question bank with every paper</span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10">
                📄
              </div>
              <span>Export to PDF, print-ready format</span>
            </div>
          </div>
        </div>
      </div>

      {/* Right side - form */}
      <div className="flex w-full items-center justify-center px-6 lg:w-1/2">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#4F46E5]">
              <FileText className="h-5 w-5 text-white" />
            </div>
            <span className="text-xl font-bold text-[#1E293B]">PaperGen</span>
          </div>

          <h2 className="text-2xl font-bold text-[#1E293B]">Create an account</h2>
          <p className="mt-1 text-sm text-[#64748B]">
            Get started with PaperGen today
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-5">
            <div className="space-y-2">
              <Label htmlFor="fullName">Full Name</Label>
              <Input
                id="fullName"
                type="text"
                placeholder="John Doe"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="h-11"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-11"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-11 pr-10"
                  required
                  minLength={6}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[#64748B] hover:text-[#1E293B]"
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="confirmPassword">Confirm Password</Label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder="••••••••"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="h-11"
                required
              />
            </div>

            <div className="space-y-2">
              <Label>I am a</Label>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setRole('teacher')}
                  className={`flex-1 rounded-lg border-2 px-4 py-3 text-sm font-medium transition-all ${
                    role === 'teacher'
                      ? 'border-[#4F46E5] bg-indigo-50 text-[#4F46E5]'
                      : 'border-gray-200 text-[#64748B] hover:border-gray-300'
                  }`}
                >
                  👩‍🏫 Teacher
                </button>
                <button
                  type="button"
                  onClick={() => setRole('admin')}
                  className={`flex-1 rounded-lg border-2 px-4 py-3 text-sm font-medium transition-all ${
                    role === 'admin'
                      ? 'border-[#4F46E5] bg-indigo-50 text-[#4F46E5]'
                      : 'border-gray-200 text-[#64748B] hover:border-gray-300'
                  }`}
                >
                  🔑 Admin
                </button>
              </div>
            </div>

            <Button
              type="submit"
              disabled={loading}
              className="h-11 w-full bg-[#4F46E5] text-white hover:bg-indigo-700"
            >
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Account
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-[#64748B]">
            Already have an account?{' '}
            <Link
              to="/login"
              className="font-medium text-[#4F46E5] hover:text-indigo-700"
            >
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
