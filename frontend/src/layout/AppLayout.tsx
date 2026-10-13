/** Sidebar + topbar shell for every authenticated page. */

import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import clsx from "clsx";

import { Button } from "@/components/ui";
import { useAuth } from "@/store/auth";
import type { Role } from "@/types";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  minimumRole?: Role;
}

const NAV: NavItem[] = [
  { to: "/", label: "Tổng quan", icon: "▤" },
  { to: "/chat", label: "Trợ lý AI", icon: "✦" },
  { to: "/conversations", label: "Hội thoại", icon: "☰" },
  { to: "/escalations", label: "Chuyển nhân viên", icon: "⚑", minimumRole: "OPERATOR" },
  { to: "/qa", label: "Kiểm tra chất lượng", icon: "✓", minimumRole: "OPERATOR" },
  { to: "/tasks", label: "Công việc", icon: "◫" },
  { to: "/documents", label: "Tài liệu", icon: "⎘" },
  { to: "/inbox", label: "Hộp thư", icon: "✉" },
  { to: "/bookings", label: "Đặt chỗ", icon: "✈" },
];

export default function AppLayout() {
  const { user, logout, hasRole } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const visible = NAV.filter((item) => !item.minimumRole || hasRole(item.minimumRole));

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  const sidebar = (
    <nav aria-label="Điều hướng chính" className="flex h-full flex-col gap-1 p-3">
      <div className="mb-4 px-2">
        <p className="text-lg font-semibold text-white">AirDesk</p>
        <p className="text-xs text-slate-400">AI cho đại lý vé &amp; vận hành</p>
      </div>
      {visible.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            clsx(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
              isActive
                ? "bg-brand-600 text-white"
                : "text-slate-300 hover:bg-slate-800 hover:text-white",
            )
          }
        >
          <span aria-hidden className="w-4 text-center">
            {item.icon}
          </span>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="flex h-full bg-slate-100">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 bg-slate-900 md:block">{sidebar}</aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-slate-900/50"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <aside className="absolute left-0 top-0 h-full w-60 bg-slate-900">{sidebar}</aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
          <button
            type="button"
            aria-label="Mở menu"
            className="rounded p-2 text-slate-500 hover:bg-slate-100 md:hidden"
            onClick={() => setOpen(true)}
          >
            ☰
          </button>
          <div className="ml-auto flex items-center gap-3">
            <div className="text-right">
              <p className="text-sm font-medium text-slate-700">{user?.full_name || user?.email}</p>
              <p className="text-xs text-slate-400">{user?.role}</p>
            </div>
            <Button variant="ghost" onClick={handleLogout}>
              Đăng xuất
            </Button>
          </div>
        </header>

        <main className="min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
