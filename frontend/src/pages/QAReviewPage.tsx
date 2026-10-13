import { useState } from "react";
import { Link } from "react-router-dom";

import {
  Badge,
  Card,
  Disclosure,
  EmptyState,
  ErrorState,
  Loading,
  Pagination,
  Select,
  StatCard,
  Table,
  toneForSeverity,
  toneForStatus,
  type Column,
} from "@/components/ui";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import { useQAOverview, useQATurns } from "@/hooks/queries";
import type { QATurn } from "@/types";

/** Rolling window (days) used for both the QA summary and the turn list. */
const QA_WINDOW_DAYS = 7;

export default function QAReviewPage() {
  const [page, setPage] = useState(1);
  const [verdict, setVerdict] = useState("");
  const days = QA_WINDOW_DAYS;

  const overview = useQAOverview(days);
  const turns = useQATurns(page, verdict || undefined, days);

  if (turns.isLoading) return <Loading />;
  if (turns.isError) {
    return (
      <ErrorState
        message="Không tải được dữ liệu kiểm tra chất lượng."
        onRetry={() => void turns.refetch()}
      />
    );
  }

  const rows = turns.data?.items ?? [];
  const stats = overview.data;

  const columns: Column<QATurn>[] = [
    {
      key: "verdict",
      header: "Kết luận",
      render: (t) => (
        <div className="space-y-1">
          <Badge tone={toneForStatus(t.verdict)}>{t.verdict}</Badge>
          <p className="text-xs text-slate-400">{(t.score * 100).toFixed(0)}%</p>
        </div>
      ),
    },
    {
      key: "preview",
      header: "Nội dung",
      render: (t) => (
        <div className="max-w-md">
          <p className="line-clamp-2 text-xs text-slate-600">{t.content_preview || "—"}</p>
          <Link
            to={`/conversations/${t.conversation_id}`}
            className="text-xs text-brand-600 underline"
          >
            Xem hội thoại
          </Link>
        </div>
      ),
    },
    {
      key: "findings",
      header: "Phát hiện",
      render: (t) =>
        t.findings.length === 0 ? (
          <span className="text-xs text-slate-400">Không có</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {t.findings.map((f) => (
              <Badge key={f.code} tone={toneForSeverity(f.severity)}>
                {f.code}
              </Badge>
            ))}
          </div>
        ),
    },
    {
      key: "metrics",
      header: "Chỉ số",
      render: (t) => (
        <div className="text-xs text-slate-500">
          <p>{t.confidence !== null ? `Tin cậy ${(t.confidence * 100).toFixed(0)}%` : "—"}</p>
          <p>{formatNumber(t.latency_ms)} ms</p>
        </div>
      ),
    },
    {
      key: "created",
      header: "Thời gian",
      render: (t) => (
        <span className="text-xs text-slate-500">{formatDateTime(t.created_at)}</span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Kiểm tra chất lượng</h1>
          <p className="text-sm text-slate-500">
            Mỗi câu trả lời của agent được chấm tự động; lượt đạt lỗi sẽ tự chuyển nhân viên.
          </p>
        </div>
        <div className="w-44">
          <Select
            aria-label="Lọc theo kết luận"
            value={verdict}
            onChange={(e) => {
              setVerdict(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Tất cả kết luận</option>
            <option value="pass">Đạt</option>
            <option value="review">Cần xem lại</option>
            <option value="fail">Không đạt</option>
          </Select>
        </div>
      </header>

      {stats && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Tỷ lệ đạt"
              value={formatPercent(stats.pass_rate)}
              hint={`${formatNumber(stats.total_turns)} lượt trong ${stats.window_days} ngày`}
            />
            <StatCard label="Điểm TB" value={formatPercent(stats.avg_score)} />
            <StatCard
              label="Không đạt"
              value={formatNumber(stats.failed)}
              tone={stats.failed > 0 ? "danger" : "neutral"}
            />
            <StatCard
              label="Độ tin cậy TB"
              value={formatPercent(stats.avg_confidence)}
              hint={`${formatNumber(stats.review)} lượt cần xem lại`}
            />
          </div>

          {Object.keys(stats.top_findings ?? {}).length > 0 && (
            <Card title="Lỗi thường gặp">
              <div className="flex flex-wrap gap-2">
                {Object.entries(stats.top_findings).map(([code, count]) => (
                  <Badge key={code} tone="warning">
                    {code}: {count}
                  </Badge>
                ))}
              </div>
            </Card>
          )}
        </>
      )}

      <Card title="Lượt trả lời đã chấm">
        <Table
          columns={columns}
          rows={rows}
          keyOf={(t) => t.message_id}
          empty={<EmptyState title="Chưa có lượt nào được chấm" />}
        />
        <Pagination
          page={turns.data?.page ?? 1}
          pages={turns.data?.pages ?? 1}
          total={turns.data?.total}
          onChange={setPage}
        />

        {rows.length > 0 && (
          <div className="mt-4 space-y-2">
            <h3 className="text-xs font-medium text-slate-500">Chi tiết kỹ thuật</h3>
            {rows.slice(0, 5).map((t) => (
              <Disclosure key={t.message_id} summary={`${t.verdict} · ${t.message_id.slice(0, 8)}…`}>
                <ul className="space-y-1 text-xs">
                  {t.findings.map((f) => (
                    <li key={f.code}>
                      <Badge tone={toneForSeverity(f.severity)}>{f.severity}</Badge>{" "}
                      <code className="text-slate-700">{f.code}</code> — {f.message}
                    </li>
                  ))}
                  {t.findings.length === 0 && <li className="text-slate-400">Không có phát hiện.</li>}
                </ul>
              </Disclosure>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
