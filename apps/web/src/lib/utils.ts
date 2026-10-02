import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Compact integer formatting for engagement counts (12.4K, 3.1M). */
export function compactNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function exactNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

/**
 * Percentages are always rendered with their denominator somewhere nearby.
 * Brief §28 forbids presenting a sample proportion as a market-wide fact, and
 * "38%" with no n is exactly that mistake.
 */
export function sharePercent(numerator: number, denominator: number): string {
  if (!denominator) return "—";
  return `${((numerator / denominator) * 100).toFixed(numerator / denominator < 0.1 ? 1 : 0)}%`;
}

export function formatPrice(amount: number | null | undefined, currency = "PHP"): string {
  if (amount === null || amount === undefined) return "—";
  return new Intl.NumberFormat("en-PH", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

/**
 * Postgres `numeric` is serialised as a string so precision is not lost in
 * JSON. Every numeric column therefore arrives as `string | number`, and
 * calling a number method on it throws. Coerce at the boundary.
 */
export function num(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}
