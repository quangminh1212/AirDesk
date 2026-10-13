import { useRef, useState } from "react";

import {
  Badge,
  Button,
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
import { errorMessage } from "@/lib/errors";
import { formatBytes, formatDateTime, formatPercent } from "@/lib/format";
import { useAskDocument, useDocuments, useUploadDocument } from "@/hooks/queries";
import type { DocumentRecord } from "@/types";

export default function DocumentsPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<DocumentRecord | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const query = useDocuments(page, status || undefined);
  const upload = useUploadDocument();
  const ask = useAskDocument();

  async function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setUploadError(null);
    try {
      const doc = await upload.mutateAsync(file);
      setSelected(doc);
    } catch (err) {
      setUploadError(errorMessage(err));
    }
  }

  async function submitQuestion() {
    if (!selected || question.trim().length < 2) return;
    setAnswer(null);
    try {
      const res = await ask.mutateAsync({ id: selected.id, question: question.trim() });
      setAnswer(res.answer);
    } catch (err) {
      setAnswer(`Lá»—i: ${errorMessage(err)}`);
    }
  }

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return <ErrorState message="KhÃ´ng táº£i Ä‘Æ°á»£c tÃ i liá»‡u." onRetry={() => void query.refetch()} />;
  }

  const rows = query.data?.items ?? [];

  const columns: Column<DocumentRecord>[] = [
    {
      key: "file",
      header: "Tá»‡p",
      render: (d) => (
        <div>
          <p className="font-medium text-slate-700">{d.filename}</p>
          <p className="text-xs text-slate-400">
            {formatBytes(d.size_bytes)} Â· <code>{d.sha256.slice(0, 10)}â€¦</code>
          </p>
        </div>
      ),
    },
    { key: "type", header: "Loáº¡i", render: (d) => <Badge tone="info">{d.doc_type}</Badge> },
    {
      key: "status",
      header: "Tráº¡ng thÃ¡i",
      render: (d) => (
        <div className="space-y-1">
          <Badge tone={toneForStatus(d.status)}>{d.status}</Badge>
          {d.needs_ocr && <Badge tone="warning">Cáº§n OCR</Badge>}
        </div>
      ),
    },
    {
      key: "confidence",
      header: "Äá»™ tin cáº­y",
      render: (d) => <span className="text-xs text-slate-500">{formatPercent(d.confidence)}</span>,
    },
    {
      key: "created",
      header: "Táº£i lÃªn",
      render: (d) => (
        <span className="text-xs text-slate-500">{formatDateTime(d.created_at)}</span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">TÃ i liá»‡u</h1>
          <p className="text-sm text-slate-500">
            Táº£i vÃ©, hoÃ¡ Ä‘Æ¡n, há»™ chiáº¿uâ€¦ Ä‘á»ƒ tá»± Ä‘á»™ng trÃ­ch xuáº¥t dá»¯ liá»‡u vÃ  há»i Ä‘Ã¡p.
          </p>
        </div>
        <select
          aria-label="Lá»c theo tráº¡ng thÃ¡i"
          className="input w-44"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Má»i tráº¡ng thÃ¡i</option>
          <option value="uploaded">ÄÃ£ táº£i lÃªn</option>
          <option value="extracting">Äang trÃ­ch xuáº¥t</option>
          <option value="extracted">ÄÃ£ trÃ­ch xuáº¥t</option>
          <option value="failed">Lá»—i</option>
        </select>
      </header>

      <Card>
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void handleFiles(e.dataTransfer.files);
          }}
          className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition ${
            dragging ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-slate-50"
          }`}
        >
          <p className="text-sm font-medium text-slate-600">
            KÃ©o tháº£ tá»‡p vÃ o Ä‘Ã¢y, hoáº·c chá»n tá»‡p
          </p>
          <p className="text-xs text-slate-400">
            Há»— trá»£ PDF, DOCX, TXT, CSV, JSON â€” tá»‘i Ä‘a 10 MB
          </p>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={(e) => void handleFiles(e.target.files)}
          />
          <Button variant="ghost" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? "Äang táº£i lÃªnâ€¦" : "Chá»n tá»‡p"}
          </Button>
          {uploadError && (
            <p role="alert" className="text-xs text-rose-600">
              {uploadError}
            </p>
          )}
        </div>
      </Card>

      <Card title={`Danh sÃ¡ch tÃ i liá»‡u`}>
        <Table
          columns={columns}
          rows={rows}
          keyOf={(d) => d.id}
          onRowClick={(d) => {
            setSelected(d);
            setAnswer(null);
            setQuestion("");
          }}
          empty={<EmptyState title="ChÆ°a cÃ³ tÃ i liá»‡u nÃ o" hint="Táº£i lÃªn tá»‡p Ä‘áº§u tiÃªn á»Ÿ trÃªn." />}
        />
        <Pagination
          page={query.data?.page ?? 1}
          pages={query.data?.pages ?? 1}
          total={query.data?.total}
          onChange={setPage}
        />
      </Card>

      <Modal
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.filename ?? "TÃ i liá»‡u"}
      >
        {selected && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge tone={toneForStatus(selected.status)}>{selected.status}</Badge>
              <Badge tone="info">{selected.doc_type}</Badge>
              <Badge tone="neutral">{formatBytes(selected.size_bytes)}</Badge>
              <Badge tone="neutral">Tin cáº­y {formatPercent(selected.confidence)}</Badge>
            </div>

            {selected.error && (
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {selected.error}
              </p>
            )}

            {selected.needs_ocr && (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Tá»‡p khÃ´ng cÃ³ lá»›p vÄƒn báº£n â€” cáº§n OCR thá»§ cÃ´ng.
              </p>
            )}

            {selected.extracted && (
              <div>
                <h3 className="mb-1 text-xs font-medium text-slate-500">Dá»¯ liá»‡u trÃ­ch xuáº¥t</h3>
                <JsonBlock value={selected.extracted} />
              </div>
            )}

            <div>
              <h3 className="mb-1 text-xs font-medium text-slate-500">Há»i vá» tÃ i liá»‡u</h3>
              <div className="flex gap-2">
                <input
                  className="input"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitQuestion();
                  }}
                  placeholder="Sá»‘ hiá»‡u vÃ© trÃªn tÃ i liá»‡u nÃ y lÃ  gÃ¬?"
                  aria-label="CÃ¢u há»i vá» tÃ i liá»‡u"
                />
                <Button onClick={() => void submitQuestion()} disabled={ask.isPending}>
                  {ask.isPending ? "â€¦" : "Há»i"}
                </Button>
              </div>
              {answer && (
                <p className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  {answer}
                </p>
              )}
            </div>

            {selected.text_content && (
              <details className="rounded-lg border border-slate-200">
                <summary className="cursor-pointer-pointer px-3 py-2 text-xs font-medium text-slate-600">
                  VÄƒn báº£n trÃ­ch xuáº¥t ({selected.text_content.length} kÃ½ tá»±)
                </summary>
                <pre className="max-h-60 overflow-auto border-t border-slate-200 p-3 text-xs text-slate-600">
                  {selected.text_content.slice(0, 20000)}
                </pre>
              </details>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
