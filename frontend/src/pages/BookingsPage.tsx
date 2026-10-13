import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  Badge,
  Button,
  Card,
  Disclosure,
  EmptyState,
  ErrorState,
  JsonBlock,
  Loading,
  Modal,
  Pagination,
  Spinner,
  Table,
  toneForStatus,
  type Column,
} from "@/components/ui";
import { bookingApi } from "@/api";
import { formatDateTime, formatVnd } from "@/lib/format";
import type { Booking, BookingDetail, Page } from "@/types";

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

function BookingDetailBody({
  base,
  detail,
  loading,
}: {
  base: Booking;
  detail: BookingDetail | undefined;
  loading: boolean;
}) {
  const b = detail ?? base;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Badge tone={toneForStatus(b.status)}>{b.status}</Badge>
        <Badge tone="neutral">{b.provider}</Badge>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <dt className="text-slate-500">Tổng tiền</dt>
        <dd className="font-medium">{formatVnd(b.total_amount, b.currency)}</dd>
        <dt className="text-slate-500">Email</dt>
        <dd>{b.contact_email}</dd>
        <dt className="text-slate-500">Điện thoại</dt>
        <dd>{b.contact_phone ?? "—"}</dd>
        <dt className="text-slate-500">PNR</dt>
        <dd>{b.pnr ?? "—"}</dd>
      </dl>

      {loading && (
        <p className="flex items-center gap-2 text-xs text-slate-400">
          <Spinner /> Đang tải chi tiết…
        </p>
      )}

      {detail && detail.passengers.length > 0 && (
        <Disclosure summary={`Hành khách (${detail.passengers.length})`} defaultOpen>
          <ul className="space-y-1 text-sm text-slate-600">
            {detail.passengers.map((p) => (
              <li key={p.id}>
                {p.last_name} {p.first_name} · {p.passenger_type}
                {p.document_number ? ` · ${p.document_number}` : ""}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {detail && detail.tickets.length > 0 && (
        <Disclosure summary={`Vé (${detail.tickets.length})`} defaultOpen>
          <ul className="space-y-1 text-sm text-slate-600">
            {detail.tickets.map((t) => (
              <li key={t.id} className="flex items-center gap-2">
                <Badge tone={toneForStatus(t.status)}>{t.status}</Badge>
                <span className="font-medium">{t.ticket_number || "chưa cấp số"}</span>
                <span className="text-xs text-slate-400">
                  {t.flight_number} · {formatVnd(t.fare_amount + t.tax_amount, b.currency)}
                </span>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {detail && detail.events.length > 0 && (
        <Disclosure summary={`Lịch sử xử lý (${detail.events.length})`}>
          <ul className="space-y-1 text-xs text-slate-500">
            {detail.events.map((e) => (
              <li key={e.id}>
                <span className="font-medium text-slate-700">{e.event_type}</span> · {e.actor} ·{" "}
                {formatDateTime(e.occurred_at)}
                {e.to_status ? ` → ${e.to_status}` : ""}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      <JsonBlock value={b} />
    </div>
  );
}

export default function BookingsPage() {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Booking | null>(null);

  const query = useQuery({
    queryKey: ["bookings", page],
    queryFn: () => bookingApi.list({ page, page_size: 20 }),
  });

  const detailQuery = useQuery({
    queryKey: ["booking", selected?.id],
    queryFn: () => bookingApi.get(selected!.id),
    enabled: Boolean(selected),
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
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Đặt chỗ</h1>
          <p className="text-sm text-slate-500">Tất cả đặt chỗ do agent hoặc nhân viên tạo.</p>
        </div>
        <Link to="/flights">
          <Button variant="primary">Đặt vé mới</Button>
        </Link>
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

      <Modal
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={`Đặt chỗ ${selected?.reference ?? ""}`}
      >
        {selected && (
          <BookingDetailBody
            base={selected}
            detail={detailQuery.data}
            loading={detailQuery.isLoading}
          />
        )}
      </Modal>
    </div>
  );
}
