import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { Button, Card, Field, Spinner } from "@/components/ui";
import { useAuth } from "@/store/auth";

export default function LoginPage() {
  const { login, loading, error, user, initialised, loadUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  // If a session already exists, skip the form.
  if (initialised && user) {
    const from = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={from} replace />;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await login(email.trim(), password);
    if (ok) {
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-slate-100 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-semibold text-slate-800">AirDesk</h1>
          <p className="mt-1 text-sm text-slate-500">
            Nền tảng AI cho đại lý vé máy bay &amp; vận hành
          </p>
        </div>

        <Card>
          <form onSubmit={onSubmit} className="space-y-4">
            <Field label="Email">
              <input
                className="input"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@airdesk.vn"
              />
            </Field>

            <Field label="Mật khẩu">
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </Field>

            {error && (
              <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {error}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? <Spinner className="border-white/40 border-t-white" /> : null}
              {loading ? "Đang đăng nhập…" : "Đăng nhập"}
            </Button>
          </form>
        </Card>

        <button
          type="button"
          className="mt-3 w-full text-center text-xs text-slate-400 underline hover:text-slate-600"
          onClick={() => void loadUser()}
        >
          Đã đăng nhập ở tab khác? Tải lại phiên
        </button>

        <p className="mt-4 text-center text-xs leading-relaxed text-slate-400">
          Tài khoản demo (sau khi chạy <code className="rounded bg-slate-200 px-1">python -m
          scripts.seed</code>):
          <br />
          <code className="text-slate-500">admin@airdesk.vn / admin12345</code> ·{" "}
          <code className="text-slate-500">operator@airdesk.vn / operator12345</code>
        </p>
      </div>
    </div>
  );
}
