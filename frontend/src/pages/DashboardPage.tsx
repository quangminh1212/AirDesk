import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, EmptyState, ErrorState, Loading, StatCard, toneForStatus, Badge } from "@/components/ui";
import { formatNumber, formatPercent, formatVnd } from "@/lib/format";
import { useOverview, useVolume } from "@/hooks/queries";

/** Dashboard: operational and agent-health KPIs plus a 30-day volume chart. */
export default function DashboardPage() {
  const overview = useOverview();
  const volume = useVolume(30);

  if (overview.isLoading) return <Loading />;
  if (overview.isError) {
    return (
      <ErrorState
        message="Không tải được dữ liệu tổng quan."
        onRetry={() => void overview.refetch()}
      />
    );
  }

  const data = overview.data ?? {};
  const convos = data.conversations ?? {};
  const bookings = data.bookings ?? {};
  const escalations = data.escalations ?? {};
  const quality = data.agent_quality ?? {};
  const tasks = data.tasks ?? {};
  const documents = data.documents ?? {};

  const revenue = bookings.revenue_by_currency ?? {};
  const revenueText =
    Object.keys(revenue).length > 0
      ? Object.entries(revenue)
          .map(([cur, amount]) => formatVnd(Number(amount), cur))
          .join(" · ")
      : "—";

  const points = volume.data?.points ?? [];

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-800">Tổng quan vận hành</h1>
        <p className="text-sm text-slate-500">
          Sức khoẻ hệ thống agent, hàng đợi xử lý và chất lượng trả lời.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Hội thoại đang mở"
          value={formatNumber(convos.open ?? 0)}
          hint={`${formatNumber(convos.total ?? 0)} tổng cộng`}
        />
        <StatCard
          label="Chờ nhân viên"
          value={formatNumber(convos.pending_human ?? 0)}
          tone={(convos.pending_human ?? 0) > 0 ? "warning" : "neutral"}
          hint={`${formatNumber(escalations.open ?? 0)} chuyển việc đang mở`}
        />
        <StatCard
          label="Đặt chỗ"
          value={formatNumber(bookings.total ?? 0)}
          hint={revenueText}
        />
        <StatCard
          label="Tỷ lệ đạt QA"
          value={formatPercent(quality.tool_success_rate ?? null)}
          hint="Tỷ lệ gọi công cụ thành công"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Công việc quá hạn"
          value={formatNumber(tasks.overdue ?? 0)}
          tone={(tasks.overdue ?? 0) > 0 ? "danger" : "neutral"}
          hint={`${formatNumber(tasks.open ?? 0)} đang mở`}
        />
        <StatCard
          label="Độ tin cậy TB"
          value={formatPercent(quality.avg_confidence ?? null)}
          hint="Điểm tự đánh giá của agent"
        />
        <StatCard
          label="Độ trễ TB"
          value={`${formatNumber(quality.avg_latency_ms ?? 0)} ms`}
          hint={`Chi phí: $${Number(quality.total_cost_usd ?? 0).toFixed(4)}`}
        />
        <StatCard
          label="Tài liệu"
          value={formatNumber(documents.total ?? 0)}
          hint={`${formatNumber(data.messages?.last_7d ?? 0)} tin nhắn 7 ngày`}
        />
      </div>

      <Card title="Lưu lượng 30 ngày">
        {volume.isLoading ? (
          <Loading />
        ) : points.length === 0 ? (
          <EmptyState
            title="Chưa có dữ liệu lưu lượng"
            hint="Biểu đồ sẽ hiển thị sau khi có hội thoại và tin nhắn."
          />
        ) : (
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                <defs>
                  <linearGradient id="convGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2f7fe0" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#2f7fe0" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#94a3b8" }} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: "#e2e8f0" }}
                  labelStyle={{ color: "#475569" }}
                />
                <Area
                  type="monotone"
                  dataKey="messages"
                  name="Tin nhắn"
                  stroke="#2f7fe0"
                  strokeWidth={2}
                  fill="url(#convGrad)"
                />
                <Area
                  type="monotone"
                  dataKey="conversations"
                  name="Hội thoại"
                  stroke="#10b981"
                  strokeWidth={2}
                  fillOpacity={0}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Chuyển việc theo ngày">
          {points.length === 0 ? (
            <EmptyState title="Chưa có dữ liệu" />
          ) : (
            <div className="h-56 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={points} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#94a3b8" }} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Bar dataKey="escalations" name="Chuyển việc" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="bookings" name="Đặt chỗ" fill="#1f66bd" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Phân loại trạng thái đặt chỗ">
          {Object.keys(bookings.by_status ?? {}).length === 0 ? (
            <EmptyState title="Chưa có đặt chỗ" />
          ) : (
            <ul className="space-y-2">
              {Object.entries(bookings.by_status ?? {}).map(([status, count]) => (
                <li key={status} className="flex items-center justify-between text-sm">
                  <Badge tone={toneForStatus(status)}>{status}</Badge>
                  <span className="font-medium text-slate-700">{formatNumber(Number(count))}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
