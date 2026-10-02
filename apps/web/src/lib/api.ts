/**
 * Typed client for the AYPO research API.
 *
 * Deliberately thin: it does not reshape responses. The backend already emits
 * the evidence-bearing shapes (claim type, confidence, sample size,
 * provenance), and quietly reshaping them in the client is how those
 * qualifiers get dropped on the way to the screen.
 */

const BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(
  path: string,
  init?: RequestInit & { params?: Record<string, string | number | boolean | undefined> },
): Promise<T> {
  const { params, ...rest } = init ?? {};
  const url = new URL(`${BASE}${path.startsWith("/") ? path : `/${path}`}`);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  const response = await fetch(url, {
    ...rest,
    headers: { "content-type": "application/json", ...(rest.headers ?? {}) },
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    throw new ApiError(
      `${rest.method ?? "GET"} ${path} failed (${response.status})`,
      response.status,
      detail,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/* ── shapes shared across surfaces ────────────────────────────────────────── */

export type ClaimType = "observed" | "computed" | "interpreted" | "hypothesis";
export type ConfidenceBand = "high" | "moderate" | "low" | "insufficient";
export type TrendState =
  | "established"
  | "growing"
  | "emerging"
  | "weak_signal"
  | "stable"
  | "declining";

export type Platform =
  | "tiktok"
  | "youtube"
  | "instagram"
  | "facebook"
  | "web"
  | "shopee"
  | "lazada";

/** What the backend reports about which sources a run could actually use. */
export type HealthResponse = {
  status: "ok" | "degraded";
  env: string;
  database: { ok: boolean; postgres?: string; pgvector?: boolean; migrations_applied?: number };
  models: { reasoning: string; analysis: string; embedding: string };
  capabilities: Record<string, boolean>;
  missing_credentials: Record<string, string[]>;
};

export function getHealth() {
  return api<HealthResponse>("/health");
}

/* ── server-side fetching ─────────────────────────────────────────────────── */

/**
 * Fetch for server components.
 *
 * `no-store` on purpose: a research run mutates this data while it executes, so
 * a cached page would show a run that has already moved on. Returns a fallback
 * rather than throwing, because a dead API should render an explanation, not a
 * Next.js error overlay.
 */
export async function serverFetch<T>(path: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!response.ok) return fallback;
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

export type RunSummary = {
  id: string;
  objective: string;
  subject_brand: string;
  market_code: string;
  state: string;
  iteration_no: number;
  stop_reason: string | null;
  degraded: boolean | null;
  created_at: string;
  stopped_at: string | null;
};

export type Coverage = {
  platform: string;
  available: boolean;
  unavailable_reason: string | null;
  queries_run: number;
  items_discovered: number;
  items_admitted: number;
  planned_units: number | null;
  achieved_units: number | null;
};

export type ResearchQueryRow = {
  id: string;
  iteration_no: number;
  strategy: string;
  platform: string;
  query_text: string;
  rationale: string;
  is_disconfirming: boolean;
  results_total: number | null;
  results_admitted: number | null;
};

export type ContentRow = {
  id: string;
  platform: string;
  source_url: string;
  caption: string | null;
  thumbnail_url: string | null;
  published_at: string | null;
  duration_seconds: number | null;
  views: number | null;
  likes: number | null;
  comments_count: number | null;
  shares: number | null;
  saves: number | null;
  hashtags: string[] | null;
  language_hint: string | null;
  platform_is_sponsored: boolean | null;
  sponsorship_source: string | null;
  creator_handle: string | null;
  follower_count: number | null;
};

export type CommerceRow = {
  id: string;
  platform: string;
  source_url: string;
  title_raw: string;
  brand_surface_form: string | null;
  listing_kind: string | null;
  currency: string;
  price_minor: number | null;
  discount_pct: number | null;
  rating_avg: number | null;
  review_count: number | null;
  sold_count_raw: number | null;
  sold_count_text: string | null;
  sold_semantics: string | null;
  image_url: string | null;
  seller_name: string | null;
};
