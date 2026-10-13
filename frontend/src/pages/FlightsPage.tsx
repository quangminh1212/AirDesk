/** Airline-ticket agency: search flights, compare offers, issue bookings. */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  Badge,
  Button,
  Card,
  ErrorState,
  Field,
  Loading,
  Modal,
  Spinner,
} from "@/components/ui";
import { bookingApi, flightApi } from "@/api";
import { formatDateTime, formatDuration, formatVnd } from "@/lib/format";
import type {
  BookingCreateRequest,
  FlightOffer,
  FlightSearchRequest,
  PassengerInput,
  Segment,
} from "@/types";

const CABINS = ["economy", "premium_economy", "business", "first"];
const PROVIDERS = ["auto", "mock", "amadeus", "duffel"];

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function tomorrowISO(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

const EMPTY_SEARCH: FlightSearchRequest = {
  origin: "SGN",
  destination: "HAN",
  departure_date: tomorrowISO(),
  return_date: null,
  adults: 1,
  children: 0,
  infants: 0,
  cabin: "economy",
  currency: "VND",
  non_stop: false,
  max_results: 20,
  provider: "auto",
};

function OfferCard({
  offer,
  onSelect,
  pending,
}: {
  offer: FlightOffer;
  onSelect: (o: FlightOffer) => void;
  pending: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-800">
            {offer.origin} → {offer.destination}
          </p>
          <p className="text-xs text-slate-500">
            {offer.primary_carrier_name ?? offer.airline_name ?? offer.provider}
            {offer.segments && offer.segments[0]?.flight_number
              ? ` · ${offer.segments[0].flight_number}`
              : ""}
          </p>
        </div>
        <div className="text-right">
          <p className="text-lg font-semibold text-slate-800">
            {formatVnd(offer.total_amount, offer.currency)}
          </p>
          {offer.refundable !== null && offer.refundable !== undefined && (
            <p className="text-xs text-slate-400">{offer.refundable ? "Hoàn tiền" : "Không hoàn"}</p>
          )}
        </div>
      </div>

      <div className="mt-3 space-y-1 text-sm text-slate-600">
        {offer.segments && offer.segments.length > 0 ? (
          offer.segments.map((s: Segment, i) => (
            <div key={i} className="flex items-center gap-2 text-xs text-slate-500">
              <span className="font-medium text-slate-700">{s.flight_number}</span>
              <span>
                {s.origin} {formatDateTime(s.departure_at)} → {s.destination}{" "}
                {formatDateTime(s.arrival_at)}
              </span>
              <span>{formatDuration(s.duration_minutes)}</span>
            </div>
          ))
        ) : (
          <p className="text-xs text-slate-400">
            {offer.departure_at ? formatDateTime(offer.departure_at) : "—"}
            {offer.stops > 0 ? ` · ${offer.stops} điểm dừng` : " · Bay thẳng"}
            {` · ${formatDuration(offer.total_duration_minutes)}`}
          </p>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex flex-wrap gap-1">
          {offer.stops === 0 && <Badge tone="success">Bay thẳng</Badge>}
          {offer.baggage_allowance && <Badge tone="neutral">{offer.baggage_allowance}</Badge>}
          {offer.seats_available !== null && offer.seats_available !== undefined && (
            <Badge tone={offer.seats_available <= 3 ? "warning" : "neutral"}>
              {offer.seats_available} chỗ
            </Badge>
          )}
          {offer.expires_at && (
            <Badge tone="warning">Hết hạn {formatDateTime(offer.expires_at)}</Badge>
          )}
        </div>
        <Button variant="primary" disabled={pending} onClick={() => onSelect(offer)}>
          {pending ? <Spinner /> : "Đặt vé"}
        </Button>
      </div>
    </div>
  );
}

function BookingModal({
  offer,
  cabin,
  onClose,
}: {
  offer: FlightOffer;
  cabin: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BookingCreateRequest>({
    offer_id: offer.offer_id,
    provider: offer.provider,
    passengers: [
      {
        passenger_type: "adult",
        first_name: "",
        last_name: "",
        email: null,
        phone: null,
        document_type: null,
        document_number: null,
        nationality: null,
      },
    ],
    contact_email: "",
    contact_phone: null,
    cabin,
  });
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => bookingApi.create(form),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["bookings"] });
      onClose();
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : "Đặt vé thất bại.");
    },
  });

  function updatePassenger(i: number, patch: Partial<PassengerInput>) {
    setForm((f) => {
      const passengers = f.passengers.map((p, idx) => (idx === i ? { ...p, ...patch } : p));
      return { ...f, passengers };
    });
  }

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={`Đặt vé · ${offer.origin} → ${offer.destination}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Hủy
          </Button>
          <Button variant="primary" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <Spinner /> : "Xác nhận đặt vé"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
          <p>
            Tổng tiền:{" "}
            <span className="font-semibold text-slate-800">
              {formatVnd(offer.total_amount, offer.currency)}
            </span>
          </p>
          <p className="text-xs text-slate-400">
            {offer.primary_carrier_name ?? offer.provider} · {offer.stops === 0 ? "Bay thẳng" : `${offer.stops} điểm dừng`}
          </p>
        </div>

        <Field label="Email liên hệ">
          <input
            className="input"
            type="email"
            value={form.contact_email}
            onChange={(e) => setForm((f) => ({ ...f, contact_email: e.target.value }))}
            placeholder="khach@email.com"
          />
        </Field>
        <Field label="Điện thoại liên hệ">
          <input
            className="input"
            value={form.contact_phone ?? ""}
            onChange={(e) => setForm((f) => ({ ...f, contact_phone: e.target.value || null }))}
            placeholder="090xxxxxxx"
          />
        </Field>

        {form.passengers.map((p, i) => (
          <div key={i} className="space-y-2 rounded-lg border border-slate-200 p-3">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Hành khách {i + 1}
            </p>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Họ">
                <input
                  className="input"
                  value={p.last_name}
                  onChange={(e) => updatePassenger(i, { last_name: e.target.value })}
                />
              </Field>
              <Field label="Tên">
                <input
                  className="input"
                  value={p.first_name}
                  onChange={(e) => updatePassenger(i, { first_name: e.target.value })}
                />
              </Field>
              <Field label="Số hộ chiếu/CMND">
                <input
                  className="input"
                  value={p.document_number ?? ""}
                  onChange={(e) => updatePassenger(i, { document_number: e.target.value || null })}
                />
              </Field>
              <Field label="Quốc tịch">
                <input
                  className="input"
                  value={p.nationality ?? ""}
                  onChange={(e) => updatePassenger(i, { nationality: e.target.value || null })}
                />
              </Field>
            </div>
          </div>
        ))}

        {error && <p className="text-sm text-rose-600">{error}</p>}
      </div>
    </Modal>
  );
}

export default function FlightsPage() {
  const [search, setSearch] = useState<FlightSearchRequest>(EMPTY_SEARCH);
  const [selected, setSelected] = useState<FlightOffer | null>(null);
  const [pricingId, setPricingId] = useState<string | null>(null);
  const [priceError, setPriceError] = useState<string | null>(null);
  const [enabled, setEnabled] = useState(false);

  const query = useQuery({
    queryKey: ["flight-search", search],
    queryFn: () => flightApi.search(search),
    enabled,
  });

  const offers = useMemo(() => query.data ?? [], [query.data]);

  /**
   * NDC OfferPrice. Offers from /search are `shopped` (reference only) and the
   * backend refuses to book them. Price first so the user sees confirmed
   * availability and final taxes before entering passenger details.
   */
  async function selectOffer(offer: FlightOffer) {
    setPriceError(null);
    setPricingId(offer.offer_id);
    try {
      const priced = await flightApi.price(offer.provider, offer.offer_id);
      if (!priced.is_bookable) {
        setPriceError("Chuyến bay này hiện không còn khả dụng để đặt. Vui lòng chọn chuyến khác.");
        return;
      }
      setSelected(priced);
    } catch {
      setPriceError("Không thể xác nhận giá chuyến bay. Chuyến có thể đã hết chỗ.");
    } finally {
      setPricingId(null);
    }
  }

  function runSearch() {
    setEnabled(true);
    void query.refetch();
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-xl font-semibold text-slate-800">Đại lý vé máy bay</h1>
        <p className="text-sm text-slate-500">Tìm chuyến bay, so sánh giá và phát hành vé.</p>
      </header>

      <Card title="Tìm chuyến bay">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Đi từ (IATA)">
            <input
              className="input uppercase"
              maxLength={3}
              value={search.origin}
              onChange={(e) => setSearch((s) => ({ ...s, origin: e.target.value.toUpperCase() }))}
            />
          </Field>
          <Field label="Đến (IATA)">
            <input
              className="input uppercase"
              maxLength={3}
              value={search.destination}
              onChange={(e) => setSearch((s) => ({ ...s, destination: e.target.value.toUpperCase() }))}
            />
          </Field>
          <Field label="Ngày đi">
            <input
              className="input"
              type="date"
              min={todayISO()}
              value={search.departure_date}
              onChange={(e) => setSearch((s) => ({ ...s, departure_date: e.target.value }))}
            />
          </Field>
          <Field label="Ngày về (tùy chọn)">
            <input
              className="input"
              type="date"
              min={search.departure_date}
              value={search.return_date ?? ""}
              onChange={(e) => setSearch((s) => ({ ...s, return_date: e.target.value || null }))}
            />
          </Field>
          <Field label="Người lớn">
            <input
              className="input"
              type="number"
              min={1}
              max={9}
              value={search.adults}
              onChange={(e) => setSearch((s) => ({ ...s, adults: Number(e.target.value) }))}
            />
          </Field>
          <Field label="Trẻ em">
            <input
              className="input"
              type="number"
              min={0}
              max={8}
              value={search.children}
              onChange={(e) => setSearch((s) => ({ ...s, children: Number(e.target.value) }))}
            />
          </Field>
          <Field label="Hạng ghế">
            <select
              className="input"
              value={search.cabin}
              onChange={(e) => setSearch((s) => ({ ...s, cabin: e.target.value }))}
            >
              {CABINS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Nguồn">
            <select
              className="input"
              value={search.provider}
              onChange={(e) => setSearch((s) => ({ ...s, provider: e.target.value }))}
            >
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={search.non_stop}
              onChange={(e) => setSearch((s) => ({ ...s, non_stop: e.target.checked }))}
            />
            Chỉ bay thẳng
          </label>
          <Button variant="primary" onClick={runSearch} disabled={query.isFetching}>
            {query.isFetching ? <Spinner /> : "Tìm kiếm"}
          </Button>
        </div>
      </Card>

      {priceError && (
        <Card>
          <p className="text-sm text-rose-600">{priceError}</p>
        </Card>
      )}

      {query.isFetching && !query.data ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState message="Tìm kiếm thất bại. Vui lòng thử lại." onRetry={runSearch} />
      ) : enabled && offers.length === 0 ? (
        <Card>
          <p className="py-8 text-center text-sm text-slate-400">
            Không tìm thấy chuyến bay phù hợp. Thử thay đổi ngày hoặc nguồn.
          </p>
        </Card>
      ) : offers.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-2">
          {offers.map((o) => (
            <OfferCard
              key={`${o.provider}:${o.offer_id}`}
              offer={o}
              onSelect={(o) => void selectOffer(o)}
              pending={pricingId === o.offer_id}
            />
          ))}
        </div>
      ) : null}

      {selected && (
        <BookingModal
          offer={selected}
          cabin={search.cabin}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
