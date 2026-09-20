import { useEffect, useState } from "react";
import { api, getActiveDataset, type EconomicAssessment } from "../lib/api";
import { int, num, useApi } from "../lib/hooks";
import { Badge, Bullets, Card, Empty, ErrorBox, KeyValue, Spinner } from "../components/ui";
import { RunAnalysisButton, formatDate } from "../components/workspace";

const FIELDS: Array<{ key: string; label: string; hint: string }> = [
  { key: "margin_per_unit", label: "Unit value / margin", hint: "currency per finished unit" },
  { key: "cost_per_unit_scrapped", label: "Scrap cost", hint: "currency per scrapped unit" },
  { key: "rework_cost_per_unit", label: "Rework cost", hint: "currency per reworked unit" },
  { key: "downtime_cost_per_hour", label: "Downtime cost", hint: "currency per downtime hour" },
  { key: "holding_cost_per_unit_hour", label: "Holding cost", hint: "currency per part per hour" },
  { key: "intervention_cost_fixed", label: "Intervention cost (process change)", hint: "one-off, used by the repair engine" },
  { key: "intervention_cost_per_capacity_unit", label: "Intervention cost (per extra resource)", hint: "one-off, used by the repair engine" },
];

type Rates = Record<string, string>;

const emptyRates: Rates = Object.fromEntries([
  ["currency", "INR"],
  ...FIELDS.map((f) => [f.key, ""]),
]);

