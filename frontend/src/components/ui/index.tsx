/** Small, dependency-free primitives used across the app. */

import clsx from "clsx";
import type { ReactNode } from "react";

// --- Card ----------------------------------------------------------------

export function Card({
  children,
  className,
  title,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className={clsx("card", className)}>
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between gap-3">
          {title ? <h2 className="text-sm font-semibold text-slate-700">{title}</h2> : <span />}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

// --- Badge ---------------------------------------------------------------

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700",
  info: "bg-brand-100 text-brand-700",
  success: "bg-emerald-100 text-emerald-700",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-rose-100 text-rose-700",
};

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return <span className={clsx("badge", TONE_CLASSES[tone], className)}>{children}</span>;
}

/** Map common backend status strings onto badge tones. */
export function toneForStatus(status: string | null | undefined): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "open":
    case "uploaded":
    case "received":
    case "todo":
      return "info";
    case "pending_human":
    case "extracting":
    case "in_progress":
    case "review":
    case "claimed":
      return "warning";
    case "resolved":
    case "extracted":
    case "processed":
    case "done":
    case "pass":
    case "active":
      return "success";
    case "failed":
    case "fail":
    case "blocked":
    case "rejected":
    case "cancelled":
      return "danger";
    default:
      return "neutral";
  }
}

/** Map a QA finding severity onto a badge tone. */
export function toneForSeverity(severity: string | null | undefined): Tone {
  switch ((severity ?? "").toLowerCase()) {
    case "info":
      return "info";
    case "warning":
      return "warning";
    case "error":
    case "critical":
      return "danger";
    default:
      return "neutral";
  }
}

/** Map an escalation priority (1=high .. 3=low) onto a badge tone. */
export function toneForPriority(priority: number | null | undefined): Tone {
  if (priority === 1) return "danger";
  if (priority === 2) return "warning";
  return "neutral";
}

export function priorityLabel(priority: number | null | undefined): string {
  if (priority === 1) return "Cao";
  if (priority === 2) return "Trung bình";
  if (priority === 3) return "Thấp";
  return "—";
}

// --- Button --------------------------------------------------------------

export function Button({
  children,
  variant = "primary",
  className,
  type = "button",
  ...rest
}: {
  children: ReactNode;
  variant?: "primary" | "ghost" | "danger";
  className?: string;
  type?: "button" | "submit";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const base = variant === "primary" ? "btn-primary" : "btn-ghost";
  const danger = "btn bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50";
  return (
    <button type={type} className={clsx(variant === "danger" ? danger : base, className)} {...rest}>
      {children}
    </button>
  );
}

// --- Feedback ------------------------------------------------------------

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Đang tải"
      className={clsx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600",
        className,
      )}
    />
  );
}

export function Loading({ label = "Đang tải…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <p className="text-sm font-medium text-slate-600">{title}</p>
      {hint && <p className="max-w-md text-xs text-slate-400">{hint}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <p className="text-sm font-medium text-rose-600">{message}</p>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          Thử lại
        </Button>
      )}
    </div>
  );
}

// --- Stat card -----------------------------------------------------------

export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="card">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p
        className={clsx(
          "mt-1 text-2xl font-semibold",
          tone === "danger" ? "text-rose-600" : "text-slate-800",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

// --- Table ---------------------------------------------------------------

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
}

export function Table<T>({
  columns,
  rows,
  keyOf,
  onRowClick,
  empty,
}: {
  columns: Column<T>[];
  rows: T[];
  keyOf: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
}) {
  if (rows.length === 0) {
    return <>{empty ?? <EmptyState title="Không có dữ liệu" />}</>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left">
            {columns.map((c) => (
              <th
                key={c.key}
                className={clsx(
                  "whitespace-nowrap px-3 py-2 font-medium text-slate-500",
                  c.className,
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={keyOf(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={clsx(
                "border-b border-slate-100 last:border-0",
                onRowClick && "cursor-pointer-pointer hover:bg-slate-50",
              )}
            >
              {columns.map((c) => (
                <td key={c.key} className={clsx("px-3 py-2 align-top", c.className)}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Pagination ----------------------------------------------------------

export function Pagination({
  page,
  pages,
  onChange,
  total,
}: {
  page: number;
  pages: number;
  onChange: (page: number) => void;
  total?: number;
}) {
  if (pages <= 1) {
    return total !== undefined ? (
      <p className="pt-3 text-xs text-slate-400">{total} bản ghi</p>
    ) : null;
  }
  return (
    <div className="flex items-center justify-between gap-3 pt-3">
      <p className="text-xs text-slate-400">
        Trang {page}/{pages}
        {total !== undefined ? ` · ${total} bản ghi` : ""}
      </p>
      <div className="flex gap-2">
        <Button variant="ghost" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Trước
        </Button>
        <Button variant="ghost" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          Sau
        </Button>
      </div>
    </div>
  );
}

// --- Form fields ---------------------------------------------------------

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

export function Select({
  children,
  className,
  ...rest
}: { children: ReactNode; className?: string } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={clsx("input", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({
  className,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={clsx("input", className)} {...rest} />;
}

// --- Modal ---------------------------------------------------------------

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={typeof title === "string" ? title : "Hộp thoại"}
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <h2 className="text-base font-semibold text-slate-800">{title}</h2>
          <button
            type="button"
            aria-label="Đóng"
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        {children}
        {footer && <div className="mt-4 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

// --- Misc ----------------------------------------------------------------

/** A collapsible block used for tool calls / extracted JSON. */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="rounded-lg border border-slate-200 bg-slate-50/60">
      <summary className="cursor-pointer-pointer px-3 py-2 text-xs font-medium text-slate-600">
        {summary}
      </summary>
      <div className="border-t border-slate-200 px-3 py-2">{children}</div>
    </details>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-64 overflow-auto rounded bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
