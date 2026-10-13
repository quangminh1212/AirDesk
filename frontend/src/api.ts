import axios, { type AxiosInstance } from "axios";
import type {
  AgentReply,
  AgentRunRequest,
  Booking,
  BookingCreateRequest,
  BookingDetail,
  Conversation,
  ConversationDetail,
  DocumentAnswer,
  DocumentRecord,
  Escalation,
  FlightOffer,
  FlightSearchRequest,
  InboundEmail,
  LoginResponse,
  OverviewReport,
  Page,
  PollSummary,
  ProviderList,
  Role,
  QATurn,
  QAOverview,
  Shipment,
  ShipmentCreate,
  ShipmentEvent,
  ShipmentCancel,
  ShipmentStats,
  ShipmentStatusUpdate,
  ShipmentSummary,
  Task,
  TaskCreate,
  TaskStats,
  TaskUpdate,
  TrackingEventCreate,
  User,
  UserCreate,
  UserUpdate,
  VolumeReport,
  AgentPerformancePoint,
} from "@/types";

const BASE_URL = import.meta.env.VITE_API_URL || "";

export const TOKEN_KEY = "airdesk.access_token";
export const REFRESH_KEY = "airdesk.refresh_token";

export const api: AxiosInstance = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { "Content-Type": "application/json" },
  timeout: 60000,
});

// Attach the bearer token to every request when present.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, clear credentials and bounce to the login screen.
api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
      if (!location.pathname.startsWith("/login")) {
        location.assign("/login");
      }
    }
    return Promise.reject(error);
  },
);

export const authApi = {
  async login(email: string, password: string): Promise<LoginResponse> {
    const { data } = await api.post<LoginResponse>("/auth/login", { email, password });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
    return data;
  },
  async me(): Promise<User> {
    const { data } = await api.get<User>("/auth/me");
    return data;
  },
  logout(): void {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

export const flightApi = {
  async providers(): Promise<ProviderList> {
    const { data } = await api.get<ProviderList>("/flights/providers");
    return data;
  },
  async search(req: FlightSearchRequest): Promise<FlightOffer[]> {
    const { data } = await api.post<FlightOffer[]>("/flights/search", req);
    return data;
  },
  async getOffer(provider: string, offerId: string): Promise<FlightOffer> {
    const { data } = await api.get<FlightOffer>(`/flights/offers/${provider}/${offerId}`);
    return data;
  },
  /** NDC OfferPrice: re-price a shopped offer before booking. */
  async price(provider: string, offerId: string): Promise<FlightOffer> {
    const { data } = await api.post<FlightOffer>(
      `/flights/offers/${provider}/${offerId}/price`,
    );
    return data;
  },
};

export const bookingApi = {
  async create(req: BookingCreateRequest): Promise<Booking> {
    const { data } = await api.post<Booking>("/bookings", req);
    return data;
  },
  async list(params?: { page?: number; page_size?: number }): Promise<Page<Booking> | Booking[]> {
    const { data } = await api.get<Page<Booking> | Booking[]>("/bookings", { params });
    return data;
  },
  async get(id: string): Promise<BookingDetail> {
    const { data } = await api.get<BookingDetail>(`/bookings/${id}`);
    return data;
  },
  async cancel(id: string): Promise<Booking> {
    const { data } = await api.post<Booking>(`/bookings/${id}/cancel`);
    return data;
  },
};

export const agentApi = {
  /**
   * Send one turn to the flight agent.
   *
   * Endpoint: `POST /agent/chat`, returns the backend `AgentReply` shape
   * (`reply`, `offers`, `qa`, `tool_calls`, ...).
   */
  async chat(req: AgentRunRequest): Promise<AgentReply> {
    const { data } = await api.post<AgentReply>("/agent/chat", req);
    return data;
  },
  async conversations(params?: {
    page?: number;
    page_size?: number;
  }): Promise<Page<Conversation>> {
    const { data } = await api.get<Page<Conversation>>("/agent/conversations", { params });
    return data;
  },
  async conversation(id: string): Promise<ConversationDetail> {
    const { data } = await api.get<ConversationDetail>(`/agent/conversations/${id}`);
    return data;
  },
  async escalations(params?: {
    page?: number;
    page_size?: number;
    status_filter?: string;
  }): Promise<Page<Escalation>> {
    const { data } = await api.get<Page<Escalation>>("/agent/escalations", { params });
    return data;
  },
  async resolveEscalation(
    id: string,
    payload: { resolution_note: string; action: "resolve" | "reject" },
  ): Promise<Escalation> {
    const { data } = await api.post<Escalation>(`/agent/escalations/${id}/resolve`, payload);
    return data;
  },
};

export const qaApi = {
  async overview(days = 7): Promise<QAOverview> {
    const { data } = await api.get<QAOverview>("/qa/overview", { params: { days } });
    return data;
  },
  async turns(params?: {
    page?: number;
    page_size?: number;
    verdict?: string;
    days?: number;
  }): Promise<Page<QATurn>> {
    const { data } = await api.get<Page<QATurn>>("/qa/turns", { params });
    return data;
  },
  async turn(messageId: string): Promise<QATurn> {
    const { data } = await api.get<QATurn>(`/qa/turns/${messageId}`);
    return data;
  },
};

export const taskApi = {
  async list(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    assignee_id?: string;
    task_type?: string;
    overdue?: boolean;
  }): Promise<Page<Task>> {
    const { data } = await api.get<Page<Task>>("/tasks", { params });
    return data;
  },
  async create(req: TaskCreate): Promise<Task> {
    const { data } = await api.post<Task>("/tasks", req);
    return data;
  },
  async update(id: string, req: TaskUpdate): Promise<Task> {
    const { data } = await api.patch<Task>(`/tasks/${id}`, req);
    return data;
  },
  async stats(): Promise<TaskStats> {
    const { data } = await api.get<TaskStats>("/tasks/stats");
    return data;
  },
};

export const reportApi = {
  async overview(): Promise<OverviewReport> {
    const { data } = await api.get<OverviewReport>("/reports/overview");
    return data;
  },
  async volume(days = 30): Promise<VolumeReport> {
    const { data } = await api.get<VolumeReport>("/reports/volume", { params: { days } });
    return data;
  },
  async agentPerformance(days = 30): Promise<{ points: AgentPerformancePoint[] }> {
    const { data } = await api.get<{ points: AgentPerformancePoint[] }>(
      "/reports/agent-performance",
      { params: { days } },
    );
    return data;
  },
};

export const documentApi = {
  async list(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    doc_type?: string;
  }): Promise<Page<DocumentRecord>> {
    const { data } = await api.get<Page<DocumentRecord>>("/documents", { params });
    return data;
  },
  async get(id: string): Promise<DocumentRecord> {
    const { data } = await api.get<DocumentRecord>(`/documents/${id}`);
    return data;
  },
  async upload(file: File): Promise<DocumentRecord> {
    const form = new FormData();
    form.append("file", file);
    const { data } = await api.post<DocumentRecord>("/documents", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  },
  async ask(id: string, question: string): Promise<DocumentAnswer> {
    const { data } = await api.post<DocumentAnswer>(`/documents/${id}/ask`, { question });
    return data;
  },
  async remove(id: string): Promise<void> {
    await api.delete(`/documents/${id}`);
  },
};

export const inboxApi = {
  async list(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    intent?: string;
  }): Promise<Page<InboundEmail>> {
    const { data } = await api.get<Page<InboundEmail>>("/inbox/messages", { params });
    return data;
  },
  async get(id: string): Promise<InboundEmail> {
    const { data } = await api.get<InboundEmail>(`/inbox/messages/${id}`);
    return data;
  },
  async poll(): Promise<PollSummary> {
    const { data } = await api.post<PollSummary>("/inbox/poll");
    return data;
  },
};

