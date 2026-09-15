// Thin API client for the existing Job Hunter backend.
// Same-origin: cookies (session) are sent automatically with credentials:"include".
// On 401 we bounce to the existing /login page — phone and web share the session.

const BASE = ""; // same origin

export type ApiJob = {
  id: number;
  title: string;
  company: string;
  location: string;
  url?: string | null;
  description?: string | null;
  full_description?: string | null;
  why_relevant?: string | null;
  source?: string | null;
  found_date?: string | null;
  status: string;
  match_score?: number | null;
  candidate_score?: number | null;
  url_verified?: number | null;
  // Demotion from the user's own pass history, 0-60. The API already sorts by
  // (match_score - feedback_penalty); the PWA dropped both fields on the floor
  // and then re-sorted by the raw score, undoing it. See SCORING.md §4.
  feedback_penalty?: number | null;
  feedback_reason?: string | null;
  apply_status?: string | null;
  // Where the application got to AFTER it was sent. A separate column from
  // apply_status on purpose: one records that we submitted, the other what
  // came back. /api/set-stage used to write apply_status and destroy the first.
  stage?: string | null;
  // engine | manual | bulk | no_url | null. 'bulk' means a one-off cleanup
  // marked it applied without applying to anything; see migration 9.
  applied_via?: string | null;
  apply_confirmation?: string | null;
  apply_error?: string | null;
  apply_failure_type?: string | null;
  applied_date?: string | null;
};

export type UiJob = {
  id: number;
  title: string;
  company: string;
  location: string;
  url: string;
  source: string;
  description: string;
  whyFits: string;
  matchScore: number | null;
  candidateScore: number | null;
  /** 0-60, from the user's own pass history. The effective rank is
   *  matchScore - feedbackPenalty; see SCORING.md. */
  feedbackPenalty: number;
  feedbackReason: string;
  verified: boolean;
  timeAgo: string;
  status: string;
  applyStatus: string | null;
  /** screening | interviewing | offer | rejected, or "" */
  stage: string;
  /** engine | manual | bulk | no_url, or "" */
  appliedVia: string;
  applyConfirmation: string;
  applyError: string;
  applyFailureType: string;
  foundDate: string | null;
};

export type Me = {
  id: number;
  name: string;
  email: string;
  role?: string;
  is_admin?: boolean;
  cv_path?: string | null;
  cv_filename?: string | null;
  cv_uploaded_date?: string | null;
  cv_optimizer_date?: string | null;
  cv_analyzed?: number | null;
  // Set once the setup flow is finished; `dismissed` is the user saying "never
  // ask again". Both come straight from user_profiles via /api/me.
  onboarding_complete?: number | null;
  onboarding_dismissed?: number | null;
  job_titles?: string | string[] | null;
  keywords?: string | string[] | null;
  locations?: string | string[] | null;
  linkedin_url?: string | null;
  // 'free' | 'premium' | 'expert'. The UI must ask the server rather than
  // infer entitlement, or the two disagree about who may do what.
  plan?: string | null;
  auto_apply_enabled?: number | null;
  phone?: string | null;
  email_address?: string | null;
  schedule_frequency?: string | null;
  search_hour?: number | null;
  apply_hour?: number | null;
  // 0=Monday..6=Sunday, matching Python's datetime.weekday() - the scheduler
  // compares these columns against it directly (app.py:597).
  search_day_of_week?: number | null;
  apply_day_of_week?: number | null;
  notification_channel?: string | null;
};

export type CvOptimizerResult = {
  score?: number;
  score_label?: string;
  summary?: string;
  strengths?: string[];
  improvements?: { title: string; detail: string }[];
  ats_notes?: string[];
  analyzed_date?: string;
  cached?: boolean;
  error?: string;
};

export type ValidateLinksResult = {
  checked: number;
  removed: number;
  alive: number;
  unknown: number;
  removed_items: { title: string; company: string }[];
};

export type Stats = {
  new: number;
  approved: number;
  applied: number;
  rejected: number;
  deferred?: number;
  total: number;
  // status='applied' was written by four unrelated things and status='rejected'
  // by two, so every rate computed from them measured something nobody had
  // defined. These split them; see migration 9. Optional because a box running
  // an older build will not send them.
  applied_engine?: number;
  applied_manual?: number;
  applied_bulk?: number;
  applied_no_url?: number;
  applied_unknown?: number;
  passed_by_user?: number;
  passed_by_system?: number;
  passed_unknown?: number;
  rejected_archived?: number;
};

export type Activity = {
  id: number;
  event_type: string;
  details: string;
  created_date: string;
};

