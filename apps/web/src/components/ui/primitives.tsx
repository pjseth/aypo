import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/* ── surfaces ─────────────────────────────────────────────────────────────── */

export function Card({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-card border border-border-subtle bg-panel",
        // No drop shadows: brief §33 asks for minimal decorative UI, and
        // elevation here is carried by border and background alone.
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("flex items-start justify-between gap-4 px-5 pt-4 pb-3", className)}>
      {children}
    </div>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("px-5 pb-5", className)}>{children}</div>;
}

export function SectionTitle({
  children,
  hint,
}: {
  children: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-3">
      <h2 className="text-[13px] font-semibold tracking-wide text-text-secondary uppercase">
        {children}
      </h2>
      {hint ? <span className="text-xs text-text-faint">{hint}</span> : null}
    </div>
  );
}

/* ── badges ───────────────────────────────────────────────────────────────── */

export function Badge({
  className,
  children,
  title,
}: {
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5",
        "text-[11px] font-medium whitespace-nowrap",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * How a statement was arrived at. Brief §3 requires the product to always
 * distinguish observed data, calculated patterns, model interpretation and
 * hypotheses — so this is a required prop on every claim surface, not an
 * optional decoration. The border style varies alongside the colour so the
 * distinction does not depend on colour vision.
 */
export type ClaimType = "observed" | "computed" | "interpreted" | "hypothesis";

const CLAIM_META: Record<ClaimType, { label: string; cls: string; help: string }> = {
  observed: {
    label: "Observed",
    cls: "border-claim-observed/40 text-claim-observed bg-claim-observed/8",
    help: "Directly present in the collected data.",
  },
  computed: {
    label: "Computed",
    cls: "border-claim-computed/40 text-claim-computed bg-claim-computed/8",
    help: "Calculated from the dataset by code, not written by a model.",
  },
  interpreted: {
    label: "Interpreted",
    cls: "border-claim-interpreted/40 text-claim-interpreted bg-claim-interpreted/8",
    help: "A model's reading of the evidence. Judgement, not measurement.",
  },
  hypothesis: {
    label: "Hypothesis",
    cls: "border-dashed border-claim-hypothesis/60 text-claim-hypothesis bg-claim-hypothesis/8",
    help: "Not established. Requires further investigation before being acted on.",
  },
};

export function ClaimTypeBadge({ type }: { type: ClaimType }) {
  const meta = CLAIM_META[type];
  return (
    <Badge className={meta.cls} title={meta.help}>
      {meta.label}
    </Badge>
  );
}

export type ConfidenceBand = "high" | "moderate" | "low" | "insufficient";

const CONFIDENCE_META: Record<ConfidenceBand, { label: string; cls: string }> = {
  high: { label: "High confidence", cls: "border-confidence-high/40 text-confidence-high bg-confidence-high/8" },
  moderate: { label: "Moderate confidence", cls: "border-confidence-moderate/40 text-confidence-moderate bg-confidence-moderate/8" },
  low: { label: "Low confidence", cls: "border-confidence-low/40 text-confidence-low bg-confidence-low/8" },
  insufficient: { label: "Insufficient data", cls: "border-confidence-insufficient/40 text-confidence-insufficient bg-confidence-insufficient/8" },
};

export function ConfidenceBadge({
  band,
  score,
}: {
  band: ConfidenceBand;
  score?: number | null;
}) {
  const meta = CONFIDENCE_META[band];
  return (
    <Badge
      className={meta.cls}
      title={
        score != null
          ? `Confidence ${score.toFixed(2)}. Reflects sample size, effect size, source diversity and temporal coverage.`
          : undefined
      }
    >
      {meta.label}
    </Badge>
  );
}

/**
 * Mirrors the `trend_class` enum in Postgres, including `insufficient_data` —
 * which is not a trend but a refusal to claim one, and is the correct answer
 * far more often than any of the others.
 */
export type TrendState =
  | "established"
  | "growing"
  | "emerging"
  | "weak_signal"
  | "stable"
  | "declining"
  | "insufficient_data";

const TREND_META: Record<TrendState, { label: string; cls: string }> = {
  established: { label: "Established", cls: "border-trend-established/40 text-trend-established bg-trend-established/8" },
  growing: { label: "Growing", cls: "border-trend-growing/40 text-trend-growing bg-trend-growing/8" },
  emerging: { label: "Emerging", cls: "border-trend-emerging/40 text-trend-emerging bg-trend-emerging/8" },
  weak_signal: { label: "Weak signal", cls: "border-trend-weak/40 text-trend-weak bg-trend-weak/8" },
  stable: { label: "Stable", cls: "border-trend-stable/40 text-trend-stable bg-trend-stable/8" },
  declining: { label: "Declining", cls: "border-trend-declining/40 text-trend-declining bg-trend-declining/8" },
  insufficient_data: {
    label: "Not yet claimable",
    cls: "border-confidence-insufficient/40 text-confidence-insufficient bg-confidence-insufficient/8",
  },
};

export function TrendBadge({ state }: { state: TrendState | string }) {
  // A value the database adds later must degrade to a readable badge rather
  // than crashing the page it appears on.
  const meta = TREND_META[state as TrendState] ?? TREND_META.insufficient_data;
  return <Badge className={meta.cls}>{meta.label}</Badge>;
}

/**
 * Sample size, shown next to every proportion or comparative statement.
 * Brief §28 requires sample sizes be visible and that sample findings never
 * read as market-wide facts, so this component carries the scoping language
 * rather than leaving it to each caller to remember.
 */
export function SampleSize({
  n,
  unit = "items",
  scope = "within the analysed sample",
}: {
  n: number;
  unit?: string;
  scope?: string;
}) {
  return (
    <span
      className="font-mono text-[11px] text-text-muted"
      title={`${n} ${unit} — ${scope}. Not a market-wide measurement.`}
    >
      n={n} {unit}
    </span>
  );
}

export function Pill({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-border-subtle bg-elevated",
        "px-2.5 py-0.5 text-[11px] text-text-secondary",
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ── states ───────────────────────────────────────────────────────────────── */

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-border-subtle px-6 py-12 text-center">
      <p className="text-sm font-medium text-text">{title}</p>
      {body ? <p className="max-w-md text-[13px] leading-relaxed text-text-muted">{body}</p> : null}
      {action}
    </div>
  );
}

/**
 * Shown where a panel would otherwise display a weak result as if it were a
 * finding. Brief §28: "If the evidence is weak, the system must say so."
 */
export function InsufficientData({ reason }: { reason: string }) {
  return (
    <div className="rounded-card border border-border-subtle bg-elevated px-4 py-3">
      <div className="flex items-center gap-2">
        <ConfidenceBadge band="insufficient" />
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-text-secondary">{reason}</p>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-md bg-border-subtle/60", className)}
    />
  );
}
