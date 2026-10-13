/** Aggregate operations dashboard: volume trends, overview KPIs, agent performance. */

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Card,
  Select,
  StatCard,
  type Tone,
} from "@/components/ui";
import { useAgentPerformance, useOverview, useVolume } from "@/hooks/queries";
import { formatNumber, formatRelative } from "@/lib/format";
import type { OverviewReport } from "@/types";

const RANGE_OPTIONS = [
  { value: 7, label: "7 ngày qua" },
  { value: 14, label: "14 ngày qua" },
  { value: 30, label: "30 ngày qua" },
  { value: 90, label: "90 ngày qua" },
];

function safe(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("vi-VN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: Tone;
}) {
  return (
    <StatCard label={label} value={value} hint={hint} tone={tone} />
  );
}

function OverviewSection({ data }: { data: OverviewReport | undefined }) {
  if (!data) return null;
  const c = data.conversations ?? {};
  const m = data.messages ?? {};
  const b = data.bookings ?? {};
  const e = data.escalations ?? {};
  const q = data.agent_quality ?? {};
  const t = data.tasks ?? {};
  const d = data.documents ?? {};

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
      <StatTile label="Hội thoại" value={formatNumber(c.total ?? 0)} hint={`${c.open ?? 0} đang mở`} />
      <StatTile
        label="Chờ nhân viên"
        value={formatNumber(c.pending_human ?? 0)}
        tone={(c.pending_human ?? 0) > 0 ? "warning" : "neutral"}
        hint="cần xử lý"
      />
      <StatTile label="Tin nhắn" value={formatNumber(m.total ?? 0)} hint={`${formatNumber(m.last_24h ?? 0)} trong 24h`} />
      <StatTile label="Đặt chỗ" value={formatNumber(b.total ?? 0)} hint={`${Object.keys(b.by_status ?? {}).length} trạng thái`} />
      <StatTile
        label="Chuyển nhân viên"
        value={formatNumber(e.total ?? 0)}
        tone={(e.open ?? 0) > 0 ? "danger" : "neutral"}
        hint={`${e.open ?? 0} đang mở`}
      />
      <StatTile label="Công việc quá hạn" value={formatNumber(t.overdue ?? 0)} tone={(t.overdue ?? 0) > 0 ? "danger" : "neutral"} hint={`${formatNumber(t.open ?? 0)} đang mở`} />
      <StatTile label="Tài liệu" value={formatNumber(d.total ?? 0)} hint={`${Object.keys(d.by_doc_type ?? {}).length} loại`} />
      <StatTile
        label="Chất lượng AI"
        value={pct(q.avg_confidence)}
        hint={`độ trễ ${safe(q.avg_latency_ms)} ms`}
      />
    </div>
  );
}

function VolumeSection() {
  const [days, setDays] = useState(30);
  const { data, isLoading, error } = useVolume(days);

  const chart = useMemo(() => {
    if (!data?.points?.length) return [];
    return data.points.map((p) => ({
      date: p.date,
      conversations: p.conversations,
      messages: p.messages,
      bookings: p.bookings,
      escalations: p.escalations,
    }));
  }, [data]);

  return (
    <Card title="Khối lượng theo thời gian">
      <div className="mb-3 flex items-center justify-end">
        <Select value={days} onChange={(ev) => setDays(Number(ev.target.value))} className="w-44">
          {RANGE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </div>
      {error ? (
        <p className="py-6 text-center text-sm text-rose-600">Không thể tải dữ liệu báo cáo.</p>
      ) : isLoading && chart.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-400">Đang tải…</p>
      ) : chart.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-400">Chưa có dữ liệu trong khoảng thời gian này.</p>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={chart} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <defs>
              <linearGradient id="g-conv" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.35} />
                <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="g-msg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="#94a3b8" />
            <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" allowDecimals={false} />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 8 }}
              labelFormatter={(v) => `Ngày ${v}`}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Area type="monotone" dataKey="messages" name="Tin nhắn" stroke="#6366f1" fill="url(#g-msg)" strokeWidth={2} />
            <Area type="monotone" dataKey="conversations" name="Hội thoại" stroke="#0ea5e9" fill="url(#g-conv)" strokeWidth={2} />
            <Area type="monotone" dataKey="bookings" name="Đặt chỗ" stroke="#10b981" fillOpacity={0} strokeWidth={2} />
            <Area type="monotone" dataKey="escalations" name="Chuyển nhân viên" stroke="#f43f5e" fillOpacity={0} strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

function AgentPerformanceSection() {
  const [days, setDays] = useState(30);
  const { data, isLoading, error } = useAgentPerformance(days);

  const chart = useMemo(() => {
    if (!data?.points?.length) return [];
    return data.points.map((p) => ({
      date: p.date,
      turns: p.turns,
      avg_confidence: (p.avg_confidence ?? 0) * 100,
      escalation_rate: (p.escalation_rate ?? 0) * 100,
      tool_failure_rate: (p.tool_failure_rate ?? 0) * 100,
      total_cost_usd: p.total_cost_usd,
    }));
  }, [data]);

  const latest = chart[chart.length - 1];

  return (
    <Card title="Hiệu suất trợ lý AI">
      <div className="mb-3 flex items-center justify-end gap-3">
        <div className="flex gap-2 text-xs text-slate-500">
          <span>Mới nhất: {latest ? `độ tự tin ${safe(latest.avg_confidence)}%` : "—"}</span>
          <span>· tỷ lệ chuyển {latest ? `${safe(latest.escalation_rate)}%` : "—"}</span>
          <span>· chi phí ${safe(latest?.total_cost_usd ?? 0, 2)}</span>
        </div>
        <Select value={days} onChange={(ev) => setDays(Number(ev.target.value))} className="w-44">
          {RANGE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </div>
      {error ? (
        <p className="py-6 text-center text-sm text-rose-600">Không thể tải dữ liệu hiệu suất.</p>
      ) : isLoading && chart.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-400">Đang tải…</p>
      ) : chart.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-400">Chưa có dữ liệu hiệu suất.</p>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chart} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="#94a3b8" />
            <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" unit="%" />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 8 }}
              labelFormatter={(v) => `Ngày ${v}`}
              formatter={(value: number, name: string) => [`${safe(value)}%`, name]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="avg_confidence" name="Độ tự tin" stroke="#0ea5e9" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="escalation_rate" name="Tỷ lệ chuyển" stroke="#f43f5e" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="tool_failure_rate" name="Lỗi công cụ" stroke="#f59e0b" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

export default function ReportsPage() {
  const overview = useOverview();

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Báo cáo tổng hợp</h1>
          <p className="text-sm text-slate-500">
            Tổng quan vận hành và hiệu suất trợ lý AI
            {overview.dataUpdatedAt
              ? ` · cập nhật ${formatRelative(new Date(overview.dataUpdatedAt).toISOString())}`
              : ""}
          </p>
        </div>
      </header>

      <OverviewSection data={overview.data} />
      <VolumeSection />
      <AgentPerformanceSection />
    </div>
  );
}
