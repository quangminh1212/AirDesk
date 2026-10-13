import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  Badge,
  Card,
  ErrorState,
  Loading,
  Pagination,
  Select,
  Table,
  toneForStatus,
  type Column,
} from "@/components/ui";
import { formatRelative } from "@/lib/format";
import { useConversations } from "@/hooks/queries";
import type { Conversation } from "@/types";

export default function ConversationsPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const navigate = useNavigate();
  const query = useConversations(page);

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return (
      <ErrorState
        message="Không tải được danh sách hội thoại."
        onRetry={() => void query.refetch()}
      />
    );
  }

  const all = query.data?.items ?? [];
  const rows = status ? all.filter((c) => c.status === status) : all;

  const columns: Column<Conversation>[] = [
    {
      key: "customer",
      header: "Khách",
      render: (c) => (
        <div>
          <p className="font-medium text-slate-700">{c.customer_ref ?? "Khách ẩn danh"}</p>
          <p className="text-xs text-slate-400">
            <code>{c.id.slice(0, 8)}…</code>
          </p>
        </div>
      ),
    },
    { key: "channel", header: "Kênh", render: (c) => <Badge tone="neutral">{c.channel}</Badge> },
    {
      key: "status",
      header: "Trạng thái",
      render: (c) => <Badge tone={toneForStatus(c.status)}>{c.status}</Badge>,
    },
    {
      key: "summary",
      header: "Tóm tắt",
      render: (c) => (
        <span className="text-xs text-slate-500">{c.summary ?? "—"}</span>
      ),
    },
    {
      key: "created",
      header: "Tạo lúc",
      render: (c) => <span className="text-xs text-slate-500">{formatRelative(c.created_at)}</span>,
    },
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Hội thoại</h1>
          <p className="text-sm text-slate-500">Tất cả lượt trao đổi giữa khách và agent.</p>
        </div>
        <div className="w-44">
          <Select
            aria-label="Lọc theo trạng thái"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">Mọi trạng thái</option>
            <option value="open">Đang mở</option>
            <option value="pending_human">Chờ nhân viên</option>
            <option value="resolved">Đã xử lý</option>
            <option value="closed">Đã đóng</option>
          </Select>
        </div>
      </header>

      <Card>
        <Table
          columns={columns}
          rows={rows}
          keyOf={(c) => c.id}
          onRowClick={(c) => navigate(`/conversations/${c.id}`)}
          empty={<p className="py-8 text-center text-sm text-slate-400">Chưa có hội thoại.</p>}
        />
        <Pagination
          page={query.data?.page ?? 1}
          pages={query.data?.pages ?? 1}
          total={query.data?.total}
          onChange={setPage}
        />
      </Card>
    </div>
  );
}
