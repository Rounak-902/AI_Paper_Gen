import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { FileText, Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

export default function LoginPage() {
  const { signIn, resetPassword } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [loading, setLoading] = useState(false);
  const [forgotMode, setForgotMode] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      toast.error('Please fill in all fields');
      return;
    }
    setLoading(true);
    const { error } = await signIn(email, password);
    setLoading(false);
    if (error) {
      toast.error(error);
    } else {
      toast.success('Welcome back!');
      navigate('/dashboard');
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) {
      toast.error('Please enter your email');
      return;
    }
    setLoading(true);
    const { error } = await resetPassword(email);
    setLoading(false);
    if (error) {
      toast.error(error);
    } else {
      toast.success('Password reset link sent to your email');
      setForgotMode(false);
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
          <p className="mt-4 max-w-md text-sm text-gray-400">
            Create professional examination papers in minutes using artificial
            intelligence. Select your board, class, subject, and let AI generate
            perfectly crafted question papers.
          </p>
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

          <h2 className="text-2xl font-bold text-[#1E293B]">
            {forgotMode ? 'Reset Password' : 'Welcome back'}
          </h2>
          <p className="mt-1 text-sm text-[#64748B]">
            {forgotMode
              ? 'Enter your email to receive a reset link'
              : 'Sign in to your account to continue'}
          </p>

          <form
            onSubmit={forgotMode ? handleForgot : handleLogin}
            className="mt-8 space-y-5"
          >
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

            {!forgotMode && (
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
            )}

            {!forgotMode && (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="remember"
                    checked={rememberMe}
                    onCheckedChange={(checked) =>
                      setRememberMe(checked as boolean)
                    }
                  />
                  <Label htmlFor="remember" className="text-sm font-normal text-[#64748B]">
                    Remember me
                  </Label>
                </div>
                <button
                  type="button"
                  onClick={() => setForgotMode(true)}
                  className="text-sm font-medium text-[#4F46E5] hover:text-indigo-700"
                >
                  Forgot password?
                </button>
              </div>
            )}

            <Button
              type="submit"
              disabled={loading}
              className="h-11 w-full bg-[#4F46E5] text-white hover:bg-indigo-700"
            >
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {forgotMode ? 'Send Reset Link' : 'Sign In'}
            </Button>

            {forgotMode && (
              <button
                type="button"
                onClick={() => setForgotMode(false)}
                className="w-full text-center text-sm text-[#4F46E5] hover:text-indigo-700"
              >
                ← Back to login
              </button>
            )}
          </form>

          {!forgotMode && (
            <p className="mt-6 text-center text-sm text-[#64748B]">
              Don't have an account?{' '}
              <Link
                to="/signup"
                className="font-medium text-[#4F46E5] hover:text-indigo-700"
              >
                Sign up
              </Link>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
