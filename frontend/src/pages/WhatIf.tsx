import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { api, getActiveDataset, type ScenarioResult } from "../lib/api";
import { int, money, pct, signed, useApi } from "../lib/hooks";
import { formatDate } from "../components/workspace";

type Kind = "capacity_change" | "processing_time_change";

export default function WhatIf() {
  const key = getActiveDataset();
  const options = useApi(() => api.simulationOptions(key), [key]);
  const history = useApi(() => api.simulationHistory(key, 20), [key]);
  const dataset = useApi(() => api.workspaceDataset(key), [key]);

  const [kind, setKind] = useState<Kind>("capacity_change");
  const [station, setStation] = useState("");
  const [capacityDelta, setCapacityDelta] = useState(1);
  const [timeFactor, setTimeFactor] = useState(0.9);

  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const offered = options.data?.stations ?? [];
  const current = offered.find((s: any) => s.key === station) ?? offered[0];

  // Default to the dataset's leading constraint, not a fixed station name.
  useEffect(() => {
    if (!station && offered.length) setStation(offered[0].key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offered.length, key]);

  if (!key) return <Empty>No dataset selected. Upload a dataset first.</Empty>;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const spec = {
        model_key: key,
        name: kind === "capacity_change" ? `${current?.label ?? station} capacity ${capacityDelta >= 0 ? "+" : ""}${capacityDelta}` : `${current?.label ?? station} cycle time ×${timeFactor}`,
        kind,
        station: current?.key ?? station,
        capacity_delta: kind === "capacity_change" ? capacityDelta : 0,
        time_factor: kind === "processing_time_change" ? timeFactor : 1,
        demand_factor: 1,
        replications: 1,
      };
      const res = await api.runScenario(spec, null, "anonymous", key);
      setResult(res);
      history.reload();
    } catch (err) {
      setError((err as Error).message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const reopen = async (runId: number, scenario: Record<string, any>) => {
    setError(null);
    try {
      if (scenario?.kind === "capacity_change" || scenario?.kind === "processing_time_change") {
        setKind(scenario.kind);
        if (scenario.station) setStation(scenario.station);
        setCapacityDelta(scenario.capacity_delta ?? 1);
        setTimeFactor(scenario.time_factor ?? 0.9);
      }
      // Re-run the stored scenario so the comparison is shown again (with a new
      // saved id), instead of displaying a stale table next to a new header.
      const spec = { ...scenario, model_key: key, name: `${scenario?.name ?? "scenario"} (reopened #${runId})` };
      const res = await api.runScenario(spec, null, "anonymous", key);
      setResult(res);
      history.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (options.loading && !options.data) return <Spinner label="Checking what this dataset supports…" />;
  if (options.error) return <ErrorBox message={options.error} onRetry={options.reload} />;

  if (options.data && options.data.available === false) {
    return (
      <div className="max-w-4xl space-y-4">
        <header>
          <h1 className="text-[19px] font-semibold tracking-tight">What-If — {dataset.data?.dataset?.name ?? key}</h1>
        </header>
        <Card title="Simulation unavailable for this dataset" subtitle="The engine is not silently re-used from another dataset">
          <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">{options.data.reason}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link className="btn" to="/repairs">
              See supported repairs instead
            </Link>
            <Link className="btn" to="/datasets">
              Switch dataset
            </Link>
          </div>
        </Card>
        {(history.data ?? []).length > 0 && (
          <Card title="Saved scenarios for this dataset" subtitle="Stored from earlier runs">
            <ul className="space-y-1.5 text-[12px] text-[var(--color-ink-dim)]">
              {(history.data ?? []).map((run: any) => (
                <li key={run.id} className="flex items-center justify-between gap-2">
                  <span>{run.name}</span>
                  <span className="mono text-[10.5px] text-[var(--color-ink-faint)]">{formatDate(run.created_at)}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    );
  }

  const comparisons = result?.comparisons ?? [];
  const utilRow = comparisons.find((r) => r.metric === "utilization" && r.station === current?.key);
  const queueRow = comparisons.find((r) => r.metric === "queue_mean" && r.station === current?.key);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">What-If — {dataset.data?.dataset?.name ?? key}</h1>
          <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            Scenarios run against this dataset's model. Every run is stored under this dataset's case file, so a
            scenario run for another dataset never appears in this history.
          </p>
        </div>
        {result?.saved_run_id && <Badge tone="ok">saved as run #{result.saved_run_id}</Badge>}
      </header>

      <div className="grid gap-6 md:grid-cols-[320px_1fr]">
        <div className="panel sticky top-6 h-fit space-y-5 border border-[var(--color-edge)] bg-white p-6 shadow-sm">
          <h2 className="text-[15px] font-bold text-[var(--color-ink)]">Configuration</h2>
          <div className="space-y-4">
            <div>
              <label className="eyebrow mb-1.5 block">Target station</label>
              <select className="field w-full text-[13px]" value={current?.key ?? ""} onChange={(e) => setStation(e.target.value)}>
                {offered.map((s: any) => (
                  <option key={s.key} value={s.key}>
                    {s.label} (capacity {s.capacity})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="eyebrow mb-1.5 block">Action</label>
              <select className="field w-full text-[13px]" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
                <option value="capacity_change">Change capacity</option>
                <option value="processing_time_change">Change processing time</option>
              </select>
            </div>

            <div>
              <label className="eyebrow mb-1.5 block">Amount</label>
              {kind === "capacity_change" ? (
                <input
                  className="field mono w-full text-[14px]"
                  type="number"
                  min={-3}
                  max={8}
                  value={capacityDelta}
                  onChange={(e) => setCapacityDelta(Number(e.target.value))}
                />
              ) : (
                <div className="flex items-center gap-3">
                  <input className="field w-full" type="range" min={0.5} max={1.5} step={0.05} value={timeFactor} onChange={(e) => setTimeFactor(Number(e.target.value))} />
                  <span className="mono w-12 text-right text-[14px]">{timeFactor}x</span>
                </div>
              )}
            </div>

            <button className="btn btn-primary mt-4 w-full font-semibold shadow-sm" onClick={run} disabled={busy || !current}>
              {busy ? "Running simulation…" : "Run simulation"}
            </button>

            {(options.data?.not_adjustable ?? []).length > 0 && (
              <details className="text-[11px] text-[var(--color-ink-faint)]">
                <summary>{(options.data?.not_adjustable ?? []).length} stations cannot be changed</summary>
                <ul className="mt-1 space-y-1">
                  {(options.data?.not_adjustable ?? []).map((s: any) => (
                    <li key={s.key}>
                      {s.label}: {s.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <p className="border-t border-[var(--color-edge)] pt-4 text-[11.5px] leading-relaxed text-[var(--color-ink-faint)]">
              {options.data?.documented_processing_times_note ?? "The engine re-simulates the documented route."} Results
              are advisory estimates.
            </p>

            {error && <div className="callout callout-warn text-[12px]">{error}</div>}
          </div>
        </div>

        <div className="space-y-6">
          {busy ? (
            <Card title="Running simulation">
              <Spinner label="Calculating what happens…" />
            </Card>
          ) : !result ? (
            <Card title="Results">
              <Empty>Select a change and run the simulation to see before/after results.</Empty>
            </Card>
          ) : (
            <div className="panel fade-in border border-[var(--color-edge)] bg-white p-6 shadow-sm">
              <h2 className="mb-6 flex items-center gap-2 text-[15px] font-bold text-[var(--color-ink)]">
                <span className="grid h-6 w-6 place-items-center rounded bg-[var(--color-accent-soft)] text-[12px] text-[var(--color-accent)]">📊</span>
                Baseline vs simulated — {result.scenario?.name}
              </h2>
              <div className="grid gap-6">
                <div className="grid grid-cols-[1fr_2fr] items-center gap-4 border-b border-[var(--color-edge)] pb-5">
                  <div className="text-[13px] font-semibold text-[var(--color-ink-dim)]">Parts completed</div>
                  <div className="flex items-center justify-between rounded-md bg-[var(--color-hull)] p-3">
                    <div className="w-1/3 text-center">
                      <div className="eyebrow mb-1">Baseline</div>
                      <div className="mono text-[17px]">{int(result.summary.baseline_completed_parts ?? 0)}</div>
                    </div>
                    <div className="text-[18px] text-[var(--color-ink-faint)]">→</div>
                    <div className="w-1/3 text-center">
                      <div className="eyebrow mb-1 text-[var(--color-accent)]">Simulated</div>
                      <div className={`mono text-[17px] font-bold ${(result.summary.throughput_delta_parts ?? 0) < 0 ? "text-[var(--color-bad)]" : "text-[var(--color-ok)]"}`}>
                        {int(result.summary.completed_parts ?? 0)}
                      </div>
                    </div>
                    <div className="w-1/4 text-right">
                      <span className={`chip ${(result.summary.throughput_delta_parts ?? 0) < 0 ? "chip-bad" : "chip-ok"}`}>
                        {signed(result.summary.throughput_delta_parts ?? 0, 0)} net
                      </span>
                    </div>
                  </div>
                </div>

                {utilRow && (
                  <div className="grid grid-cols-[1fr_2fr] items-center gap-4 border-b border-[var(--color-edge)] pb-5">
                    <div className="text-[13px] font-semibold text-[var(--color-ink-dim)]">Station utilisation</div>
                    <div className="flex items-center justify-between rounded-md bg-[var(--color-hull)] p-3">
                      <div className="w-1/3 text-center">
                        <div className="eyebrow mb-1">Baseline</div>
                        <div className="mono text-[17px]">{pct((utilRow.current ?? 0) * 100, 1)}</div>
                      </div>
                      <div className="text-[18px] text-[var(--color-ink-faint)]">→</div>
                      <div className="w-1/3 text-center">
                        <div className="eyebrow mb-1 text-[var(--color-accent)]">Simulated</div>
                        <div className={`mono text-[17px] font-bold ${(utilRow.delta ?? 0) < 0 ? "text-[var(--color-ok)]" : "text-[var(--color-warn)]"}`}>
                          {pct((utilRow.simulated ?? 0) * 100, 1)}
                        </div>
                      </div>
                      <div className="w-1/4 text-right text-[12px] font-medium text-[var(--color-ink-dim)]">
                        {signed((utilRow.delta ?? 0) * 100, 1)}% net
                      </div>
                    </div>
                    <div className="col-span-2 text-[11px] text-[var(--color-ink-faint)]">{utilRow.comparison_basis}</div>
                  </div>
                )}

                {queueRow && (
                  <div className="grid grid-cols-[1fr_2fr] items-center gap-4">
                    <div className="text-[13px] font-semibold text-[var(--color-ink-dim)]">Station queue</div>
                    <div className="flex items-center justify-between rounded-md bg-[var(--color-hull)] p-3">
                      <div className="w-1/3 text-center">
                        <div className="eyebrow mb-1">Baseline</div>
                        <div className="mono text-[17px]">{int(queueRow.current ?? 0)}</div>
                      </div>
                      <div className="text-[18px] text-[var(--color-ink-faint)]">→</div>
                      <div className="w-1/3 text-center">
                        <div className="eyebrow mb-1 text-[var(--color-accent)]">Simulated</div>
                        <div className={`mono text-[17px] font-bold ${(queueRow.delta ?? 0) < 0 ? "text-[var(--color-ok)]" : "text-[var(--color-warn)]"}`}>
                          {int(queueRow.simulated ?? 0)}
                        </div>
                      </div>
                      <div className="w-1/4 text-right text-[12px] font-medium text-[var(--color-ink-dim)]">
                        {signed(queueRow.delta ?? 0, 1)} net
                      </div>
                    </div>
                    <div className="col-span-2 text-[11px] text-[var(--color-ink-faint)]">{queueRow.comparison_basis_note}</div>
                  </div>
                )}
              </div>

              <div className="mt-6 rounded-md border border-[var(--color-edge)] bg-[var(--color-hull)] p-5 text-center">
                <div className="eyebrow mb-2">Economic impact of this scenario</div>
                {!result.economics.available ? (
                  <p className="text-[12.5px] italic text-[var(--color-ink-dim)]">
                    {result.economics.reason}{" "}
                    <Link className="link" to="/economics">
                      Supply a rate card for this dataset
                    </Link>
                    .
                  </p>
                ) : (
                  <span className="mono text-[22px] font-bold text-[var(--color-ok)]">
                    {money(result.economics.total ?? null, result.economics.currency ?? "USD")}
                  </span>
                )}
                <div className="mt-2 text-[11px] text-[var(--color-ink-faint)]">{result.validation?.verdict}</div>
              </div>
            </div>
          )}

          <Card
            title="Scenario history for this dataset"
            subtitle={`${(history.data ?? []).length} saved run(s) — reopening one re-runs its parameters`}
            actions={
              <button className="btn" onClick={history.reload}>
                Refresh
              </button>
            }
          >
            {(history.data ?? []).length === 0 ? (
              <Empty>No scenario stored for this dataset yet.</Empty>
            ) : (
              <div className="overflow-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>scenario</th>
                      <th>saved</th>
                      <th>completed parts</th>
                      <th>delta</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(history.data ?? []).map((run: any) => (
                      <tr key={run.id}>
                        <td className="mono">{run.id}</td>
                        <td>{run.name}</td>
                        <td className="text-[11.5px] text-[var(--color-ink-dim)]">{formatDate(run.created_at)}</td>
                        <td className="mono">{int(run.result?.summary?.completed_parts ?? 0)}</td>
                        <td className="mono">{signed(run.result?.summary?.throughput_delta_parts ?? 0, 0)}</td>
                        <td>
                          <div className="flex justify-end">
                            <button className="btn" onClick={() => reopen(run.id, run.scenario ?? {})}>
                              Reopen
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
