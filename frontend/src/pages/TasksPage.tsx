import { useMemo, useState } from "react";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Loading,
  Modal,
  Select,
  Textarea,
  priorityLabel,
  toneForPriority,
} from "@/components/ui";
import { errorMessage } from "@/lib/errors";
import { formatDate, isOverdue } from "@/lib/format";
import { useCreateTask, useTasks, useUpdateTask } from "@/hooks/queries";
import type { Task, TaskStatus } from "@/types";

const COLUMNS: { status: TaskStatus; label: string }[] = [
  { status: "todo", label: "Cáº§n lÃ m" },
  { status: "in_progress", label: "Äang lÃ m" },
  { status: "blocked", label: "Äang chá»" },
  { status: "done", label: "HoÃ n thÃ nh" },
];

export default function TasksPage() {
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState(2);
  const [taskType, setTaskType] = useState("other");
  const [dueAt, setDueAt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const query = useTasks({ overdue: overdueOnly });
  const create = useCreateTask();
  const update = useUpdateTask();

  const grouped = useMemo(() => {
    const map: Record<string, Task[]> = {};
    for (const col of COLUMNS) map[col.status] = [];
    for (const task of query.data?.items ?? []) {
      (map[task.status] ??= []).push(task);
    }
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => a.priority - b.priority);
    }
    return map;
  }, [query.data]);

  async function submit() {
    if (title.trim().length < 2) {
      setError("Vui lÃ²ng nháº­p tiÃªu Ä'á».");
      return;
    }
    setError(null);
    try {
      await create.mutateAsync({
        title: title.trim(),
        description: description.trim() || null,
        priority,
        task_type: taskType,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });
      setCreating(false);
      setTitle("");
      setDescription("");
      setDueAt("");
      setPriority(2);
      setTaskType("other");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  function move(task: Task, status: TaskStatus) {
    void update.mutate({ id: task.id, patch: { status } });
  }

  if (query.isLoading) return <Loading />;
  if (query.isError) {
    return <ErrorState message="KhÃ´ng táº£i Ä'Æ°á»£c danh sÃ¡ch cÃ´ng viá»?c." onRetry={() => void query.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">CÃ´ng viá»?c</h1>
          <p className="text-sm text-slate-500">
            Viá»?c cáº§n ngÆ°á»i xá»­ lÃ½: theo dÃµi khÃ¡ch, hoÃ n vÃ©, kiá»fm tra tÃ i liá»?uâ?¦
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex cursor-pointer-pointer items-center gap-1.5 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={overdueOnly}
              onChange={(e) => setOverdueOnly(e.target.checked)}
            />
            Chá»? quÃ¡ háº¡n
          </label>
          <Button onClick={() => setCreating(true)}>Táº¡o cÃ´ng viá»?c</Button>
        </div>
      </header>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {COLUMNS.map((col) => {
          const items = grouped[col.status] ?? [];
          return (
            <Card key={col.status} title={`${col.label} (${items.length})`}>
              <div className="space-y-2">
                {items.length === 0 && (
                  <p className="py-6 text-center text-xs text-slate-400">Trá»'ng</p>
                )}
                {items.map((task) => (
                  <article
                    key={task.id}
                    className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm"
                  >
                    <div className="mb-1 flex items-start justify-between gap-2">
                      <p className="text-sm font-medium text-slate-700">{task.title}</p>
                      <Badge tone={toneForPriority(task.priority)}>
                        {priorityLabel(task.priority)}
                      </Badge>
                    </div>

                    {task.description && (
                      <p className="mb-2 line-clamp-2 text-xs text-slate-500">
                        {task.description}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-1.5 text-xs">
                      <Badge tone="neutral">{task.task_type}</Badge>
                      {task.due_at && (
                        <Badge
                          tone={isOverdue(task.due_at, task.status) ? "danger" : "info"}
                        >
                          {formatDate(task.due_at)}
                        </Badge>
                      )}
                    </div>

                    <div className="mt-2 flex gap-1">
                      {col.status !== "done" && (
                        <button
                          type="button"
                          className="rounded px-2 py-1 text-xs text-brand-600 hover:bg-brand-50"
                          onClick={() => move(task, "done")}
                        >
                          HoÃ n thÃ nh
                        </button>
                      )}
                      {col.status !== "in_progress" && col.status !== "done" && (
                        <button
                          type="button"
                          className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
                          onClick={() => move(task, "in_progress")}
                        >
                          Báº¯t Ä'áº§u
                        </button>
                      )}
                      {col.status !== "todo" && col.status !== "done" && (
                        <button
                          type="button"
                          className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
                          onClick={() => move(task, "todo")}
                        >
                          Vá» cáº§n lÃ m
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </Card>
          );
        })}
      </div>

      {(query.data?.items ?? []).length === 0 && (
        <EmptyState
          title="ChÆ°a cÃ³ cÃ´ng viá»?c nÃ o"
          hint="Táº¡o cÃ´ng viá»?c Ä'á»f theo dÃµi cÃ¡c viá»?c cáº§n ngÆ°á»i xá»­ lÃ½."
        />
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="Táº¡o cÃ´ng viá»?c"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Huá»·
            </Button>
            <Button onClick={() => void submit()} disabled={create.isPending}>
              {create.isPending ? "Äang lÆ°uâ?¦" : "Táº¡o"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="TiÃªu Ä'á»">
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Gá»i láº¡i khÃ¡ch xÃ¡c nháº­n hoÃ n vÃ©"
            />
          </Field>

          <Field label="MÃ´ táº£">
            <Textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Chi tiáº¿t cáº§n lÃ mâ?¦"
            />
          </Field>

          <div className="grid grid-cols-3 gap-3">
            <Field label="Æ¯u tiÃªn">
              <Select value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
                <option value={1}>Cao</option>
                <option value={2}>Trung bÃ¬nh</option>
                <option value={3}>Tháº¥p</option>
              </Select>
            </Field>

            <Field label="Loáº¡i">
              <Select value={taskType} onChange={(e) => setTaskType(e.target.value)}>
                <option value="customer_followup">Theo dÃµi khÃ¡ch</option>
                <option value="booking_issue">Váº¥n Ä'á» Ä'áº·t chá»-</option>
                <option value="refund">HoÃ n tiá»n</option>
                <option value="document_review">Kiá»fm tra tÃ i liá»?u</option>
                <option value="logistics">Váº­n hÃ nh</option>
                <option value="report">BÃ¡o cÃ¡o</option>
                <option value="other">KhÃ¡c</option>
              </Select>
            </Field>

            <Field label="Háº¡n">
              <input
                className="input"
                type="date"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
              />
            </Field>
          </div>

          {error && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              {error}
            </p>
          )}
        </div>
      </Modal>
    </div>
  );
}
