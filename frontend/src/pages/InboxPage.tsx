import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Loading,
  Modal,
  Pagination,
  Select,
  Table,
  toneForStatus,
  type Column,
} from "@/components/ui";
import { errorMessage } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";
import { useInbox, usePollInbox } from "@/hooks/queries";
import type { InboundEmail } from "@/types";

export default function InboxPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [intent, setIntent] = useState("");
  const [selected, setSelected] = useState<InboundEmail | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const query = useInbox(page, status || undefined, intent || undefined);
  const poll = usePollInbox();

  async function runPoll() {
    setNotice(null);
    try {
      const summary = await poll.mutateAsync();
      if (summary.configured === false) {
        setNotice("IMAP chưa được cấu hình — không có thư nào được tải.");
      } else {
        setNotice(
          `Đã tải ${summary.fetched} thư · xử lý ${summary.processed} · lỗi ${summary.failed} · bỏ qua ${summary.skipped}`,
        );
      }
    } catch (err) {
      setNotice(`Lỗi: ${errorMessage(err)}`);
    }
  }

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return <ErrorState message="Không tải được hộp thư." onRetry={() => void query.refetch()} />;
  }

  const rows = query.data?.items ?? [];

  const columns: Column<InboundEmail>[] = [
    {
      key: "from",
      header: "Người gửi",
      render: (e) => (
        <div>
          <p className="font-medium text-slate-700">{e.from_address}</p>
          <p className="max-w-xs truncate text-xs text-slate-500">{e.subject ?? "(không tiêu đề)"}</p>
        </div>
      ),
    },
    { key: "intent", header: "Phân loại", render: (e) => <Badge tone="info">{e.intent}</Badge> },
    {
      key: "status",
      header: "Trạng thái",
      render: (e) => (
        <div className="space-y-1">
          <Badge tone={toneForStatus(e.status)}>{e.status}</Badge>
          {e.error && <p className="max-w-[16rem] truncate text-xs text-rose-600">{e.error}</p>}
        </div>
      ),
    },
    {
      key: "conversation",
      header: "Hội thoại",
      render: (e) => (
        <span className="text-xs text-slate-400">
          {e.conversation_id ? `${e.conversation_id.slice(0, 8)}…` : "—"}
        </span>
      ),
    },
    {
      key: "created",
      header: "Nhận lúc",
      render: (e) => <span className="text-xs text-slate-500">{formatDateTime(e.created_at)}</span>,
    },
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Hộp thư đến</h1>
          <p className="text-sm text-slate-500">
            Email của khách được phân loại tự động và xử lý bởi agent.
          </p>
        </div>
        <Button onClick={() => void runPoll()} disabled={poll.isPending}>
          {poll.isPending ? "Đang kiểm tra…" : "Kiểm tra thư mới"}
        </Button>
      </header>

      {notice && (
        <p className="rounded-lg bg-brand-50 px-3 py-2 text-xs text-brand-700">{notice}</p>
      )}

      <div className="flex flex-wrap gap-3">
        <Select
          aria-label="Lọc theo trạng thái"
          className="w-44"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Mọi trạng thái</option>
          <option value="received">Đã nhận</option>
          <option value="processing">Đang xử lý</option>
          <option value="processed">Đã xử lý</option>
          <option value="failed">Lỗi</option>
          <option value="ignored">Bỏ qua</option>
        </Select>

        <Select
          aria-label="Lọc theo phân loại"
          className="w-52"
          value={intent}
          onChange={(e) => {
            setIntent(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Mọi phân loại</option>
          <option value="flight_booking">Đặt vé</option>
          <option value="booking_change">Thay đổi vé</option>
          <option value="refund_request">Yêu cầu hoàn tiền</option>
          <option value="invoice_or_document">Hoá đơn / tài liệu</option>
          <option value="complaint">Khiếu nại</option>
          <option value="general_inquiry">Hỏi chung</option>
          <option value="spam">Spam</option>
          <option value="unknown">Không xác định</option>
        </Select>
      </div>

      <Card>
        <Table
          columns={columns}
          rows={rows}
          keyOf={(e) => e.id}
          onRowClick={setSelected}
          empty={
            <EmptyState
              title="Hộp thư trống"
              hint="Bấm “Kiểm tra thư mới” để tải email từ hộp thư IMAP."
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

      <Modal open={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.subject ?? "(không tiêu đề)"}>
        {selected && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge tone="info">{selected.intent}</Badge>
              <Badge tone={toneForStatus(selected.status)}>{selected.status}</Badge>
            </div>
            <p className="text-xs text-slate-500">
              Từ <strong>{selected.from_address}</strong> · {formatDateTime(selected.created_at)}
            </p>
            {selected.error && (
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {selected.error}
              </p>
            )}
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
              {selected.body_text ?? "(không có nội dung)"}
            </pre>
          </div>
        )}
      </Modal>
    </div>
  );
}
