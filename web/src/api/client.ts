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
  apply_status?: string | null;
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
  verified: boolean;
  timeAgo: string;
  status: string;
  applyStatus: string | null;
};

export type Me = {
  id: number;
  name: string;
  email: string;
  role?: string;
};

export type Stats = {
  new: number;
  approved: number;
  applied: number;
  rejected: number;
  deferred?: number;
  total: number;
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
    throw new HttpError(res.status, `Request failed: ${res.status}`);
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
  later: (id: number) => request(`/api/jobs/${id}/later`, "POST", {}),
  restore: (id: number) => request(`/api/jobs/${id}/restore`, "POST", {}),
  runSearch: () => request("/api/run-search", "POST", {}),
  pushPublicKey: () => request<{ publicKey: string }>("/api/push/public-key"),
  pushSubscribe: (sub: any) => request("/api/push/subscribe", "POST", { subscription: sub }),
  pushTest: () => request("/api/push/test", "POST", {}),
  saveProfile: (body: Record<string, unknown>) =>
    request("/api/save-profile", "POST", body),
  saveSchedule: (body: Record<string, unknown>) =>
    request("/api/save-schedule", "POST", body),
  saveNotifications: (body: Record<string, unknown>) =>
    request("/api/save-notifications", "POST", body),
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
    verified: j.url_verified === 1,
    timeAgo: timeAgo(j.found_date),
    status: j.status,
    applyStatus: j.apply_status ?? null,
  };
}
