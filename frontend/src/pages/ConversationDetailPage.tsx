import { useParams } from "react-router-dom";

import {
  Badge,
  Card,
  Disclosure,
  ErrorState,
  JsonBlock,
  Loading,
  toneForStatus,
} from "@/components/ui";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useConversation } from "@/hooks/queries";

export default function ConversationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const query = useConversation(id ?? null);

  if (query.isLoading) return <Loading />;
  if (query.isError || !query.data) {
    return (
      <ErrorState
        message="Không tải được hội thoại này."
        onRetry={() => void query.refetch()}
      />
    );
  }

  const convo = query.data;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">
            {convo.customer_ref ?? "Khách ẩn danh"}
          </h1>
          <p className="text-xs text-slate-400">
            <code>{convo.id}</code> · {convo.channel} · tạo {formatDateTime(convo.created_at)}
          </p>
        </div>
        <Badge tone={toneForStatus(convo.status)}>{convo.status}</Badge>
      </header>

      <Card title={`Tin nhắn (${convo.messages.length})`}>
        {convo.messages.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-400">Chưa có tin nhắn.</p>
        ) : (
          <ol className="space-y-4">
            {convo.messages.map((m) => (
              <li key={m.id} className="border-l-2 border-slate-200 pl-4">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone={m.role === "user" ? "info" : "success"}>{m.role}</Badge>
                  {m.confidence !== null && m.confidence !== undefined && (
                    <span className="text-slate-400">
                      tin cậy {(m.confidence * 100).toFixed(0)}%
                    </span>
                  )}
                  {typeof m.latency_ms === "number" && m.latency_ms > 0 && (
                    <span className="text-slate-400">{m.latency_ms} ms</span>
                  )}
                  <span className="text-slate-400">{formatDateTime(m.created_at)}</span>
                </div>

                <p className="whitespace-pre-wrap text-sm text-slate-700">{m.content || "—"}</p>

                {(m.tool_calls ?? []).length > 0 && (
                  <div className="mt-2">
                    <Disclosure summary={`Công cụ (${m.tool_calls?.length ?? 0})`}>
                      <ul className="space-y-1.5">
                        {(m.tool_calls ?? []).map((t) => (
                          <li key={t.id} className="text-xs">
                            <div className="flex items-center gap-2">
                              <Badge tone={t.success ? "success" : "danger"}>
                                {t.success ? "OK" : "LỖI"}
                              </Badge>
                              <code className="text-slate-700">{t.tool}</code>
                              <span className="text-slate-400">{t.duration_ms} ms</span>
                            </div>
                            {t.error && <p className="text-rose-600">{t.error}</p>}
                            {t.arguments && (
                              <div className="mt-1">
                                <JsonBlock value={t.arguments} />
                              </div>
                            )}
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </Card>

      {convo.summary && (
        <Card title="Tóm tắt">
          <p className="text-sm text-slate-600">{convo.summary}</p>
        </Card>
      )}

      <p className="text-center text-xs text-slate-400">
        Cập nhật lần cuối {formatRelative(convo.updated_at)}
      </p>
    </div>
  );
}
