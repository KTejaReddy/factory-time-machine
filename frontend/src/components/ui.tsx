import type { ReactNode } from "react";
import { useState } from "react";
import { api } from "../lib/api";
import { num, pct } from "../lib/hooks";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
  dense = false,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  dense?: boolean;
}) {
  return (
    <section className={`panel fade-in ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          <div className="min-w-0">
            {title && <div className="panel-title">{title}</div>}
            {subtitle && <div className="mt-1 text-[12px] leading-snug text-[var(--color-ink-faint)]">{subtitle}</div>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={dense ? "p-3" : "p-4"}>{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  unit,
  hint,
  tone = "neutral",
  unavailable = false,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "bad" | "accent";
  unavailable?: boolean;
}) {
  const toneClass =
    tone === "ok"
      ? "text-[var(--color-ok)]"
      : tone === "warn"
        ? "text-[var(--color-warn)]"
        : tone === "bad"
          ? "text-[var(--color-bad)]"
          : tone === "accent"
            ? "text-[var(--color-accent)]"
            : "text-[var(--color-ink)]";
  return (
    <div className="panel-flat p-3">
      <div className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-[var(--color-ink-faint)]">{label}</div>
      <div className={`mono mt-1.5 text-[22px] leading-none font-semibold ${unavailable ? "text-[var(--color-ink-faint)]" : toneClass}`}>
        {unavailable ? "n/a" : value}
        {unit && !unavailable && <span className="ml-1 text-[12px] font-medium text-[var(--color-ink-faint)]">{unit}</span>}
      </div>
      {hint && <div className="mt-1.5 text-[11.5px] leading-snug text-[var(--color-ink-faint)]">{hint}</div>}
    </div>
  );
}

export function Badge({
  children,
  tone = "muted",
  title,
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "bad" | "info" | "muted" | "assumed";
  title?: string;
}) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function severityTone(severity: string): "ok" | "warn" | "bad" | "muted" {
  if (severity === "high") return "bad";
  if (severity === "moderate") return "warn";
  if (severity === "low") return "muted";
  return "muted";
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 py-6 text-[13px] text-[var(--color-ink-faint)]">
      <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-[var(--color-edge)] border-t-[var(--color-accent)]" />
      {label}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="callout callout-bad">
      <div className="font-semibold text-[var(--color-bad)]">Request failed</div>
      <div className="mono mt-1 text-[12px] leading-relaxed break-words">{message}</div>
      {onRetry && (
        <button className="btn mt-2.5" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-[13px] text-[var(--color-ink-faint)]">{children}</div>;
}

/** Used wherever the dataset cannot support a feature - never a zero, never a guess. */
export function GatedNotice({ title, reason, alternatives }: { title: string; reason: string; alternatives?: string[] }) {
  return (
    <div className="callout callout-warn border-dashed">
      <div className="flex items-center gap-2 text-[12.5px] font-semibold">
        <Badge tone="warn">not calculable</Badge>
        {title}
      </div>
      <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">{reason}</p>
      {alternatives && alternatives.length > 0 && (
        <ul className="mt-2 space-y-1 text-[12px] text-[var(--color-ink-faint)]">
          {alternatives.map((a) => (
            <li key={a} className="flex gap-2">
              <span className="text-[var(--color-accent)]">·</span>
              <span>{a}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Bullets({ items, tone = "dim" }: { items: string[]; tone?: "dim" | "faint" }) {
  if (!items?.length) return null;
  const color = tone === "dim" ? "text-[var(--color-ink-dim)]" : "text-[var(--color-ink-faint)]";
  return (
    <ul className={`space-y-1.5 text-[12.5px] leading-relaxed ${color}`}>
      {items.map((item, i) => (
        <li key={`${i}-${item.slice(0, 24)}`} className="flex gap-2">
          <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-[var(--color-ink-faint)]" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export interface EvidenceRow {
  claim: string;
  value?: number | null;
  unit?: string;
  source?: string;
  strength?: string;
  significance?: number;
}

export function EvidenceList({ items, compact = false }: { items: EvidenceRow[]; compact?: boolean }) {
  if (!items?.length) return <Empty>No evidence rows returned.</Empty>;
  return (
    <ul className="divide-y divide-[rgba(30,44,65,0.6)]">
      {items.map((item, i) => {
        const tone = item.strength === "strong" ? "ok" : item.strength === "weak" ? "muted" : item.strength === "assumed" ? "assumed" : "info";
        return (
          <li key={`${i}-${item.claim.slice(0, 30)}`} className={compact ? "py-2" : "py-2.5"}>
            <div className="flex items-start justify-between gap-3">
              <span className="text-[12.5px] leading-snug text-[var(--color-ink)]">{item.claim}</span>
              <span className="flex shrink-0 items-center gap-2">
                {item.value !== null && item.value !== undefined && (
                  <span className="mono text-[12.5px] font-semibold text-[var(--color-accent)]">
                    {num(item.value, 3)}
                    {item.unit ? <span className="ml-1 text-[11px] font-normal text-[var(--color-ink-faint)]">{item.unit}</span> : null}
                  </span>
                )}
                {item.strength && <Badge tone={tone as any}>{item.strength}</Badge>}
              </span>
            </div>
            {item.source && <div className="mt-1 text-[11.5px] leading-snug text-[var(--color-ink-faint)]">{item.source}</div>}
          </li>
        );
      })}
    </ul>
  );
}

export function Meter({ value, max = 1, tone }: { value: number | null | undefined; max?: number; tone?: string }) {
  if (value === null || value === undefined) {
    return <div className="h-1.5 w-full rounded-full bg-[rgba(148,163,184,0.14)]" title="not available" />;
  }
  const ratio = Math.max(0, Math.min(1, value / max));
  const color =
    tone === "bad"
      ? "bg-[var(--color-bad)]"
      : tone === "warn"
        ? "bg-[var(--color-warn)]"
        : tone === "ok"
          ? "bg-[var(--color-ok)]"
          : ratio >= 0.9
            ? "bg-[var(--color-bad)]"
            : ratio >= 0.75
              ? "bg-[var(--color-warn)]"
              : "bg-[var(--color-accent)]";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(148,163,184,0.14)]">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${ratio * 100}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Human in the loop
// ---------------------------------------------------------------------------
export function FeedbackBar({
  findingId,
  findingKind,
  findingTitle,
  payload,
  onDone,
}: {
  findingId: string;
  findingKind: string;
  findingTitle: string;
  payload: unknown;
  onDone?: () => void;
}) {
  const [note, setNote] = useState("");
  const [engineer, setEngineer] = useState("engineer@plant");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");

  const submit = async (decision: "confirmed" | "rejected" | "needs_review") => {
    setState("sending");
    try {
      const res = await api.feedback({
        finding_id: findingId,
        finding_kind: findingKind,
        finding_title: findingTitle,
        decision,
        note,
        engineer,
        payload: payload as Record<string, unknown>,
      });
      setState("sent");
      setMessage(res.note ?? "Recorded.");
      onDone?.();
    } catch (err) {
      setState("error");
      setMessage((err as Error).message);
    }
  };

  return (
    <div className="panel-flat mt-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.09em] text-[var(--color-ink-faint)]">
          Engineer review
        </span>
        <input
          className="field mono ml-auto max-w-[190px]"
          value={engineer}
          onChange={(e) => setEngineer(e.target.value)}
          placeholder="engineer id"
          aria-label="engineer identifier"
        />
      </div>
      <textarea
        className="field mt-2 h-[52px] resize-y"
        placeholder="Optional note (why you agree, what you know from the line, what to check next)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button className="btn btn-ok" disabled={state === "sending"} onClick={() => submit("confirmed")}>
          ✓ Confirm
        </button>
        <button className="btn btn-bad" disabled={state === "sending"} onClick={() => submit("rejected")}>
          ✕ Reject
        </button>
        <button className="btn btn-warn" disabled={state === "sending"} onClick={() => submit("needs_review")}>
          ⚠ Needs review
        </button>
        {message && (
          <span className={`text-[11.5px] ${state === "error" ? "text-[var(--color-bad)]" : "text-[var(--color-ok)]"}`}>{message}</span>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-snug text-[var(--color-ink-faint)]">
        Decisions are stored with the exact evidence payload you were shown. This build does not retrain any model from
        feedback, and does not claim to.
      </p>
    </div>
  );
}

export function KeyValue({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[minmax(120px,auto)_1fr] gap-x-4 gap-y-1.5 text-[12.5px]">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-[var(--color-ink-faint)]">{key}</dt>
          <dd className="mono text-[var(--color-ink)]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function UtilBar({ value, capacity }: { value?: number | null; capacity?: number | null }) {
  return (
    <div className="min-w-[110px]">
      <div className="mono mb-1 flex items-baseline justify-between text-[12px]">
        <span>{value === null || value === undefined ? "—" : pct(value * 100, 1)}</span>
        {capacity ? <span className="text-[10.5px] text-[var(--color-ink-faint)]">cap {capacity}</span> : null}
      </div>
      <Meter value={value} />
    </div>
  );
}
