/** Route guard: requires an authenticated user (and optionally a minimum role). */

import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { Loading } from "@/components/ui";
import { useAuth } from "@/store/auth";
import type { Role } from "@/types";

export default function ProtectedRoute({
  children,
  minimumRole,
}: {
  children: React.ReactNode;
  minimumRole?: Role;
}) {
  const { user, initialised, loadUser, hasRole } = useAuth();
  const location = useLocation();

  useEffect(() => {
    if (!initialised) void loadUser();
  }, [initialised, loadUser]);

  if (!initialised) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loading label="Đang kiểm tra đăng nhập…" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (minimumRole && !hasRole(minimumRole)) {
    return (
      <div className="p-8">
        <div className="card">
          <h1 className="text-base font-semibold text-rose-600">Không có quyền truy cập</h1>
          <p className="mt-1 text-sm text-slate-500">
            Trang này yêu cầu quyền <strong>{minimumRole}</strong>. Vai trò hiện tại của bạn là{" "}
            <strong>{user.role}</strong>.
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
