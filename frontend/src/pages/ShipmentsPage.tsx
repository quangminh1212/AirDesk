/** Trang Vận chuyển: quản lý lô hàng, theo dõi trạng thái và sự kiện quét. */

import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  Field,
  Modal,
  Pagination,
  Select,
  Spinner,
  StatCard,
  Table,
  Textarea,
  toneForStatus,
  type Column,
} from "@/components/ui";
import {
  useAddShipmentEvent,
  useCancelShipment,
  useCreateShipment,
  useShipment,
  useShipments,
  useShipmentStats,
  useUpdateShipmentStatus,
} from "@/hooks/queries";
import { formatDateTime, formatNumber, formatRelative, formatVnd, titleCase } from "@/lib/format";
import type {
  ShipmentCreate,
  ShipmentMode,
  ShipmentPriority,
  ShipmentStatus,
  ShipmentSummary,
} from "@/types";

const SHIPMENT_STATUSES: ShipmentStatus[] = [
  "DRAFT",
  "BOOKED",
  "PICKED_UP",
  "IN_TRANSIT",
  "CUSTOMS",
  "OUT_FOR_DELIVERY",
  "DELIVERED",
  "DELAYED",
  "EXCEPTION",
  "CANCELLED",
  "RETURNED",
];

const SHIPMENT_MODES: ShipmentMode[] = ["AIR", "SEA", "ROAD", "RAIL", "COURIER"];

const EVENT_TYPES = [
  "PICKUP",
  "DEPARTURE",
  "ARRIVAL",
  "CUSTOMS_CLEARANCE",
  "OUT_FOR_DELIVERY",
  "DELIVERED",
  "EXCEPTION",
  "DELAY",
  "RETURNED",
];

const DEFAULT_CREATE: ShipmentCreate = {
  origin: "",
  destination: "",
  mode: "AIR",
  priority: 2,
  pieces: 1,
  declared_value: 0,
  cod_amount: 0,
  currency: "VND",
};