class HttpError extends Error {
  status: number;
  constructor(status: number, msg: string) {
    super(msg);
    this.status = status;
  }
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  // Backend may answer unauthenticated requests with either a 401 or a
  // 302 redirect to /login (fetch follows it transparently). Handle both.
  if (res.status === 401 || (res.redirected && /\/login/.test(res.url))) {
    window.location.href = "/login";
    throw new HttpError(401, "Not authenticated");
  }
  if (!res.ok) {
    let detail = `Request failed: ${res.status}`;
    try { const j = await res.clone().json(); if (j && j.error) detail = j.error; } catch { /* non-json */ }
    throw new HttpError(res.status, detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return undefined as unknown as T;
}

export const api = {
  me: () => request<Me>("/api/me"),
  stats: () => request<Stats>("/api/stats"),
  activity: () => request<Activity[]>("/api/activity"),
  jobs: (status: string, sort = "match") =>
    request<ApiJob[]>(`/api/jobs?status=${encodeURIComponent(status)}&sort=${sort}`),
  approve: (id: number) => request(`/api/jobs/${id}/approve`, "POST", {}),
  reject: (id: number, reason: string) =>
    request(`/api/jobs/${id}/reject`, "POST", { reason }),
  applyNow: (id: number) => request(`/api/jobs/${id}/apply-now`, "POST", {}),
  markApplied: (id: number) => request(`/api/jobs/${id}/applied`, "POST", { notes: "Marked applied manually" }),
  later: (id: number) => request(`/api/jobs/${id}/later`, "POST", {}),
  // Puts a job back in the review queue and clears every apply field. Also the
  // way to undo a bulk-marked "applied" that was never applied to.
  restore: (id: number) => request<{ success?: boolean }>(`/api/jobs/${id}/restore`, "POST", {}),
  runSearch: () => request("/api/run-search", "POST", {}),
  setStage: (id: number, stage: string) =>
    request<{ ok?: boolean; error?: string }>("/api/set-stage", "POST", { id, stage }),
  bulk: (action: "approve" | "reject", ids: number[]) =>
    request<{ success?: boolean; updated?: number }>("/api/jobs/bulk", "POST", { action, ids }),
  runApply: () => request<{ started?: boolean; status?: string; error?: string }>(
    "/api/run-apply", "POST", {}),

  // Reads a CV and fills the profile from it (titles, keywords, locations).
  // NOT cvOptimizer, which scores a CV and changes nothing - this is the one a
  // new user needs, and /app had no way to call it.
  analyzeCv: () => request<{
    summary?: string; job_titles?: string[]; keywords?: string[];
    locations?: string[]; seniority?: string; experience_years?: number;
    error?: string;
  }>("/api/analyze-cv", "POST", {}),

  dismissOnboarding: () => request("/api/dismiss-onboarding", "POST", {}),

  changePassword: (current_password: string, new_password: string) =>
    request<{ success: boolean; error?: string; signed_out_other_sessions?: boolean }>(
      "/api/change-password", "POST", { current_password, new_password }),

  // The configured channel (Telegram / WhatsApp / email), not web push.
  testNotification: (channel: string) =>
    request<{ success?: boolean; error?: string; detail?: string }>(
      "/api/test-notification", "POST", { channel }),
  cvOptimizerCached: () => request<CvOptimizerResult>("/api/cv-optimizer-analyze"),
  cvOptimizer: () => request<CvOptimizerResult>("/api/cv-optimizer-analyze", "POST", {}),
  validateLinks: () => request<ValidateLinksResult>("/api/validate-links", "POST", {}),
  learned: () => request<{ pass_reasons: { reason: string; count: number }[]; blocklist: string[]; patterns: { id: number; company: string; title: string; notes: string; created_date: string }[] }>("/api/learned"),
  blockCompany: (company: string) => request("/api/blocklist", "POST", { company }),
  unblockCompany: (company: string) => request("/api/blocklist/remove", "POST", { company }),
  forgetPattern: (id: number) => request("/api/patterns/forget", "POST", { id }),
  pushPublicKey: () => request<{ publicKey: string }>("/api/push/public-key"),
  pushSubscribe: (sub: any) => request("/api/push/subscribe", "POST", { subscription: sub }),
  pushTest: () => request("/api/push/test", "POST", {}),
  saveProfile: (body: Record<string, unknown>) =>
    request("/api/save-profile", "POST", body),
  saveSchedule: (body: Record<string, unknown>) =>
    request("/api/save-schedule", "POST", body),
  saveNotifications: (body: Record<string, unknown>) =>
    request("/api/save-notifications", "POST", body),
  adminQueueStats: () => request<{ new: number; approved: number; applied: number; rejected: number; manual_required: number; dead_links: number }>("/api/admin/queue-stats"),
  adminClearAttempted: () => request<{ cleared: number }>("/api/admin/clear-attempted", "POST", {}),
  adminClearApplied: () => request<{ deleted: number }>("/api/admin/clear-applied", "POST", {}),
  adminRescore: () => request<{ rescored?: number; message?: string }>("/api/admin/rescore", "POST", {}),
  adminDedup: () => request<{ removed: number }>("/api/admin/dedup"),
  adminUsers: () => request<any[]>("/api/admin/users"),
  // Returns the NEW state, so the row renders what is rather than what it
  // guessed. 400 with code "cannot_disable_self" if you aim it at yourself.
  adminToggleUser: (id: number) =>
    request<{ success?: boolean; is_active?: number; error?: string; code?: string }>(
      `/api/admin/users/${id}/toggle`, "POST", {}),
  // Read-only diagnosis of the apply runtime. Submits nothing.
  adminApplySelftest: () => request<Record<string, any>>("/api/admin/apply-selftest"),
};

import { timeAgo } from "../lib/format";

export function toUiJob(j: ApiJob): UiJob {
  return {
    id: j.id,
    title: j.title || "Untitled role",
    company: j.company || "Unknown company",
    location: j.location || "",
    url: j.url || "",
    source: j.source || "",
    description: j.full_description || j.description || "",
    whyFits: j.why_relevant || "",
    matchScore: j.match_score ?? null,
    candidateScore: j.candidate_score ?? null,
    feedbackPenalty: j.feedback_penalty ?? 0,
    feedbackReason: j.feedback_reason || "",
    verified: j.url_verified === 1,
    timeAgo: timeAgo(j.found_date),
    status: j.status,
    applyStatus: j.apply_status ?? null,
    stage: j.stage || "",
    appliedVia: j.applied_via || "",
    applyConfirmation: j.apply_confirmation || "",
    applyError: j.apply_error || "",
    applyFailureType: j.apply_failure_type || "",
    foundDate: j.found_date ?? null,
  };
}
