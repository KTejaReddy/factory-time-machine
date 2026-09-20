import type { ForensicCase } from "../lib/api";
import { num, pct, signed } from "../lib/hooks";
import { Badge, severityTone } from "./ui";

type Event = ForensicCase["timeline"][number];

/**
 * Route-ordered divergence profile.
 *
 * The dataset exports no timestamps, so this is explicitly an ordering along the
 * documented process route. The header says so every time it is rendered.
 */
export default function Timeline({ events, firstStation }: { events: Event[]; firstStation?: string | null }) {
  const magnitudes = events.map((e) => Math.abs(e.z ?? 0));
  const max = Math.max(1, ...magnitudes);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11.5px] text-[var(--color-ink-faint)]">
        <Badge tone="warn">route order</Badge>
        <span>
          Ordered along the documented process route (Blanking → Forklift → Pressing → Cells → Paint → Quality → Storage).
          The export contains no timestamps, so this is not clock order.
        </span>
      </div>
      <ol className="rail space-y-2.5">
        {events.map((event) => {
          const isFirst = event.station === firstStation;
          const magnitude = Math.abs(event.z ?? 0);
          const width = max ? Math.max(2, (magnitude / max) * 100) : 0;
          const tone = severityTone(event.severity);
          const barColor =
            tone === "bad" ? "bg-[var(--color-bad)]" : tone === "warn" ? "bg-[var(--color-warn)]" : "bg-[var(--color-accent)]";
          return (
            <li key={event.station} className="relative pl-10">
              <span
                className={`absolute left-[9px] top-3 grid h-3.5 w-3.5 place-items-center rounded-full border-2 ${
                  isFirst ? "border-[var(--color-bad)] bg-[var(--color-bad)]" : "border-[var(--color-edge)] bg-[var(--color-hull)]"
                }`}
                title={isFirst ? "first divergence along the route" : event.severity}
              />
              <div className={`panel-flat px-3 py-2.5 ${isFirst ? "border-[rgba(251,113,133,0.45)]" : ""}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-semibold">{event.label}</span>
                  <Badge tone="muted">{event.stage}</Badge>
                  {isFirst && <Badge tone="bad">first divergence</Badge>}
                  {event.metric === "not exported" ? (
                    <Badge tone="muted">not measured</Badge>
                  ) : (
                    <Badge tone={tone}>{event.severity}</Badge>
                  )}
                  <span className="mono ml-auto text-[12px] text-[var(--color-ink-dim)]">
                    {event.z === null || event.z === undefined ? "—" : signed(event.z, 2)}
                    <span className="ml-1 text-[10.5px] text-[var(--color-ink-faint)]">
                      {event.metric === "not exported" ? "" : event.metric}
                    </span>
                  </span>
                </div>

                {event.metric !== "not exported" && (
                  <div className="mt-2 mb-1 h-1.5 w-full overflow-hidden rounded-full bg-[rgba(148,163,184,0.12)]">
                    <div className={`h-full rounded-full ${barColor}`} style={{ width: `${width}%` }} />
                  </div>
                )}

                <div className="mono flex flex-wrap gap-x-4 gap-y-0.5 text-[11.5px] text-[var(--color-ink-faint)]">
                  <span>
                    value {event.value === null || event.value === undefined ? "—" : num(event.value, 4)}
                    {event.metric.includes("queue") ? " (log1p)" : ""}
                  </span>
                  <span>baseline {event.baseline === null || event.baseline === undefined ? "—" : num(event.baseline, 4)}</span>
                  {event.delta_pct !== null && event.delta_pct !== undefined && <span>Δ {pct(event.delta_pct, 1)}</span>}
                  {event.capacity ? <span>capacity {event.capacity}</span> : null}
                </div>

                {event.evidence?.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5 text-[11.5px] leading-snug text-[var(--color-ink-dim)]">
                    {event.evidence.slice(0, 3).map((line, i) => (
                      <li key={i} className="flex gap-2">
                        <span className="text-[var(--color-ink-faint)]">›</span>
                        <span>{line}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