export default function ShipmentsPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const [carrier, setCarrier] = useState("");
  const [tracking, setTracking] = useState("");
  const [phone, setPhone] = useState("");
  const [showOverdue, setShowOverdue] = useState(false);
  const [showAttention, setShowAttention] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const shipments = useShipments({
    page,
    status: status || undefined,
    mode: mode || undefined,
    carrier: carrier || undefined,
    tracking_number: tracking || undefined,
    customer_phone: phone || undefined,
    overdue: showOverdue || undefined,
    needs_attention: showAttention || undefined,
  });
  const stats = useShipmentStats();

  const rows = shipments.data?.items ?? [];
  const totalPages = shipments.data?.pages ?? 1;
  const totalItems = shipments.data?.total ?? 0;

  function resetAndClose() {
    setShowCreate(false);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Vận chuyển</h1>
          <p className="text-sm text-slate-500">
            Quản lý lô hàng, theo dõi trạng thái và ghi nhận sự kiện quét.
          </p>
        </div>
        <Button onClick={() => setShowCreate(true)}>+ Tạo lô hàng</Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <StatCard label="Tổng lô hàng" value={stats.data ? formatNumber(stats.data.total ?? 0) : "—"} />
        <StatCard
          label="Đang vận chuyển"
          value={stats.data ? formatNumber(stats.data.in_transit ?? 0) : "—"}
          tone="info"
        />
        <StatCard
          label="Cần chú ý"
          value={stats.data ? formatNumber(stats.data.needs_attention ?? 0) : "—"}
          tone="warning"
        />
        <StatCard
          label="Quá hạn ETA"
          value={stats.data ? formatNumber(stats.data.overdue ?? 0) : "—"}
          tone="danger"
        />
        <StatCard
          label="Giao tuần này"
          value={stats.data ? formatNumber(stats.data.delivered_this_week ?? 0) : "—"}
          tone="success"
        />
        <StatCard
          label="Tổng giá trị (VND)"
          value={stats.data ? formatVnd(stats.data.total_declared_value_vnd ?? 0) : "—"}
        />
      </div>

      <Card title="Bộ lọc">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <Field label="Trạng thái">
            <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">Tất cả</option>
              {SHIPMENT_STATUSES.map((s) => (
                <option key={s} value={s}>{titleCase(s)}</option>
              ))}
            </Select>
          </Field>
          <Field label="Phương thức">
            <Select value={mode} onChange={(e) => { setMode(e.target.value); setPage(1); }}>
              <option value="">Tất cả</option>
              {SHIPMENT_MODES.map((m) => (
                <option key={m} value={m}>{titleCase(m)}</option>
              ))}
            </Select>
          </Field>
          <Field label="Hãng vận chuyển">
            <input
              className="input"
              value={carrier}
              placeholder="VD: DHL, FedEx"
              onChange={(e) => { setCarrier(e.target.value); setPage(1); }}
            />
          </Field>
          <Field label="Mã theo dõi">
            <input
              className="input"
              value={tracking}
              placeholder="Tracking / Waybill"
              onChange={(e) => { setTracking(e.target.value); setPage(1); }}
            />
          </Field>
          <Field label="SĐT khách hàng">
            <input
              className="input"
              value={phone}
              placeholder="0xxxxxxxxx"
              onChange={(e) => { setPhone(e.target.value); setPage(1); }}
            />
          </Field>
          <div className="flex flex-col justify-end gap-2">
            <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
              <input
                type="checkbox"
                checked={showOverdue}
                onChange={(e) => { setShowOverdue(e.target.checked); setPage(1); }}
              />
              Chỉ lô hàng quá hạn
            </label>
            <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
              <input
                type="checkbox"
                checked={showAttention}
                onChange={(e) => { setShowAttention(e.target.checked); setPage(1); }}
              />
              Cần chú ý (delay / exception / customs)
            </label>
          </div>
        </div>
      </Card>

      <Card>
        {shipments.isLoading ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : shipments.isError ? (
          <p className="py-10 text-center text-sm text-rose-600">Không thể tải danh sách lô hàng.</p>
        ) : (
          <>
            <Table<ShipmentSummary>
              columns={columns}
              rows={rows}
              keyOf={(s) => s.id}
              onRowClick={(s) => setSelectedId(s.id)}
              empty={<p className="py-10 text-center text-sm text-slate-400">Chưa có lô hàng nào.</p>}
            />
            <Pagination
              page={page}
              pages={totalPages}
              total={totalItems}
              onChange={setPage}
            />
          </>
        )}
      </Card>

      {selectedId && (
        <ShipmentDetailModal
          id={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
      {showCreate && (
        <CreateShipmentModal onClose={resetAndClose} />
      )}
    </div>
  );
}

const columns: Column<ShipmentSummary>[] = [
  {
    key: "reference",
    header: "Mã",
    render: (s) => <span className="font-medium text-slate-700">{s.reference}</span>,
  },
  {
    key: "status",
    header: "Trạng thái",
    render: (s) => <Badge tone={toneForStatus(s.status)}>{titleCase(s.status)}</Badge>,
  },
  { key: "route", header: "Tuyến", render: (s) => `${s.origin} → ${s.destination}` },
  {
    key: "mode",
    header: "PT",
    render: (s) => <Badge>{s.mode}</Badge>,
  },
  { key: "carrier", header: "Hãng", render: (s) => s.carrier ?? "—" },
  { key: "customer", header: "Khách", render: (s) => s.customer_name ?? "—" },
  {
    key: "eta",
    header: "ETA",
    render: (s) =>
      s.is_overdue ? (
        <span className="text-rose-600">{formatDateTime(s.eta_at)}</span>
      ) : (
        formatDateTime(s.eta_at)
      ),
  },
  { key: "created", header: "Tạo", render: (s) => formatRelative(s.created_at) },
];

function CreateShipmentModal({ onClose }: { onClose: () => void }) {
  const create = useCreateShipment();
  const [form, setForm] = useState<ShipmentCreate>(DEFAULT_CREATE);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof ShipmentCreate>(key: K, value: ShipmentCreate[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit() {
    setError(null);
    if (!form.origin.trim() || !form.destination.trim()) {
      setError("Vui lòng nhập điểm đi và điểm đến.");
      return;
    }
    try {
      await create.mutateAsync(form);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Tạo lô hàng thất bại.");
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Tạo lô hàng mới"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Hủy</Button>
          <Button onClick={submit} disabled={create.isPending}>
            {create.isPending ? "Đang tạo…" : "Tạo"}
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Điểm đi *">
          <input className="input" value={form.origin} onChange={(e) => set("origin", e.target.value)} />
        </Field>
        <Field label="Điểm đến *">
          <input className="input" value={form.destination} onChange={(e) => set("destination", e.target.value)} />
        </Field>
        <Field label="Phương thức">
          <Select value={form.mode} onChange={(e) => set("mode", e.target.value as ShipmentMode)}>
            {SHIPMENT_MODES.map((m) => (
              <option key={m} value={m}>{titleCase(m)}</option>
            ))}
          </Select>
        </Field>
        <Field label="Ưu tiên">
          <Select
            value={String(form.priority ?? 2)}
            onChange={(e) => set("priority", Number(e.target.value) as ShipmentPriority)}
          >
            <option value="1">Cao</option>
            <option value="2">Trung bình</option>
            <option value="3">Thấp</option>
          </Select>
        </Field>
        <Field label="Hãng vận chuyển">
          <input className="input" value={form.carrier ?? ""} onChange={(e) => set("carrier", e.target.value || null)} />
        </Field>
        <Field label="Số kiện">
          <input
            type="number"
            min={1}
            className="input"
            value={form.pieces ?? 1}
            onChange={(e) => set("pieces", Number(e.target.value))}
          />
        </Field>
        <Field label="Khối lượng (kg)">
          <input
            type="number"
            className="input"
            value={form.weight_kg ?? ""}
            onChange={(e) => set("weight_kg", e.target.value ? Number(e.target.value) : null)}
          />
        </Field>
        <Field label="Giá trị khai báo (VND)">
          <input
            type="number"
            className="input"
            value={form.declared_value ?? 0}
            onChange={(e) => set("declared_value", Number(e.target.value))}
          />
        </Field>
        <Field label="Tiền thu hộ COD (VND)">
          <input
            type="number"
            className="input"
            value={form.cod_amount ?? 0}
            onChange={(e) => set("cod_amount", Number(e.target.value))}
          />
        </Field>
        <Field label="Tên khách hàng">
          <input className="input" value={form.customer_name ?? ""} onChange={(e) => set("customer_name", e.target.value || null)} />
        </Field>
        <Field label="SĐT khách hàng">
          <input className="input" value={form.customer_phone ?? ""} onChange={(e) => set("customer_phone", e.target.value || null)} />
        </Field>
        <Field label="Yêu cầu khai báo hải quan">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={Boolean(form.requires_customs)}
              onChange={(e) => set("requires_customs", e.target.checked)}
            />
            Có
          </label>
        </Field>
        <Field label="Hàng nguy hiểm">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={Boolean(form.is_dangerous_goods)}
              onChange={(e) => set("is_dangerous_goods", e.target.checked)}
            />
            Có
          </label>
        </Field>
        <div className="sm:col-span-2">
          <Field label="Mô tả">
            <Textarea value={form.description ?? ""} onChange={(e) => set("description", e.target.value || null)} />
          </Field>
        </div>
      </div>
      {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
    </Modal>
  );
}

function ShipmentDetailModal({ id, onClose }: { id: string; onClose: () => void }) {
  const shipment = useShipment(id);
  const updateStatus = useUpdateShipmentStatus();
  const addEvent = useAddShipmentEvent();
  const cancel = useCancelShipment();

  const [nextStatus, setNextStatus] = useState<ShipmentStatus | "">("");
  const [eventType, setEventType] = useState(EVENT_TYPES[0]);
  const [eventNote, setEventNote] = useState("");
  const [eventLocation, setEventLocation] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);

  const data = shipment.data;

  async function applyStatus() {
    if (!nextStatus) return;
    setFeedback(null);
    try {
      await updateStatus.mutateAsync({ id, status: nextStatus });
      setNextStatus("");
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : "Cập nhật trạng thái thất bại.");
    }
  }

  async function submitEvent() {
    setFeedback(null);
    try {
      await addEvent.mutateAsync({
        id,
        payload: {
          event_type: eventType,
          note: eventNote || null,
          location: eventLocation || null,
        },
      });
      setEventNote("");
      setEventLocation("");
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : "Ghi nhận sự kiện thất bại.");
    }
  }

  async function submitCancel() {
    setFeedback(null);
    try {
      await cancel.mutateAsync({ id, reason: cancelReason || undefined });
      onClose();
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : "Hủy lô hàng thất bại.");
    }
  }

  if (shipment.isLoading) {
    return (
      <Modal open onClose={onClose} title="Chi tiết lô hàng">
        <div className="flex justify-center py-10"><Spinner /></div>
      </Modal>
    );
  }

  if (!data) {
    return (
      <Modal open onClose={onClose} title="Chi tiết lô hàng">
        <p className="py-6 text-center text-sm text-rose-600">Không tìm thấy lô hàng.</p>
      </Modal>
    );
  }

  const allowed = data.allowed_next ?? [];

  return (
    <Modal open onClose={onClose} title={`${data.reference} · ${titleCase(data.status)}`}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
          <Detail label="Trạng thái" value={<Badge tone={toneForStatus(data.status)}>{titleCase(data.status)}</Badge>} />
          <Detail label="Phương thức" value={titleCase(data.mode)} />
          <Detail label="Ưu tiên" value={String(data.priority)} />
          <Detail label="Tuyến" value={`${data.origin} → ${data.destination}`} />
          <Detail label="Hãng" value={data.carrier ?? "—"} />
          <Detail label="Mã tracking" value={data.tracking_number ?? "—"} />
          <Detail label="Số kiện" value={formatNumber(data.pieces)} />
          <Detail label="Khối lượng" value={data.weight_kg != null ? `${formatNumber(data.weight_kg, 1)} kg` : "—"} />
          <Detail label="Giá trị khai báo" value={formatVnd(data.declared_value, data.currency)} />
          <Detail label="COD" value={formatVnd(data.cod_amount, data.currency)} />
          <Detail label="Hải quan" value={data.requires_customs ? "Có" : "Không"} />
          <Detail label="Hàng nguy hiểm" value={data.is_dangerous_goods ? "Có" : "Không"} />
          <Detail label="Khách hàng" value={data.customer_name ?? "—"} />
          <Detail label="SĐT" value={data.customer_phone ?? "—"} />
          <Detail label="ETA" value={data.is_overdue ? <span className="text-rose-600">{formatDateTime(data.eta_at)}</span> : formatDateTime(data.eta_at)} />
          <Detail label="Đã giao" value={formatDateTime(data.delivered_at)} />
        </div>

        <Section title="Chuyển trạng thái">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Trạng thái tiếp theo">
              <Select value={nextStatus} onChange={(e) => setNextStatus(e.target.value as ShipmentStatus | "")}>
                <option value="">Chọn…</option>
                {allowed.map((s) => (
                  <option key={s} value={s}>{titleCase(s)}</option>
                ))}
              </Select>
            </Field>
            <Button onClick={applyStatus} disabled={!nextStatus || updateStatus.isPending}>
              {updateStatus.isPending ? "Đang cập nhật…" : "Áp dụng"}
            </Button>
          </div>
        </Section>

        <Section title="Ghi nhận sự kiện quét">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Loại sự kiện">
              <Select value={eventType} onChange={(e) => setEventType(e.target.value)}>
                {EVENT_TYPES.map((t) => (
                  <option key={t} value={t}>{titleCase(t)}</option>
                ))}
              </Select>
            </Field>
            <Field label="Địa điểm">
              <input className="input" value={eventLocation} onChange={(e) => setEventLocation(e.target.value)} />
            </Field>
            <Field label="Ghi chú">
              <input className="input" value={eventNote} onChange={(e) => setEventNote(e.target.value)} />
            </Field>
          </div>
          <div className="mt-2">
            <Button onClick={submitEvent} disabled={addEvent.isPending}>
              {addEvent.isPending ? "Đang ghi nhận…" : "Thêm sự kiện"}
            </Button>
          </div>
        </Section>

        {data.events && data.events.length > 0 && (
          <Section title={`Nhật ký sự kiện (${data.events.length})`}>
            <ul className="space-y-2">
              {[...data.events]
                .sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1))
                .map((ev) => (
                  <li key={ev.id} className="rounded border border-slate-200 p-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="info">{titleCase(ev.event_type)}</Badge>
                      <span className="text-slate-500">{formatDateTime(ev.occurred_at)}</span>
                      {ev.location && <span className="text-slate-500">· {ev.location}</span>}
                    </div>
                    {ev.note && <p className="mt-1 text-slate-600">{ev.note}</p>}
                  </li>
                ))}
            </ul>
          </Section>
        )}

        {data.status !== "CANCELLED" && data.status !== "DELIVERED" && data.status !== "RETURNED" && (
          <Section title="Hủy lô hàng">
            <Field label="Lý do hủy">
              <Textarea value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} />
            </Field>
            <div className="mt-2">
              <Button variant="danger" onClick={submitCancel} disabled={cancel.isPending}>
                {cancel.isPending ? "Đang hủy…" : "Hủy lô hàng"}
              </Button>
            </div>
          </Section>
        )}

        {feedback && <p className="text-sm text-rose-600">{feedback}</p>}
      </div>
    </Modal>
  );
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-0.5 text-slate-700">{value}</p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      {children}
    </div>
  );
}
