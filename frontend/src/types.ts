/** Shared API types mirroring the FastAPI schemas. */

export type Role = "VIEWER" | "AGENT" | "OPERATOR" | "ADMIN";

/** Generic pagination envelope returned by `Page[T]` on the backend. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at?: string;
  last_login_at?: string | null;
}

/** Body for `POST /auth/users`. Password must contain a letter AND a digit. */
export interface UserCreate {
  email: string;
  full_name: string;
  role: Role;
  password: string;
}

/** Body for `PATCH /auth/users/{id}`. All fields optional. */
export interface UserUpdate {
  full_name?: string;
  role?: Role;
  is_active?: boolean;
  /** Optional password reset; server enforces the same letter+digit rule. */
  password?: string;
}

export interface Segment {
  origin: string;
  destination: string;
  departure_at: string;
  arrival_at: string;
  carrier_code: string;
  carrier_name: string;
  flight_number: string;
  duration_minutes: number;
  aircraft: string | null;
  cabin: string;
  fare_basis: string | null;
  baggage_allowance: string | null;
}

export interface FlightOffer {
  offer_id: string;
  provider: string;
  origin: string;
  destination: string;
  departure_at: string | null;
  return_at: string | null;
  airline_code?: string;
  airline_name?: string;
  primary_carrier?: string;
  primary_carrier_name?: string;
  segments?: Segment[];
  total_amount: number;
  base_amount?: number;
  taxes_amount?: number;
  currency: string;
  stops: number;
  total_duration_minutes: number;
  seats_available: number | null;
  fare_type?: string | null;
  refundable?: boolean | null;
  baggage_allowance?: string | null;
  expires_at?: string | null;
  /** "shopped" (reference only) or "priced" (bookable). */
  kind?: string;
  price_guarantee_until?: string | null;
  is_bookable?: boolean;
  markup_amount?: number;
  airline_amount?: number;
}

export interface FlightSearchRequest {
  origin: string;
  destination: string;
  departure_date: string;
  return_date?: string | null;
  adults: number;
  children: number;
  infants: number;
  cabin: string;
  currency: string;
  non_stop: boolean;
  max_results: number;
  provider: string;
}

export interface ProviderList {
  providers: string[];
}

export interface PassengerInput {
  passenger_type: string;
  first_name: string;
  last_name: string;
  email?: string | null;
  phone?: string | null;
  document_type?: string | null;
  document_number?: string | null;
  nationality?: string | null;
}

export interface BookingCreateRequest {
  offer_id: string;
  provider: string;
  passengers: PassengerInput[];
  contact_email: string;
  contact_phone?: string | null;
  cabin: string;
}

export interface Booking {
  id: string;
  reference: string;
  status: string;
  provider: string;
  pnr?: string | null;
  total_amount: number;
  currency: string;
  contact_email: string;
  contact_phone?: string | null;
  created_at: string;
  passengers?: unknown[];
}

export interface PassengerRead {
  id: string;
  passenger_type: string;
  first_name: string;
  last_name: string;
  date_of_birth: string | null;
  nationality?: string | null;
  document_type?: string | null;
  document_number?: string | null;
  email?: string | null;
  phone?: string | null;
}

export interface OrderEventRead {
  id: string;
  event_type: string;
  occurred_at: string;
  actor: string;
  from_status?: string | null;
  to_status?: string | null;
  payload?: Record<string, unknown>;
}

export interface TicketRead {
  id: string;
  ticket_number: string;
  segment_index: number;
  flight_number: string;
  status: string;
  pnr?: string | null;
  fare_amount: number;
  tax_amount: number;
  issued_at?: string | null;
  voided_at?: string | null;
  refunded_at?: string | null;
  refund_amount: number;
  notes?: string | null;
}

export interface BookingDetail extends Booking {
  passengers: PassengerRead[];
  events: OrderEventRead[];
  tickets: TicketRead[];
  notes?: string | null;
}

// --- agent ---------------------------------------------------------------

export interface ToolCallSummary {
  tool: string;
  success: boolean;
  duration_ms: number;
  error?: string | null;
  arguments?: Record<string, unknown> | null;
}

export interface QAFinding {
  code: string;
  message: string;
  severity: "info" | "warning" | "error" | "critical" | string;
  details?: Record<string, unknown>;
}

