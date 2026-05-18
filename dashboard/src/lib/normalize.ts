/** Coerce API decimals (often JSON strings from PostgreSQL NUMERIC) to numbers. */
export function asNumber(value: unknown): number | null {
  if (value == null || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function asInt(value: unknown, fallback = 0): number {
  const n = asNumber(value);
  return n == null ? fallback : Math.trunc(n);
}
