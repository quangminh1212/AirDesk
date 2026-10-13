import { useState } from "react";
import { Link } from "react-router-dom";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Loading,
  Modal,
  Pagination,
  Select,
  Table,
  Textarea,
  priorityLabel,
  toneForPriority,
  toneForStatus,
  type Column,
} from "@/components/ui";
import { errorMessage } from "@/lib/errors";
import { formatRelative } from "@/lib/format";
import { useEscalations, useResolveEscalation } from "@/hooks/queries";
import type { Escalation } from "@/types";

export default function EscalationsPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("open");
  const [target, setTarget] = useState<Escalation | null>(null);
  const [note, setNote] = useState("");
  const [action, setAction] = useState<"resolve" | "reject">("resolve");
  const [error, setError] = useState<string | null>(null);

  const query = useEscalations(page, status || undefined);
  const resolve = useResolveEscalation();

  async function submit() {
    if (!target) return;
    if (note.trim().length < 2) {
      setError("Vui lòng nhập ghi chú xử lý (ít nhất 2 ký tự).");
      return;
    }
    setError(null);
    try {
      await resolve.mutateAsync({ id: target.id, resolution_note: note.trim(), action });
      setTarget(null);
      setNote("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return (
      <ErrorState message="Không tải được hàng đợi." onRetry={() => void query.refetch()} />
    );
  }

  const rows = query.data?.items ?? [];

  const columns: Column<Escalation>[] = [
    {
      key: "priority",
      header: "Ưu tiên",
      render: (e) => <Badge tone={toneForPriority(e.priority)}>{priorityLabel(e.priority)}</Badge>,
    },
    {
      key: "reason",
      header: "Lý do",
      render: (e) => (
        <div>
          <p className="font-medium text-slate-700">{e.reason}</p>
          {e.details && (
            <p className="text-xs text-slate-400">
              {(e.details.qa_findings as string[] | undefined)?.join(", ") ?? ""}
            </p>
          )}
        </div>
      ),
    },
    {
      key: "status",
      header: "Trạng thái",
      render: (e) => <Badge tone={toneForStatus(e.status)}>{e.status}</Badge>,
    },
    {
      key: "conversation",
      header: "Hội thoại",
      render: (e) =>
        e.conversation_id ? (
          <Link
            to={`/conversations/${e.conversation_id}`}
            className="text-xs text-brand-600 underline"
            onClick={(ev) => ev.stopPropagation()}
          >
            {e.conversation_id.slice(0, 8)}…
          </Link>
        ) : (
          <span className="text-xs text-slate-400">—</span>
        ),
    },
    {
      key: "created",
      header: "Thời gian",
      render: (e) => (
        <span className="text-xs text-slate-500">{formatRelative(e.created_at)}</span>
      ),
    },
    {
      key: "actions",
      header: "",
      render: (e) =>
        e.status === "open" || e.status === "claimed" ? (
          <Button
            variant="ghost"
            onClick={(ev) => {
              ev.stopPropagation();
              setTarget(e);
              setNote("");
              setAction("resolve");
              setError(null);
            }}
          >
            Xử lý
          </Button>
        ) : (
          <span className="text-xs text-slate-400">{e.resolution_note ?? "—"}</span>
        ),
    },
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Chuyển cho nhân viên</h1>
          <p className="text-sm text-slate-500">
            Các trường hợp agent không tự xử lý: cần người quyết định.
          </p>
        </div>
        <div className="w-44">
          <Select
            aria-label="Lọc theo trạng thái"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Tất cả</option>
            <option value="open">Đang mở</option>
            <option value="claimed">Đã nhận</option>
            <option value="resolved">Đã xử lý</option>
            <option value="rejected">Từ chối</option>
          </Select>
        </div>
      </header>

      <Card>
        <Table
          columns={columns}
          rows={rows}
          keyOf={(e) => e.id}
          empty={
            <EmptyState
              title="Hàng đợi trống"
              hint="Không có trường hợp nào cần nhân viên xử lý."
            />
          }
        />
        <Pagination
          page={query.data?.page ?? 1}
          pages={query.data?.pages ?? 1}
          total={query.data?.total}
          onChange={setPage}
        />
      </Card>

      <Modal
        open={Boolean(target)}
        onClose={() => setTarget(null)}
        title="Xử lý chuyển việc"
        footer={
          <>
            <Button variant="ghost" onClick={() => setTarget(null)}>
              Huỷ
            </Button>
            <Button onClick={() => void submit()} disabled={resolve.isPending}>
              {resolve.isPending ? "Đang lưu…" : "Xác nhận"}
            </Button>
          </>
        }
      >
        {target && (
          <div className="space-y-3">
            <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm">
              <p className="font-medium text-slate-700">{target.reason}</p>
              <p className="mt-1 text-xs text-slate-500">
                Ưu tiên {priorityLabel(target.priority)} · {formatRelative(target.created_at)}
              </p>
            </div>

            <Field label="Hành động">
              <Select
                value={action}
                onChange={(e) => setAction(e.target.value as "resolve" | "reject")}
              >
                <option value="resolve">Đã xử lý</option>
                <option value="reject">Từ chối / không cần xử lý</option>
              </Select>
            </Field>

            <Field label="Ghi chú xử lý">
              <Textarea
                rows={3}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Mô tả đã làm gì cho khách…"
              />
            </Field>

            {error && (
              <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {error}
              </p>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
