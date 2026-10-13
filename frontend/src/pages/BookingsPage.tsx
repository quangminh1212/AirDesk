import { useState } from "react";

import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  JsonBlock,
  Loading,
  Modal,
  Pagination,
  Table,
  toneForStatus,
  type Column,
} from "@/components/ui";
import { bookingApi } from "@/api";
import { useQuery } from "@tanstack/react-query";
import { formatDateTime, formatVnd } from "@/lib/format";
import type { Booking, Page } from "@/types";

/** Bookings may come back as a `Page[Booking]` envelope or a bare array. */
function toPage(data: Page<Booking> | Booking[] | undefined): {
  items: Booking[];
  page?: number;
  pages?: number;
  total?: number;
} {
  if (!data) return { items: [] };
  if (Array.isArray(data)) return { items: data };
  return { items: data.items, page: data.page, pages: data.pages, total: data.total };
}

export default function BookingsPage() {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Booking | null>(null);

  const query = useQuery({
    queryKey: ["bookings", page],
    queryFn: () => bookingApi.list({ page, page_size: 20 }),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return (
      <ErrorState message="Không tải được danh sách đặt chỗ." onRetry={() => void query.refetch()} />
    );
  }

  const { items, page: currentPage, pages, total } = toPage(query.data);

  const columns: Column<Booking>[] = [
    {
      key: "reference",
      header: "Mã đặt chỗ",
      render: (b) => (
        <div>
          <p className="font-medium text-slate-700">{b.reference}</p>
          {b.pnr && <p className="text-xs text-slate-400">PNR {b.pnr}</p>}
        </div>
      ),
    },
    { key: "status", header: "Trạng thái", render: (b) => <Badge tone={toneForStatus(b.status)}>{b.status}</Badge> },
    { key: "provider", header: "Nguồn", render: (b) => <Badge tone="neutral">{b.provider}</Badge> },
    {
      key: "amount",
      header: "Tổng tiền",
      render: (b) => (
        <span className="font-medium text-slate-700">{formatVnd(b.total_amount, b.currency)}</span>
      ),
    },
    {
      key: "contact",
      header: "Liên hệ",
      render: (b) => <span className="text-xs text-slate-500">{b.contact_email}</span>,
    },
    {
      key: "created",
      header: "Tạo lúc",
      render: (b) => <span className="text-xs text-slate-500">{formatDateTime(b.created_at)}</span>,
    },
  ];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-800">Đặt chỗ</h1>
        <p className="text-sm text-slate-500">Tất cả đặt chỗ do agent hoặc nhân viên tạo.</p>
      </header>

      <Card>
        <Table
          columns={columns}
          rows={items}
          keyOf={(b) => b.id}
          onRowClick={setSelected}
          empty={<EmptyState title="Chưa có đặt chỗ nào" />}
        />
        <Pagination
          page={currentPage ?? 1}
          pages={pages ?? 1}
          total={total}
          onChange={setPage}
        />
      </Card>

      <Modal open={Boolean(selected)} onClose={() => setSelected(null)} title={`Đặt chỗ ${selected?.reference ?? ""}`}>
        {selected && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Badge tone={toneForStatus(selected.status)}>{selected.status}</Badge>
              <Badge tone="neutral">{selected.provider}</Badge>
            </div>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <dt className="text-slate-500">Tổng tiền</dt>
              <dd className="font-medium">{formatVnd(selected.total_amount, selected.currency)}</dd>
              <dt className="text-slate-500">Email</dt>
              <dd>{selected.contact_email}</dd>
              <dt className="text-slate-500">Điện thoại</dt>
              <dd>{selected.contact_phone ?? "—"}</dd>
              <dt className="text-slate-500">PNR</dt>
              <dd>{selected.pnr ?? "—"}</dd>
            </dl>
            <JsonBlock value={selected} />
          </div>
        )}
      </Modal>
    </div>
  );
}