export const shipmentApi = {
  async list(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    mode?: string;
    carrier?: string;
    tracking_number?: string;
    customer_phone?: string;
    overdue?: boolean;
    needs_attention?: boolean;
  }): Promise<Page<ShipmentSummary>> {
    const { data } = await api.get<Page<ShipmentSummary>>("/shipments", { params });
    return data;
  },
  async stats(): Promise<ShipmentStats> {
    const { data } = await api.get<ShipmentStats>("/shipments/stats");
    return data;
  },
  async getByTracking(trackingNumber: string): Promise<Shipment> {
    const { data } = await api.get<Shipment>(
      `/shipments/track/${encodeURIComponent(trackingNumber)}`,
    );
    return data;
  },
  async get(id: string): Promise<Shipment> {
    const { data } = await api.get<Shipment>(`/shipments/${id}`);
    return data;
  },
  async getEvents(id: string): Promise<ShipmentEvent[]> {
    const { data } = await api.get<ShipmentEvent[]>(`/shipments/${id}/events`);
    return data;
  },
  async create(req: ShipmentCreate): Promise<Shipment> {
    const { data } = await api.post<Shipment>("/shipments", req);
    return data;
  },
  async updateStatus(id: string, payload: ShipmentStatusUpdate): Promise<Shipment> {
    const { data } = await api.post<Shipment>(`/shipments/${id}/status`, payload);
    return data;
  },
  async addEvent(id: string, payload: TrackingEventCreate): Promise<Shipment> {
    const { data } = await api.post<Shipment>(`/shipments/${id}/events`, payload);
    return data;
  },
  async cancel(id: string, payload: ShipmentCancel = {}): Promise<Shipment> {
    const { data } = await api.post<Shipment>(`/shipments/${id}/cancel`, payload);
    return data;
  },
};

// --- users (admin) ----------------------------------------------------------

export const usersApi = {
  /** List all users. Admin only. */
  async list(): Promise<User[]> {
    const { data } = await api.get<User[]>("/auth/users");
    return data;
  },
  /** Create a user. Admin only. */
  async create(req: UserCreate): Promise<User> {
    const { data } = await api.post<User>("/auth/users", req);
    return data;
  },
  /** Update a user (role, active flag, name, optional password). Admin only. */
  async update(id: string, payload: UserUpdate): Promise<User> {
    const { data } = await api.patch<User>(`/auth/users/${id}`, payload);
    return data;
  },
  /** Enumerate the roles the backend accepts. */
  async roles(): Promise<Role[]> {
    const { data } = await api.get<Role[]>("/auth/roles");
    return data;
  },
};