export default function Economics() {
  const key = getActiveDataset();
  const context = useApi(() => api.workspaceDataset(key), [key]);
  const assessment = useApi(() => api.economics(key), [key]);
  const registry = useApi(() => api.workspaceDatasets(), []);
  const [rates, setRates] = useState<Rates>(emptyRates);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EconomicAssessment | null>(null);
  const [copyFrom, setCopyFrom] = useState("");

  useEffect(() => {
    const stored = context.data?.rate_card?.rates;
    if (stored) {
      setRates({
        currency: stored.currency ?? "INR",
        ...Object.fromEntries(FIELDS.map((f) => [f.key, stored[f.key] === null || stored[f.key] === undefined ? "" : String(stored[f.key])])),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context.data?.rate_card?.updated_at, key]);

  if (!key) return <Empty>No dataset selected. Upload a dataset first.</Empty>;
  if (context.loading && !context.data) return <Spinner label="Loading dataset…" />;
  if (context.error) return <ErrorBox message={context.error} onRetry={context.reload} />;

  const record = context.data?.dataset;
  const detected = record?.capabilities?.detected;
  const costColumns = detected?.cost_fields ?? [];
  const storedAt = context.data?.rate_card?.updated_at;

  const payload = () => {
    const body: Record<string, any> = { currency: rates.currency || "INR" };
    FIELDS.forEach((f) => {
      if (rates[f.key] !== "") body[f.key] = Number(rates[f.key]);
    });
    return body;
  };

  const save = async () => {
    setError(null);
    setMessage(null);
    try {
      await api.saveRateCard(key, payload());
      setMessage("Rate card saved against this dataset only.");
      context.reload();
      const fresh = await api.economicsWithRates(key, payload());
      setResult(fresh);
      assessment.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const clear = async () => {
    setError(null);
    setMessage(null);
    try {
      await api.deleteRateCard(key);
      setRates(emptyRates);
      setResult(null);
      setMessage("Rate card removed for this dataset.");
      context.reload();
      assessment.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const copyRates = async () => {
    if (!copyFrom) return;
    setError(null);
    try {
      const other = await api.rateCard(copyFrom);
      const stored = other.rate_card ?? {};
      setRates({
        currency: stored.currency ?? "INR",
        ...Object.fromEntries(FIELDS.map((f) => [f.key, stored[f.key] === null || stored[f.key] === undefined ? "" : String(stored[f.key])])),
      });
      setMessage(`Rates copied from ${copyFrom}. They are not saved here until you press Save.`);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const shown = result ?? assessment.data;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Economics — {record?.name}</h1>
          <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            Quantities always come from this dataset. Rates come from this dataset's own cost columns where they exist,
            and otherwise from the rate card stored against this dataset. A rate card is never applied to another
            dataset silently.
          </p>
        </div>
        <RunAnalysisButton label="Re-run analysis" />
      </header>

      {costColumns.length > 0 ? (
        <Card title="Cost information detected inside this dataset" subtitle="Used automatically; no rate card needed for these lines">
          <div className="flex flex-wrap gap-1.5">
            {costColumns.map((col) => (
              <Badge key={col} tone="ok">
                {col}
              </Badge>
            ))}
          </div>
        </Card>
      ) : (
        <Card title="No cost column detected in this dataset" subtitle="Case B: a rate card is required before any monetary figure can be shown">
          <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            The dataset carries no price, cost or revenue column, so the assessment below can only be computed once you
            supply rates. Nothing is guessed on your behalf.
          </p>
        </Card>
      )}

      <Card
        title="Rate card for this dataset"
        subtitle={storedAt ? `stored ${formatDate(storedAt)}` : "not stored yet for this dataset"}
        actions={
          <div className="flex items-center gap-2">
            <select className="field !w-auto !py-1 text-[12px]" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
              <option value="">copy rates from…</option>
              {(registry.data?.datasets ?? [])
                .filter((d) => d.key !== key)
                .map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.name}
                  </option>
                ))}
            </select>
            <button className="btn" disabled={!copyFrom} onClick={copyRates}>
              Copy
            </button>
          </div>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block">
            <span className="eyebrow">Currency</span>
            <input className="field mt-1" value={rates.currency} onChange={(e) => setRates({ ...rates, currency: e.target.value })} />
          </label>
          {FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className="eyebrow">{field.label}</span>
              <input
                className="field mt-1 mono"
                inputMode="decimal"
                placeholder="—"
                value={rates[field.key]}
                onChange={(e) => setRates({ ...rates, [field.key]: e.target.value })}
              />
              <span className="mt-0.5 block text-[10.5px] text-[var(--color-ink-faint)]">{field.hint}</span>
            </label>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button className="btn btn-primary" onClick={save}>
            Save rate card
          </button>
          <button className="btn" onClick={clear} disabled={!storedAt}>
            Remove stored card
          </button>
          <span className="text-[11.5px] text-[var(--color-ink-faint)]">
            Saved per dataset ({key}). Leave a field empty to keep that line unevaluated.
          </span>
        </div>
        {message && <div className="mt-3 rounded-md border border-[var(--color-ok-edge)] bg-[var(--color-ok-soft)] px-3 py-2 text-[12px] text-[var(--color-ok)]">{message}</div>}
        {error && <div className="mt-3"><ErrorBox message={error} /></div>}
      </Card>

      <Card
        title="Assessment"
        subtitle={shown?.available ? `${shown.currency ?? ""} ${int(shown.total ?? 0)} over the documented horizon` : "no monetary figure yet"}
        actions={<Badge tone={shown?.available ? "ok" : "warn"}>{shown?.available ? "computed" : "unavailable"}</Badge>}
      >
        {assessment.loading && !assessment.data ? (
          <Spinner />
        ) : (
          <>
            <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">{shown?.reason}</p>
            {shown?.lines && shown.lines.length > 0 && (
              <div className="mt-3 overflow-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>line</th>
                      <th>quantity</th>
                      <th>unit</th>
                      <th>rate</th>
                      <th>amount</th>
                      <th>quantity source</th>
                      <th>rate source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.lines.map((line, i) => (
                      <tr key={i}>
                        <td>{line.label}</td>
                        <td className="mono">{line.quantity === null || line.quantity === undefined ? "—" : num(line.quantity, 3)}</td>
                        <td className="text-[11px] text-[var(--color-ink-faint)]">{line.unit}</td>
                        <td className="mono">{line.rate === null || line.rate === undefined ? "—" : num(line.rate, 2)}</td>
                        <td className="mono">{line.amount === null || line.amount === undefined ? "—" : num(line.amount, 2)}</td>
                        <td className="text-[11px] text-[var(--color-ink-dim)]">{line.quantity_source}</td>
                        <td className="text-[11px] text-[var(--color-ink-dim)]">{line.rate_source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {shown?.missing_variables && shown.missing_variables.length > 0 && (
              <div className="mt-3">
                <div className="eyebrow mb-1">Still missing for a complete picture</div>
                <Bullets items={shown.missing_variables} tone="faint" />
              </div>
            )}
            {shown && (
              <div className="mt-3">
                <KeyValue
                  rows={[
                    ["Dataset-supplied quantities", (shown.dataset_supplied_values ?? []).join(", ") || "none detected"],
                    ["Disclaimer", shown.disclaimer],
                  ]}
                />
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
