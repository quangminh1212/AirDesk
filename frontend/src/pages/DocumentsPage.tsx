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
      setAnswer(`Lỗi: ${errorMessage(err)}`);
    }
  }

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return <ErrorState message="Không tải được tài liệu." onRetry={() => void query.refetch()} />;
  }

  const rows = query.data?.items ?? [];

  const columns: Column<DocumentRecord>[] = [
    {
      key: "file",
      header: "Tệp",
      render: (d) => (
        <div>
          <p className="font-medium text-slate-700">{d.filename}</p>
          <p className="text-xs text-slate-400">
            {formatBytes(d.size_bytes)} · <code>{d.sha256.slice(0, 10)}…</code>
          </p>
        </div>
      ),
    },
    { key: "type", header: "Loại", render: (d) => <Badge tone="info">{d.doc_type}</Badge> },
    {
      key: "status",
      header: "Trạng thái",
      render: (d) => (
        <div className="space-y-1">
          <Badge tone={toneForStatus(d.status)}>{d.status}</Badge>
          {d.needs_ocr && <Badge tone="warning">Cần OCR</Badge>}
        </div>
      ),
    },
    {
      key: "confidence",
      header: "Độ tin cậy",
      render: (d) => <span className="text-xs text-slate-500">{formatPercent(d.confidence)}</span>,
    },
    {
      key: "created",
      header: "Tải lên",
      render: (d) => (
        <span className="text-xs text-slate-500">{formatDateTime(d.created_at)}</span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Tài liệu</h1>
          <p className="text-sm text-slate-500">
            Tải vé, hoá đơn, hộ chiếu… để tự động trích xuất dữ liệu và hỏi đáp.
          </p>
        </div>
        <select
          aria-label="Lọc theo trạng thái"
          className="input w-44"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Mọi trạng thái</option>
          <option value="uploaded">Đã tải lên</option>
          <option value="extracting">Đang trích xuất</option>
          <option value="extracted">Đã trích xuất</option>
          <option value="failed">Lỗi</option>
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
            Kéo thả tệp vào đây, hoặc chọn tệp
          </p>
          <p className="text-xs text-slate-400">
            Hỗ trợ PDF, DOCX, TXT, CSV, JSON — tối đa 10 MB
          </p>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={(e) => void handleFiles(e.target.files)}
          />
          <Button variant="ghost" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? "Đang tải lên…" : "Chọn tệp"}
          </Button>
          {uploadError && (
            <p role="alert" className="text-xs text-rose-600">
              {uploadError}
            </p>
          )}
        </div>
      </Card>

      <Card title={`Danh sách tài liệu`}>
        <Table
          columns={columns}
          rows={rows}
          keyOf={(d) => d.id}
          onRowClick={(d) => {
            setSelected(d);
            setAnswer(null);
            setQuestion("");
          }}
          empty={<EmptyState title="Chưa có tài liệu nào" hint="Tải lên tệp đầu tiên ở trên." />}
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
        title={selected?.filename ?? "Tài liệu"}
      >
        {selected && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge tone={toneForStatus(selected.status)}>{selected.status}</Badge>
              <Badge tone="info">{selected.doc_type}</Badge>
              <Badge tone="neutral">{formatBytes(selected.size_bytes)}</Badge>
              <Badge tone="neutral">Tin cậy {formatPercent(selected.confidence)}</Badge>
            </div>

            {selected.error && (
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {selected.error}
              </p>
            )}

            {selected.needs_ocr && (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Tệp không có lớp văn bản — cần OCR thủ công.
              </p>
            )}

            {selected.extracted && (
              <div>
                <h3 className="mb-1 text-xs font-medium text-slate-500">Dữ liệu trích xuất</h3>
                <JsonBlock value={selected.extracted} />
              </div>
            )}

            <div>
              <h3 className="mb-1 text-xs font-medium text-slate-500">Hỏi về tài liệu</h3>
              <div className="flex gap-2">
                <input
                  className="input"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitQuestion();
                  }}
                  placeholder="Số hiệu vé trên tài liệu này là gì?"
                  aria-label="Câu hỏi về tài liệu"
                />
                <Button onClick={() => void submitQuestion()} disabled={ask.isPending}>
                  {ask.isPending ? "…" : "Hỏi"}
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
                <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-600">
                  Văn bản trích xuất ({selected.text_content.length} ký tự)
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