export interface QAReport {
  verdict: "pass" | "review" | "fail" | string;
  score: number;
  findings: QAFinding[];
}

/** Response from `POST /agent/chat` (see backend `AgentReply`). */
export interface AgentReply {
  conversation_id: string;
  reply: string;
  intent: string | null;
  confidence: number | null;
  offers: FlightOffer[];
  booking: Booking | null;
  escalated: boolean;
  escalation_reason: string | null;
  tool_calls: ToolCallSummary[];
  qa: QAReport | null;
}

export interface AgentRunRequest {
  message: string;
  conversation_id?: string | null;
  channel: string;
}

export type Channel = "web" | "zalo" | "messenger" | "email" | "api" | "internal";
export type ConversationStatus = "open" | "pending_human" | "resolved" | "closed";

export interface ToolCall extends ToolCallSummary {
  id: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  model?: string | null;
  confidence?: number | null;
  latency_ms?: number;
  created_at: string;
  tool_calls?: ToolCall[];
}

export interface Conversation {
  id: string;
  channel: Channel;
  status: ConversationStatus;
  customer_ref: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export type EscalationStatus = "open" | "claimed" | "resolved" | "rejected";

export interface Escalation {
  id: string;
  conversation_id: string | null;
  booking_id: string | null;
  reason: string;
  details: Record<string, unknown> | null;
  status: EscalationStatus;
  priority: number;
  assigned_to_id: string | null;
  resolution_note: string | null;
  created_at: string;
  resolved_at: string | null;
}

// --- QA review -----------------------------------------------------------

export interface QATurn {
  message_id: string;
  conversation_id: string;
  created_at: string;
  verdict: string;
  score: number;
  confidence: number | null;
  latency_ms: number;
  model: string | null;
  cost_usd: number;
  content_preview: string;
  findings: QAFinding[];
  tool_calls: ToolCallSummary[];
}

export interface QAOverview {
  window_days: number;
  total_turns: number;
  passed: number;
  review: number;
  failed: number;
  pass_rate: number;
  avg_score: number;
  avg_confidence: number | null;
  top_findings: Record<string, number>;
}

// --- tasks ---------------------------------------------------------------

export type TaskStatus = "todo" | "in_progress" | "blocked" | "done" | "cancelled";

export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: number;
  task_type: string;
  assignee_id: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  escalation_id?: string | null;
  document_id?: string | null;
  due_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at?: string;
}

export interface TaskCreate {
  title: string;
  description?: string | null;
  status?: TaskStatus;
  priority?: number;
  task_type?: string;
  assignee_id?: string | null;
  due_at?: string | null;
}

export interface TaskUpdate {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  priority?: number;
  task_type?: string;
  assignee_id?: string | null;
  due_at?: string | null;
}

export interface TaskStats {
  by_status?: Record<string, number>;
  by_assignee?: Record<string, number>;
  overdue?: number;
  total?: number;
}

// --- documents -----------------------------------------------------------

export interface DocumentRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: string;
  doc_type: string;
  confidence: number;
  needs_ocr: boolean;
  error: string | null;
  created_at: string;
  text_content?: string | null;
  extracted?: Record<string, unknown> | null;
}

export interface DocumentAnswer {
  answer: string;
}

// --- shipments (logistics) ----------------------------------------------

export type ShipmentStatus =
  | "DRAFT"
  | "BOOKED"
  | "PICKED_UP"
  | "IN_TRANSIT"
  | "CUSTOMS"
  | "OUT_FOR_DELIVERY"
  | "DELIVERED"
  | "DELAYED"
  | "EXCEPTION"
  | "CANCELLED"
  | "RETURNED";

export type ShipmentMode = "AIR" | "SEA" | "ROAD" | "RAIL" | "COURIER";

export type ShipmentPriority = 1 | 2 | 3;

