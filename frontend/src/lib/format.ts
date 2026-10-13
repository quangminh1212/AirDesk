/** Formatting helpers shared across the UI. */

import { format, formatDistanceToNowStrict, isValid, parseISO } from "date-fns";

/** Format an amount as Vietnamese dong, e.g. `1.450.000 ₫`. */
export function formatVnd(value: number | null | undefined, currency = "VND"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  try {
    return new Intl.NumberFormat("vi-VN", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${value.toLocaleString("vi-VN")} ${currency}`;
  }
}

/** Format a plain number with Vietnamese thousand separators. */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("vi-VN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function toDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = typeof iso === "string" ? parseISO(iso) : new Date(iso);
  return isValid(d) ? d : null;
}

/** `01/12/2026 14:30` in local time. */
export function formatDateTime(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? format(d, "dd/MM/yyyy HH:mm") : "—";
}

/** `01/12/2026`. */
export function formatDate(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? format(d, "dd/MM/yyyy") : "—";
}

/** `5 phút trước`. */
export function formatRelative(iso: string | null | undefined): string {
  const d = toDate(iso);
  if (!d) return "—";
  return `${formatDistanceToNowStrict(d, { addSuffix: true })}`;
}

/** `2h44m` from a minute count. */
export function formatDuration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return "—";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** `85.0%` from a 0–1 ratio. */
export function formatPercent(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** `in_progress` -> `In progress`. */
export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  const spaced = value.replace(/[_-]+/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** `1.2 MB` from a byte count. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** True when a due date is in the past and the task is not finished. */
export function isOverdue(dueAt: string | null | undefined, status?: string): boolean {
  const d = toDate(dueAt);
  if (!d) return false;
  if (status === "done" || status === "cancelled") return false;
  return d.getTime() < Date.now();
}
