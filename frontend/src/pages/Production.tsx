import { UtilisationChart } from "../components/charts";
import { ErrorBox, Spinner, UtilBar } from "../components/ui";
import { Term } from "../components/Glossary";
import { api, type StationRow } from "../lib/api";
import { int, num, useApi } from "../lib/hooks";

export default function Production() {
  const snapshot = useApi(() => api.productionSnapshot(), []);
  const anomalies = useApi(() => api.anomalies(undefined, 5), []);

  const snap = snapshot.data;
  const stations: StationRow[] = snap?.stations ?? [];
  const measured = stations.filter((s) => s.utilization !== null && s.utilization !== undefined);
  const topAnomaly = anomalies.data?.top?.[0];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-[var(--color-edge)] pb-4">
        <div>
          <h1 className="text-[24px] font-bold tracking-tight text-[var(--color-ink)]">Factory Analysis</h1>
          <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
            Health and performance overview of the production line.
          </p>
        </div>
      </header>

      {snapshot.loading ? (
        <div className="flex justify-center py-12"><Spinner label="Loading factory data..." /></div>
      ) : snapshot.error ? (
        <ErrorBox message={snapshot.error} onRetry={snapshot.reload} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
          {/* Main Content Area */}
          <div className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="panel p-5 bg-[var(--color-surface)] border-l-4 border-l-[var(--color-accent)]">
                <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Throughput</div>
                <div className="text-[24px] font-bold text-[var(--color-ink)]">{snap?.throughput_total ? int(snap.throughput_total) : "—"}</div>
                <div className="text-[12px] text-[var(--color-ink-dim)]">parts / run</div>
              </div>
              <div className="panel p-5 bg-[var(--color-surface)] border-l-4 border-l-[var(--color-warn)]">
                <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Parts Waiting (WIP)</div>
                <div className="text-[24px] font-bold text-[var(--color-ink)]">{num(snap?.wip_total ?? 0, 1)}</div>
                <div className="text-[12px] text-[var(--color-ink-dim)]">parts</div>
              </div>
            </div>

            <div className="panel p-0 overflow-hidden">
              <div className="panel-head border-b border-[var(--color-edge)]">
                <h3 className="panel-title flex items-center gap-2">
                  <span className="grid place-items-center w-5 h-5 rounded bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[12px]">📊</span>
                  Station Utilisation
                </h3>
              </div>
              <div className="p-4 h-[240px]">
                <UtilisationChart
                  data={measured.map((s) => ({ label: s.label, utilization: s.utilization ?? null, rank: s.rank }))}
                />
              </div>
            </div>

            <div className="panel p-0 overflow-hidden">
              <div className="panel-head border-b border-[var(--color-edge)]">
                <h3 className="panel-title flex items-center gap-2">
                  <span className="grid place-items-center w-5 h-5 rounded bg-[var(--color-warn-soft)] text-[var(--color-warn)] text-[12px]">🚧</span>
                  Top Bottlenecks
                </h3>
              </div>
              <div className="overflow-x-auto">
                <table className="table w-full text-left text-[13px]">
                  <thead className="bg-[var(--color-hull)]">
                    <tr>
                      <th className="py-2.5 px-4 font-semibold text-[var(--color-ink-faint)]">Station</th>
                      <th className="py-2.5 px-4 font-semibold text-[var(--color-ink-faint)]"><Term name="utilization">Busy Level</Term></th>
                      <th className="py-2.5 px-4 font-semibold text-[var(--color-ink-faint)]">Signals</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-edge)]">
                    {stations.slice(0, 5).map((station) => (
                      <tr key={station.station} className="hover:bg-[var(--color-hull)] transition-colors">
                        <td className="py-2.5 px-4 font-medium text-[var(--color-ink)]">{station.label}</td>
                        <td className="py-2.5 px-4 w-1/3">
                          <UtilBar value={station.utilization} />
                        </td>
                        <td className="py-2.5 px-4 max-w-[200px]">
                          <div className="flex flex-wrap gap-1">
                            {(station.utilization ?? 0) > 0.8 && <span className="chip chip-warn text-[10px] py-0.5 px-1.5 rounded-sm">Very busy</span>}
                            {(station.wip_evidence ?? 0) > 5 && <span className="chip chip-warn text-[10px] py-0.5 px-1.5 rounded-sm">Queue building</span>}
                            {(station.capacity ?? 0) < 3 && <span className="chip chip-info text-[10px] py-0.5 px-1.5 rounded-sm">Limited cap</span>}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="bg-[var(--color-hull)] p-3 text-[11px] text-[var(--color-ink-dim)] border-t border-[var(--color-edge)]">
                <strong>Methodology:</strong> Ranked by a composite score heavily weighting <Term name="utilization">utilisation</Term> (80%). This is an arithmetic ranking, not an AI prediction.
              </div>
            </div>
          </div>

          {/* Sidebar Area */}
          <div className="space-y-6">
            <div className="panel p-5 bg-[var(--color-hull)] border border-[var(--color-edge)]">
              <h3 className="panel-title flex items-center gap-2 mb-4">
                <span className="grid place-items-center w-5 h-5 rounded bg-[var(--color-bad-soft)] text-[var(--color-bad)] text-[12px]">⚠️</span>
                Unusual Activity
              </h3>
              
              {anomalies.loading ? (
                <div className="flex justify-center py-4"><Spinner /></div>
              ) : !topAnomaly ? (
                <div className="text-[13px] text-[var(--color-ok)] flex items-center gap-2 bg-[var(--color-ok-soft)] p-3 rounded-md">
                  <span>✅</span> All production runs match normal patterns.
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="bg-white p-3 rounded-md border border-[var(--color-edge-strong)] shadow-sm">
                    <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Most Unusual Run</div>
                    <div className="text-[18px] font-bold text-[var(--color-bad)]">#{topAnomaly.run_index}</div>
                  </div>
                  
                  <div>
                    <div className="text-[12.5px] font-semibold text-[var(--color-ink)] mb-2">Key Drivers</div>
                    <ul className="space-y-2">
                      {topAnomaly.drivers.slice(0, 3).map((d: any, idx: number) => (
                        <li key={idx} className="flex items-center gap-2 text-[12px] bg-white p-2 rounded border border-[var(--color-edge)]">
                          <span className={`w-2 h-2 rounded-full ${d.z > 0 ? 'bg-[var(--color-warn)]' : 'bg-[var(--color-accent)]'}`}></span>
                          <span className="font-medium">{d.feature}</span>
                          <span className="text-[var(--color-ink-dim)] ml-auto">{d.z > 0 ? "High" : "Low"}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <details className="mt-4 text-[11.5px] text-[var(--color-ink-dim)] cursor-pointer">
                    <summary className="font-semibold outline-none">How this works</summary>
                    <div className="mt-2 space-y-1.5 p-2 bg-white rounded border border-[var(--color-edge)]">
                      <p><strong>PCA + Mahalanobis Distance</strong> flags the top 0.5% most distant runs compared to normal patterns.</p>
                    </div>
                  </details>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
