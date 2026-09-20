import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------- formatting
export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: digits });
  return value.toFixed(digits);
}

export function int(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Math.round(value).toLocaleString();
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function frac(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export function money(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
  } catch {
    return `${value.toFixed(0)} ${currency}`;
  }
}

export const titleCase = (value: string) =>
  value.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

// ---------------------------------------------------------------- fetching
interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Small data hook. `deps` re-triggers the request; `pollMs` re-fetches on a
 * timer (used while the dataset catalog is still building).
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = [], pollMs?: number): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetcherRef
      .current()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  useEffect(() => {
    window.addEventListener("datasetChanged", reload);
    return () => window.removeEventListener("datasetChanged", reload);
  }, [reload]);

  useEffect(() => {
    if (!pollMs) return;
    const timer = window.setInterval(reload, pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs, reload]);

  return { data, error, loading, reload };
}

export function useHashState<T extends string>(initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(initial);
  return [value, setValue];
}
