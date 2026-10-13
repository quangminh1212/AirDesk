/**
 * React Query hooks.
 *
 * One place for every server call so cache keys, refresh intervals and
 * error/loading semantics stay consistent across pages.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { agentApi, documentApi, inboxApi, qaApi, reportApi, taskApi } from "@/api";
import type { AgentRunRequest, TaskCreate, TaskUpdate } from "@/types";

function pageParam(page: number, pageSize = 20) {
  return { page, page_size: pageSize };
}

// --- conversations -------------------------------------------------------

export function useConversations(page = 1) {
  return useQuery({
    queryKey: ["conversations", page],
    queryFn: () => agentApi.conversations(pageParam(page)),
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => agentApi.conversation(id as string),
    enabled: Boolean(id),
  });
}

// --- agent chat ----------------------------------------------------------

export function useAgentChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: AgentRunRequest) => agentApi.chat(req),
    onSuccess: (data) => {
      // A turn can create a conversation, an escalation and a booking.
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      void qc.invalidateQueries({ queryKey: ["conversation", data.conversation_id] });
      void qc.invalidateQueries({ queryKey: ["escalations"] });
      void qc.invalidateQueries({ queryKey: ["qa-turns"] });
      void qc.invalidateQueries({ queryKey: ["qa-overview"] });
      void qc.invalidateQueries({ queryKey: ["bookings"] });
      void qc.invalidateQueries({ queryKey: ["report-overview"] });
    },
  });
}

// --- escalations ---------------------------------------------------------

export function useEscalations(page = 1, status?: string) {
  return useQuery({
    queryKey: ["escalations", page, status ?? "all"],
    queryFn: () =>
      agentApi.escalations({ ...pageParam(page), ...(status ? { status_filter: status } : {}) }),
    refetchInterval: 30_000,
  });
}

export function useResolveEscalation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      id: string;
      resolution_note: string;
      action: "resolve" | "reject";
    }) => agentApi.resolveEscalation(args.id, args),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["escalations"] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      void qc.invalidateQueries({ queryKey: ["report-overview"] });
    },
  });
}

// --- QA ------------------------------------------------------------------

export function useQAOverview(days = 7) {
  return useQuery({
    queryKey: ["qa-overview", days],
    queryFn: () => qaApi.overview(days),
  });
}

export function useQATurns(page = 1, verdict?: string, days = 7) {
  return useQuery({
    queryKey: ["qa-turns", page, verdict ?? "all", days],
    queryFn: () => qaApi.turns({ ...pageParam(page), days, ...(verdict ? { verdict } : {}) }),
  });
}

// --- tasks ---------------------------------------------------------------

export function useTasks(params: { page?: number; status?: string; overdue?: boolean } = {}) {
  return useQuery({
    queryKey: ["tasks", params.page ?? 1, params.status ?? "all", params.overdue ?? false],
    queryFn: () =>
      taskApi.list({
        ...pageParam(params.page ?? 1, 100),
        ...(params.status ? { status: params.status } : {}),
        ...(params.overdue ? { overdue: true } : {}),
      }),
    refetchInterval: 30_000,
  });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: TaskCreate) => taskApi.create(req),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["task-stats"] });
      void qc.invalidateQueries({ queryKey: ["report-overview"] });
    },
  });
}

export function useUpdateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; patch: TaskUpdate }) => taskApi.update(args.id, args.patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["task-stats"] });
      void qc.invalidateQueries({ queryKey: ["report-overview"] });
    },
  });
}

// --- reports -------------------------------------------------------------

export function useOverview() {
  return useQuery({
    queryKey: ["report-overview"],
    queryFn: () => reportApi.overview(),
    refetchInterval: 60_000,
  });
}

export function useVolume(days = 30) {
  return useQuery({
    queryKey: ["report-volume", days],
    queryFn: () => reportApi.volume(days),
  });
}

export function useAgentPerformance(days = 30) {
  return useQuery({
    queryKey: ["report-agent-performance", days],
    queryFn: () => reportApi.agentPerformance(days),
  });
}

// --- documents -----------------------------------------------------------

export function useDocuments(page = 1, status?: string) {
  return useQuery({
    queryKey: ["documents", page, status ?? "all"],
    queryFn: () =>
      documentApi.list({ ...pageParam(page), ...(status ? { status } : {}) }),
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => documentApi.upload(file),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents"] });
      void qc.invalidateQueries({ queryKey: ["report-overview"] });
    },
  });
}

export function useAskDocument() {
  return useMutation({
    mutationFn: (args: { id: string; question: string }) =>
      documentApi.ask(args.id, args.question),
  });
}

// --- inbox ---------------------------------------------------------------

export function useInbox(page = 1, status?: string, intent?: string) {
  return useQuery({
    queryKey: ["inbox", page, status ?? "all", intent ?? "all"],
    queryFn: () =>
      inboxApi.list({
        ...pageParam(page),
        ...(status ? { status } : {}),
        ...(intent ? { intent } : {}),
      }),
  });
}

export function usePollInbox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => inboxApi.poll(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inbox"] });
    },
  });
}
