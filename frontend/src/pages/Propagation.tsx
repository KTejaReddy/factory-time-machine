import PropagationGraph from "../components/PropagationGraph";
import { Badge, Card, ErrorBox, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";

export default function Propagation() {
  const graph = useApi(() => api.propagation(), []);

  return (
    <div className="space-y-6 max-w-7xl">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-bold tracking-tight text-[var(--color-ink)]">Problem Flow</h1>
          <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
            How problems propagate: Process → Problem → Effect → Impact
          </p>
        </div>
        <div className="flex gap-2">
          {graph.data && <Badge tone="info">{graph.data.nodes.length} nodes · {graph.data.edges.length} connections</Badge>}
        </div>
      </header>

      {graph.loading ? (
        <Spinner label="Loading the problem flow..." />
      ) : graph.error ? (
        <ErrorBox message={graph.error} onRetry={graph.reload} />
      ) : graph.data ? (
        <div className="space-y-6">
          <Card title="HOW THE PROBLEM FLOWS" subtitle="Process → Problem → Effect → Impact">
            {/* The chain is derived from the graph, never a hard-coded story: an
                uploaded dataset has no documented route, so it shows measured
                stations and no fabricated order between them. */}
            <div className="flex flex-wrap items-center justify-center gap-3 rounded-md border border-[var(--color-edge)] bg-[var(--color-surface)] py-4 text-[13px] font-medium text-[var(--color-ink-dim)]">
              {(graph.data.nodes ?? []).slice(0, 8).map((node: any, index: number) => (
                <span key={node.id} className="flex items-center gap-3">
                  {index > 0 && <span className="text-[var(--color-ink-faint)]">→</span>}
                  <span className={node.kind === "outcome" ? "text-[var(--color-bad)]" : undefined}>{node.label}</span>
                </span>
              ))}
            </div>
            {(graph.data.nodes ?? []).length === 0 && (
              <p className="text-[13px] text-[var(--color-ink-dim)]">
                {graph.data.linkage_notice || "No propagation path can be established from the available data."}
              </p>
            )}

            <div className="mt-6">
              <PropagationGraph
                nodes={graph.data.nodes}
                edges={graph.data.edges}
                legend={graph.data.legend}
                notice={graph.data.linkage_notice}
              />
            </div>
          </Card>

          <Card title="What We Don't Know">
            <ul className="list-disc list-inside text-[13px] text-[var(--color-ink-dim)] space-y-1">
              {graph.data.limitations?.map((limitation: string, idx: number) => (
                <li key={idx}>{limitation}</li>
              ))}
            </ul>
            <details className="mt-4 cursor-pointer border-t border-[var(--color-edge)] pt-4 text-[13px] text-[var(--color-ink-dim)]">
              <summary className="font-semibold text-[var(--color-ink)]">How this was calculated ▾</summary>
              <div className="mt-2 space-y-2">
                <p>
                  <strong>Edges:</strong> structural, not statistical. A supplied archive follows its documented route
                  order; an uploaded dataset draws no station-to-station edge at all, because its file states no order
                  and carries no timestamps.
                </p>
                <p>
                  <strong>Solid (observed) nodes:</strong> measured in this dataset. Dashed (assumed) edges are
                  engineer-declared and cannot be verified from the data.
                </p>
                <p>
                  <strong>Defect nodes:</strong> exist only where the dataset carries defect labels (the image archive);
                  an uploaded CSV has none, so nothing is linked to a defect class.
                </p>
              </div>
            </details>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
