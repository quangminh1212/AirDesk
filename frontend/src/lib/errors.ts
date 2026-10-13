/**
 * Error helpers.
 *
 * The AirDesk backend returns RFC 7807-style problem payloads shaped as
 * `{"error": {"code", "message", "details", "request_id"}}` (see
 * `backend/app/core/errors.py`). FastAPI's own validation failures are
 * normalised into that same envelope, so this is the only shape we need to
 * understand — but we still fall back defensively.
 */

import axios from "axios";

interface ProblemEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
}

export interface AppErrorInfo {
  message: string;
  code?: string;
  status?: number;
  requestId?: string;
  details?: Record<string, unknown>;
}

/** Turn any thrown value into a displayable error. */
export function describeError(error: unknown): AppErrorInfo {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const payload = error.response?.data as ProblemEnvelope | undefined;
    const envelope = payload?.error;

    if (envelope?.message) {
      return {
        message: envelope.message,
        code: envelope.code,
        status,
        requestId: envelope.request_id,
        details: envelope.details,
      };
    }

    // FastAPI's default validation shape, just in case.
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") {
      return { message: detail, status };
    }
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string } | undefined;
      return { message: first?.msg ?? "Yêu cầu không hợp lệ.", status };
    }

    if (error.code === "ECONNABORTED") {
      return { message: "Yêu cầu quá thời gian chờ. Vui lòng thử lại.", status };
    }
    if (!error.response) {
      return { message: "Không kết nối được máy chủ. Kiểm tra kết nối mạng.", status };
    }
    return { message: error.message || "Đã xảy ra lỗi.", status };
  }

  if (error instanceof Error) {
    return { message: error.message };
  }
  return { message: String(error ?? "Đã xảy ra lỗi không xác định.") };
}

/** Convenience: just the human-readable message. */
export function errorMessage(error: unknown): string {
  return describeError(error).message;
}
