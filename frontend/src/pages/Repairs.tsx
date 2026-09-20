import { Link } from "react-router-dom";
import { api, getActiveDataset, type RepairCandidate } from "../lib/api";
import { int, num, useApi } from "../lib/hooks";
import { Badge, Bullets, Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { RunAnalysisButton } from "../components/workspace";

const confidenceTone = (value: string) => (value === "high" ? "ok" : value === "medium" ? "warn" : "muted");

function CandidateCard({ candidate, highlight }: { candidate: RepairCandidate; highlight?: boolean }) {
  const loss = candidate.expected_loss_reduction ?? {};
  const cost = candidate.intervention_cost ?? {};
  const net = candidate.net_impact ?? {};
  return (
    <Card
      title={candidate.repair}
      subtitle={`${candidate.kind}${candidate.target?.label ? ` · ${candidate.target.label}` : ""}${candidate.target?.column ? ` · ${candidate.target.column}` : ""}`}
      actions={
        <div className="flex items-center gap-1.5">
          {highlight && <Badge tone="ok">cheapest supported effective repair</Badge>}
          <Badge tone={confidenceTone(candidate.confidence)}>confidence: {candidate.confidence}</Badge>
        </div>
      }
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="panel-flat p-2.5">
          <div className="eyebrow">Expected loss reduction</div>
          <div className="mono mt-1 text-[14px] text-[var(--color-ink)]">
            {loss.amount === null || loss.amount === undefined ? "not valued" : num(loss.amount, 0)}
          </div>
          {loss.per_row_amount !== undefined && loss.per_row_amount !== null && (
            <div className="mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">
              {num(loss.per_row_amount, 2)} per row × {int(loss.observations ?? 0)} rows
            </div>
          )}
          {loss.reason && <div className="mt-0.5 text-[10.5px] text-[var(--color-warn)]">{loss.reason}</div>}
        </div>
        <div className="panel-flat p-2.5">
          <div className="eyebrow">Intervention cost</div>
          <div className="mono mt-1 text-[14px] text-[var(--color-ink)]">
            {cost.amount === null || cost.amount === undefined ? "not stated" : num(cost.amount, 0)}
          </div>
          <div className="mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">{cost.basis ?? cost.reason}</div>
        </div>
        <div className="panel-flat p-2.5">
          <div className="eyebrow">Net impact</div>
          <div className="mono mt-1 text-[14px] text-[var(--color-ink)]">
            {net.amount === null || net.amount === undefined ? "not comparable yet" : num(net.amount, 0)}
          </div>
          <div className="mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">{net.currency ?? ""}</div>
        </div>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div>
          <div className="eyebrow mb-1">Evidence</div>
          <Bullets items={candidate.evidence} tone="dim" />
        </div>
        <div>
          <div className="eyebrow mb-1">Assumptions</div>
          <Bullets items={candidate.assumptions} tone="faint" />
        </div>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div className="text-[11.5px] text-[var(--color-ink-dim)]">
          <span className="eyebrow">Quality effect:</span> {candidate.expected_quality_effect || "not measured"}
        </div>
        <div className="text-[11.5px] text-[var(--color-ink-dim)]">
          <span className="eyebrow">Throughput effect:</span>{" "}
          {candidate.expected_throughput_effect?.available === false
            ? candidate.expected_throughput_effect.reason
            : candidate.expected_throughput_effect?.completed_parts_delta !== undefined
            ? `completed parts ${int(candidate.expected_throughput_effect.completed_parts_delta)} over the horizon (re-simulation)`
            : "—"}
        </div>
      </div>

      {loss.lines && loss.lines.length > 0 && (
        <div className="mt-3 overflow-auto">
          <table className="table">
            <thead>
              <tr>
                <th>basis</th>
                <th>quantity</th>
                <th>unit</th>
                <th>rate</th>
                <th>amount</th>
                <th>rate source</th>
              </tr>
            </thead>
            <tbody>
              {loss.lines.map((line, i) => (
                <tr key={i}>
                  <td>{line.label}</td>
                  <td className="mono">{num(line.quantity, 3)}</td>
                  <td className="text-[11px] text-[var(--color-ink-faint)]">{line.unit}</td>
                  <td className="mono">{num(line.rate, 2)}</td>
                  <td className="mono">{num(line.amount, 2)}</td>
                  <td className="text-[11px] text-[var(--color-ink-dim)]">{line.rate_source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Repairs() {
  const key = getActiveDataset();
  const repairs = useApi(() => api.repairs(key), [key]);

  if (!key) return <Empty>No dataset selected. Upload a dataset first.</Empty>;
  if (repairs.loading && !repairs.data) return <Spinner label="Finding supported repairs…" />;
  if (repairs.error) return <ErrorBox message={repairs.error} onRetry={repairs.reload} />;

  const report = repairs.data;
  const best = report?.cheapest_supported_effective_repair ?? null;
  const needsCard = report?.status === "cost_comparison_unavailable";

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Repairs — what can this dataset change?</h1>
          <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            Candidate interventions are discovered from this dataset's own columns and, where the dataset is covered by
            the documented-route re-simulation, from simulated changes. There is no fixed universal repair list, and no
            price is ever invented: an intervention cost exists only if it is in the dataset's rate card.
          </p>
        </div>
        <RunAnalysisButton label="Re-run analysis" />
      </header>

      <Card
        title="Cheapest supported effective repair"
        subtitle="Best net benefit per unit of intervention cost, among repairs whose effect and cost are both supported"
        actions={<Badge tone={best ? "ok" : needsCard ? "warn" : "muted"}>{report?.status}</Badge>}
      >
        {best ? (
          <div className="space-y-2">
            <div className="text-[16px] font-semibold text-[var(--color-ink)]">{best.repair}</div>
            <div className="text-[12.5px] text-[var(--color-ink-dim)]">
              Net {num(best.net_impact?.amount, 0)} {best.expected_loss_reduction?.currency ?? ""} at a benefit-cost ratio of{" "}
              <span className="mono">{num(best.benefit_cost_ratio, 2)}x</span>. {best.selection_reason}
            </div>
            <Bullets items={best.assumptions} tone="faint" />
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-[13px] text-[var(--color-ink-dim)]">{report?.statement}</p>
            {needsCard && (
              <Link className="btn btn-primary" to="/economics">
                Supply a rate card for this dataset
              </Link>
            )}
          </div>
        )}
      </Card>

      <Card title="How each rate was sourced for this dataset" subtitle={`currency: ${report?.currency ?? "not stated"} (${report?.currency_source ?? "—"})`}>
        {Object.keys(report?.rates ?? {}).length === 0 ? (
          <p className="text-[12px] text-[var(--color-ink-dim)]">
            No rate is available: the dataset has no cost column that matches a lever, and no rate card is stored for it.
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>concept</th>
                <th>value</th>
                <th>source</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(report?.rates ?? {}).map(([concept, entry]) => (
                <tr key={concept}>
                  <td>{concept}</td>
                  <td className="mono">{num(entry.value, 2)}</td>
                  <td className="text-[11.5px] text-[var(--color-ink-dim)]">{entry.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="space-y-3">
        <h2 className="eyebrow">Candidate repairs ({report?.candidates?.length ?? 0})</h2>
        {(report?.candidates ?? []).map((candidate, i) => (
          <CandidateCard key={`${candidate.repair}-${i}`} candidate={candidate} highlight={best?.repair === candidate.repair} />
        ))}
        {(report?.candidates ?? []).length === 0 && (
          <Empty>{report?.statement ?? "No supported repair can be proposed for this dataset."}</Empty>
        )}
      </div>

      <Card title="Method and limits" subtitle="Stated assumptions, not hidden ones">
        <Bullets items={report?.limitations ?? []} tone="faint" />
        <div className="mt-2 text-[11.5px] text-[var(--color-ink-faint)]">
          Assumed reductions:{" "}
          {Object.entries(report?.reductions_assumed ?? {})
            .map(([lever, fraction]) => `${lever} ${Math.round(Number(fraction) * 100)}%`)
            .join(" · ")}
        </div>
      </Card>
    </div>
  );
}