export interface ShipmentEvent {
  id: string;
  event_type: string;
  occurred_at: string;
  actor: string;
  location?: string | null;
  from_status?: string | null;
  to_status?: string | null;
  note?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface ShipmentSummary {
  id: string;
  reference: string;
  status: ShipmentStatus;
  mode: ShipmentMode;
  priority: ShipmentPriority;
  carrier?: string | null;
  tracking_number?: string | null;
  origin: string;
  destination: string;
  customer_name?: string | null;
  pieces: number;
  weight_kg?: number | null;
  eta_at?: string | null;
  delivered_at?: string | null;
  created_at: string;
  is_overdue?: boolean;
}

export interface Shipment {
  id: string;
  reference: string;
  status: ShipmentStatus;
  mode: ShipmentMode;
  priority: ShipmentPriority;
  carrier?: string | null;
  tracking_number?: string | null;
  waybill_number?: string | null;
  origin: string;
  destination: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  customer_email?: string | null;
  description?: string | null;
  pieces: number;
  weight_kg?: number | null;
  declared_value: number;
  cod_amount: number;
  currency: string;
  requires_customs: boolean;
  is_dangerous_goods: boolean;
  eta_at?: string | null;
  picked_up_at?: string | null;
  delivered_at?: string | null;
  created_at: string;
  updated_at: string;
  meta?: Record<string, unknown> | null;
  is_overdue?: boolean;
  allowed_next?: ShipmentStatus[];
  events?: ShipmentEvent[];
}

export interface ShipmentStats {
  total?: number;
  by_status?: Array<{ status: string; count: number }>;
  by_mode?: Array<{ mode: string; count: number }>;
  in_transit?: number;
  needs_attention?: number;
  overdue?: number;
  delivered_this_week?: number;
  total_declared_value_vnd?: number;
}

export interface ShipmentCreate {
  origin: string;
  destination: string;
  mode?: ShipmentMode;
  priority?: ShipmentPriority;
  carrier?: string | null;
  customer_name?: string | null;
  customer_phone?: string | null;
  customer_email?: string | null;
  description?: string | null;
  pieces?: number;
  weight_kg?: number | null;
  declared_value?: number;
  cod_amount?: number;
  currency?: string;
  requires_customs?: boolean;
  is_dangerous_goods?: boolean;
  eta_at?: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  notes?: string | null;
  idempotency_key?: string | null;
}

export interface ShipmentStatusUpdate {
  status: ShipmentStatus;
}

export interface TrackingEventCreate {
  event_type: string;
  location?: string | null;
  note?: string | null;
  occurred_at?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface ShipmentCancel {
  reason?: string | null;
}

// --- inbox ---------------------------------------------------------------

export interface InboundEmail {
  id: string;
  message_id?: string;
  from_address: string;
  to_address?: string | null;
  subject: string | null;
  body_text?: string | null;
  intent: string;
  status: string;
  conversation_id: string | null;
  error: string | null;
  created_at: string;
  processed_at?: string | null;
}

export interface PollSummary {
  fetched: number;
  processed: number;
  failed: number;
  skipped: number;
  configured?: boolean;
}

// --- reports -------------------------------------------------------------

export interface CountGroup {
  total?: number;
  [key: string]: unknown;
}

export interface OverviewReport {
  conversations?: {
    total?: number;
    open?: number;
    pending_human?: number;
    resolved?: number;
    [k: string]: unknown;
  };
  messages?: { total?: number; last_24h?: number; last_7d?: number; [k: string]: unknown };
  bookings?: {
    total?: number;
    by_status?: Record<string, number>;
    revenue_by_currency?: Record<string, number>;
    [k: string]: unknown;
  };
  escalations?: {
    total?: number;
    open?: number;
    avg_resolution_minutes?: number | null;
    [k: string]: unknown;
  };
  agent_quality?: {
    avg_confidence?: number | null;
    avg_latency_ms?: number | null;
    total_cost_usd?: number;
    tool_success_rate?: number | null;
    [k: string]: unknown;
  };
  tasks?: { open?: number; overdue?: number; done_this_week?: number; [k: string]: unknown };
  documents?: { total?: number; by_doc_type?: Record<string, number>; [k: string]: unknown };
}

export interface VolumePoint {
  date: string;
  conversations: number;
  messages: number;
  bookings: number;
  escalations: number;
}

export interface VolumeReport {
  points: VolumePoint[];
}

export interface AgentPerformancePoint {
  date: string;
  turns: number;
  avg_confidence: number | null;
  escalation_rate: number;
  avg_latency_ms: number | null;
  total_cost_usd: number;
  tool_failure_rate: number;
}
