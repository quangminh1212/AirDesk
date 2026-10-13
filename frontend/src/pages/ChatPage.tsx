import { useEffect, useRef, useState } from "react";

import {
  Badge,
  Button,
  Card,
  Disclosure,
  JsonBlock,
  Loading,
  Spinner,
  Textarea,
  toneForSeverity,
  toneForStatus,
} from "@/components/ui";
import { errorMessage } from "@/lib/errors";
import { formatDuration, formatRelative, formatVnd } from "@/lib/format";
import { useAgentChat, useConversations } from "@/hooks/queries";
import type { AgentReply, FlightOffer } from "@/types";

interface Turn {
  role: "user" | "assistant";
  text: string;
  reply?: AgentReply;
  at: string;
}

/** The conversational agent workspace. */
export default function ChatPage() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const chat = useAgentChat();
  const conversations = useConversations(1);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, chat.isPending]);

  async function send() {
    const message = input.trim();
    if (!message || chat.isPending) return;
    setError(null);
    setInput("");
    setTurns((prev) => [
      ...prev,
      { role: "user", text: message, at: new Date().toISOString() },
    ]);

    try {
      const reply = await chat.mutateAsync({
        message,
        conversation_id: conversationId,
        channel: "web",
      });
      setConversationId(reply.conversation_id);
      setTurns((prev) => [
        ...prev,
        { role: "assistant", text: reply.reply, reply, at: new Date().toISOString() },
      ]);
    } catch (err) {
      setError(errorMessage(err));
      setTurns((prev) => [
        ...prev,
        {
          role: "assistant",
          text: "Xin lỗi, hệ thống đang gặp sự cố. Vui lòng thử lại.",
          at: new Date().toISOString(),
        },
      ]);
    }
  }

  function newConversation() {
    setConversationId(null);
    setTurns([]);
    setError(null);
    setInput("");
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_280px]">
      <div className="flex min-h-0 flex-col gap-3">
        <header className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-800">Trợ lý AI</h1>
            <p className="text-sm text-slate-500">
              {conversationId ? (
                <>
                  Hội thoại <code className="text-xs">{conversationId.slice(0, 8)}…</code>
                </>
              ) : (
                "Bắt đầu hội thoại mới"
              )}
            </p>
          </div>
          <Button variant="ghost" onClick={newConversation}>
            Hội thoại mới
          </Button>
        </header>

        <Card className="flex min-h-[26rem] flex-1 flex-col p-0">
          <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
            {turns.length === 0 && (
              <div className="py-12 text-center">
                <p className="text-sm font-medium text-slate-600">
                  Xin chào! Tôi có thể giúp gì cho bạn?
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  Ví dụ: “Tìm vé từ SGN đi Hà Nội ngày 01/12/2026 cho 1 người lớn”
                </p>
              </div>
            )}

            {turns.map((turn, i) => (
              <MessageBubble key={`${turn.at}-${i}`} turn={turn} />
            ))}

            {chat.isPending && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Spinner />
                Đang xử lý…
              </div>
            )}
          </div>

          <div className="border-t border-slate-200 p-3">
            {error && (
              <p role="alert" className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {error}
              </p>
            )}
            <div className="flex items-end gap-2">
              <Textarea
                rows={2}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder="Nhập yêu cầu… (Enter để gửi, Shift+Enter xuống dòng)"
                aria-label="Nội dung tin nhắn"
              />
              <Button onClick={() => void send()} disabled={chat.isPending || !input.trim()}>
                Gửi
              </Button>
            </div>
          </div>
        </Card>
      </div>

      <aside className="space-y-4">
        <Card title="Hội thoại gần đây">
          {conversations.isLoading ? (
            <Loading />
          ) : (conversations.data?.items ?? []).length === 0 ? (
            <p className="text-xs text-slate-400">Chưa có hội thoại.</p>
          ) : (
            <ul className="space-y-1">
              {(conversations.data?.items ?? []).slice(0, 12).map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setConversationId(c.id);
                      setTurns([]);
                      setError(null);
                    }}
                    className={`w-full rounded-lg px-2 py-1.5 text-left text-xs transition hover:bg-slate-50 ${
                      conversationId === c.id ? "bg-brand-50 ring-1 ring-brand-100" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-slate-600">
                        {c.customer_ref ?? c.channel}
                      </span>
                      <Badge tone={toneForStatus(c.status)}>{c.status}</Badge>
                    </div>
                    <span className="text-slate-400">{formatRelative(c.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Hướng dẫn">
          <ul className="space-y-1.5 text-xs text-slate-500">
            <li>• Agent chỉ báo giá từ công cụ tra cứu thật.</li>
            <li>• Câu trả lời kém tin cậy sẽ tự chuyển nhân viên.</li>
            <li>• Mọi lượt gọi công cụ đều được ghi lại để kiểm tra.</li>
          </ul>
        </Card>
      </aside>
    </div>
  );
}

function MessageBubble({ turn }: { turn: Turn }) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white">
          <p className="whitespace-pre-wrap">{turn.text}</p>
        </div>
      </div>
    );
  }

  const reply = turn.reply;
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2">
        <div className="rounded-2xl rounded-bl-sm bg-white px-4 py-2 text-sm text-slate-700 shadow-sm ring-1 ring-slate-200">
          <p className="whitespace-pre-wrap">{turn.text}</p>
        </div>

        {reply && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {reply.intent && <Badge tone="info">{reply.intent}</Badge>}
              {reply.confidence !== null && (
                <Badge tone={reply.confidence >= 0.75 ? "success" : "warning"}>
                  Tin cậy {(reply.confidence * 100).toFixed(0)}%
                </Badge>
              )}
              {reply.qa && (
                <Badge tone={toneForStatus(reply.qa.verdict)}>
                  QA {reply.qa.verdict} · {(reply.qa.score * 100).toFixed(0)}%
                </Badge>
              )}
            </div>

            {reply.escalated && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <strong>Đã chuyển cho nhân viên.</strong>{" "}
                Lý do: {reply.escalation_reason ?? "không xác định"}
              </div>
            )}

            {reply.qa && reply.qa.findings.length > 0 && (
              <div className="space-y-1">
                {reply.qa.findings.map((f) => (
                  <div
                    key={f.code}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs"
                  >
                    <Badge tone={toneForSeverity(f.severity)}>{f.severity}</Badge>{" "}
                    <span className="text-slate-600">{f.message}</span>
                  </div>
                ))}
              </div>
            )}

            {reply.offers.length > 0 && <OfferList offers={reply.offers} />}

            {reply.tool_calls.length > 0 && (
              <Disclosure summary={`Công cụ đã dùng (${reply.tool_calls.length})`}>
                <ul className="space-y-1.5">
                  {reply.tool_calls.map((t, i) => (
                    <li key={`${t.tool}-${i}`} className="text-xs">
                      <div className="flex items-center gap-2">
                        <Badge tone={t.success ? "success" : "danger"}>
                          {t.success ? "OK" : "LỖI"}
                        </Badge>
                        <code className="text-slate-700">{t.tool}</code>
                        <span className="text-slate-400">{t.duration_ms} ms</span>
                      </div>
                      {t.error && <p className="mt-0.5 text-rose-600">{t.error}</p>}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            )}

            <Disclosure summary="Chi tiết kỹ thuật">
              <JsonBlock value={reply} />
            </Disclosure>
          </div>
        )}
      </div>
    </div>
  );
}

function OfferList({ offers }: { offers: FlightOffer[] }) {
  return (
    <div className="space-y-2">
      {offers.slice(0, 6).map((o) => (
        <div
          key={`${o.provider}-${o.offer_id}`}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-700">
                {o.airline_name ?? o.primary_carrier_name ?? o.airline_code ?? o.provider}
              </p>
              <p className="text-xs text-slate-400">
                {o.origin} → {o.destination} · {o.departure_at ? formatRelative(o.departure_at) : "—"}{" "}
                · {formatDuration(o.total_duration_minutes)} ·{" "}
                {o.stops === 0 ? "Bay thẳng" : `${o.stops} điểm dừng`}
              </p>
            </div>
            <p className="whitespace-nowrap text-sm font-semibold text-brand-700">
              {formatVnd(o.total_amount, o.currency)}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}
