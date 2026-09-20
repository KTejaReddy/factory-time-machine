import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { PropEdge, PropNode } from "../lib/api";
import { num, pct } from "../lib/hooks";
import { Badge, Bullets, Empty, EvidenceList, KeyValue } from "./ui";

interface NodeData extends Record<string, unknown> {
  label: string;
  kind: string;
  status: string;
  utilisation?: number | null;
  queue?: number | null;
  images?: number | null;
}
type FlowNode = Node<NodeData>;

// Light industrial palette: paper-white cards with a slate-blue, amber, clay and
// stone tint per stage. The graph used to be drawn on a near-black canvas, which
// no longer matches the rest of the app.
const STAGE_STYLES: Record<string, { border: string; bg: string }> = {
  process: { border: "var(--color-accent-edge)", bg: "var(--color-accent-soft)" },
  state: { border: "#6ee7b7", bg: "var(--color-ok-soft)" },
  outcome: { border: "#fcd34d", bg: "var(--color-warn-soft)" },
  economic: { border: "var(--color-edge-strong)", bg: "var(--color-hull)" },
  defect: { border: "#fca5a5", bg: "var(--color-bad-soft)" },
};

function GraphNode({ data, selected }: NodeProps<FlowNode>) {
  const style = STAGE_STYLES[data.kind] ?? STAGE_STYLES.process;
  const classes = ["flow-node", selected ? "selected" : "", data.status === "assumed" ? "assumed" : "", data.status === "unavailable" ? "unavailable" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={classes} style={{ borderColor: style.border, background: style.bg }}>
      <Handle type="target" position={Position.Left} style={{ background: "#8a97a8", width: 6, height: 6 }} />
      <div className="text-[11.5px] font-semibold leading-tight">{data.label}</div>
      <div className="mt-1 space-y-0.5 text-[10.5px] text-[var(--color-ink-faint)]">
        {data.utilisation !== undefined && data.utilisation !== null && (
          <div className="mono">util {pct(data.utilisation * 100, 1)}</div>
        )}
        {data.queue !== undefined && data.queue !== null && <div className="mono">queue {num(data.queue, 1)}</div>}
        {data.images !== undefined && data.images !== null && <div className="mono">{num(data.images, 0)} images</div>}
      </div>
      {data.status !== "observed" && (
        <div className="mt-1">
          <span className={`chip ${data.status === "assumed" ? "chip-assumed" : "chip-muted"}`}>{data.status}</span>
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: "#8a97a8", width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { flow: GraphNode };

const COLUMN = 210;
const ROUTE_Y = 340;
const QUEUE_Y = 560;
const OUTCOME_Y = 60;
const DEFECT_Y = 780;

/**
 * Positions derived from the graph itself, not from a list of hardcoded ids.
 *
 * An earlier version mapped a fixed set of node names (`stage_logistics`,
 * `state_storage`, ...) which had drifted from the API: 17 of the 32 nodes the
 * backend returns found no entry and spilled onto a fallback grid, which is what
 * turned the diagram into a hairball. The structure carries all of it:
 *
 * * the process route is the chain of `feeds` edges, laid out left to right so
 *   it reads like the plant (blanking -> forklift -> presses -> cells -> paint
 *   -> quality);
 * * a node the route feeds via `queues_parts_into` is that station's queue, hung
 *   underneath its station;
 * * `outcome`/`economic` nodes sit on the row above the route, `defect` nodes on
 *   their own row below (the archive shares no key with the route, so nothing
 *   joins them);
 * * anything the structure does not explain still gets a deterministic slot, so
 *   nothing is ever hidden.
 */
export function layoutNodes(nodes: PropNode[], edges: PropEdge[]): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const routeIds = new Set<string>();

  // 1. Walk the `feeds` chain from its head to its tail.
  const feeds = edges.filter((edge) => edge.relation === "feeds");
  const next = new Map<string, string>();
  const hasIncoming = new Set<string>();
  feeds.forEach((edge) => {
    next.set(edge.source, edge.target);
    hasIncoming.add(edge.target);
    routeIds.add(edge.source);
    routeIds.add(edge.target);
  });
  const head = nodes.find((node) => routeIds.has(node.id) && !hasIncoming.has(node.id))?.id;
  const route: string[] = [];
  for (let cursor = head; cursor && !route.includes(cursor); cursor = next.get(cursor)) route.push(cursor);
  // Nodes on the route that the chain walk missed (a branch, or no head) keep
  // their relative order from the payload.
  nodes.forEach((node) => {
    if (routeIds.has(node.id) && !route.includes(node.id)) route.push(node.id);
  });
  route.forEach((id, index) => {
    positions[id] = { x: index * COLUMN, y: ROUTE_Y };
  });

  // 2. Queues hang below the station that feeds them.
  edges
    .filter((edge) => edge.relation === "queues_parts_into" && !positions[edge.target])
    .forEach((edge, index) => {
      const station = positions[edge.source];
      positions[edge.target] = { x: station ? station.x : index * COLUMN, y: QUEUE_Y };
    });

  // 3. Defects on their own row - they are model labels, not route positions.
  nodes
    .filter((node) => node.kind === "defect" && !positions[node.id])
    .forEach((node, index) => {
      positions[node.id] = { x: index * COLUMN * 0.85, y: DEFECT_Y };
    });

  // 4. Outcomes above the route, to the right, nearest the end of the chain.
  const routeWidth = Math.max(0, (route.length - 1) * COLUMN);
  nodes
    .filter((node) => (node.kind === "outcome" || node.kind === "economic") && !positions[node.id])
    .forEach((node, index) => {
      positions[node.id] = { x: Math.max(0, routeWidth - index * COLUMN * 0.75), y: OUTCOME_Y };
    });

  // 5. A declared process problem sits at the head of the route.
  nodes
    .filter((node) => node.kind === "process" && !positions[node.id])
    .forEach((node, index) => {
      positions[node.id] = { x: -COLUMN, y: ROUTE_Y - 120 * (index + 1) };
    });

  // 6. Anything still unplaced goes on a deterministic grid below.
  const rest = nodes.filter((node) => !positions[node.id]);
  rest.forEach((node, index) => {
    positions[node.id] = { x: (index % 4) * COLUMN, y: DEFECT_Y + 150 * (Math.floor(index / 4) + 1) };
  });
  return positions;
}

export default function PropagationGraph({
  nodes,
  edges,
  legend,
  notice,
}: {
  nodes: PropNode[];
  edges: PropEdge[];
  legend: Record<string, string>;
  notice: string;
}) {
  const [selected, setSelected] = useState<PropNode | null>(null);
  const [showAssumed, setShowAssumed] = useState(true);
  const [instance, setInstance] = useState<ReactFlowInstance | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const flowNodes: FlowNode[] = useMemo(() => {
    const positions = layoutNodes(nodes, edges);
    return nodes.map((node) => {
        const position = positions[node.id] ?? { x: 0, y: 0 };
        return {
          id: node.id,
          type: "flow",
          position,
          data: {
            label: node.label,
            kind: node.kind,
            status: node.status,
            utilisation: node.metrics?.utilisation_mean ?? node.metrics?.utilisation ?? null,
            queue: node.metrics?.queue_mean ?? null,
            images: node.metrics?.images ?? null,
          },
          selected: selected?.id === node.id,
        } satisfies FlowNode;
      });
  }, [nodes, edges, selected]);

  const flowEdges: Edge[] = useMemo(
    () =>
      edges
        .filter((edge) => showAssumed || edge.status !== "assumed")
        .map((edge) => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          animated: edge.status === "observed" && (edge.strength ?? 0) > 0.5,
          label: edge.relation,
          labelStyle: { fill: "#5b6675", fontSize: 9.5 },
          labelBgStyle: { fill: "rgba(255,255,255,0.88)" },
          labelBgPadding: [4, 2] as [number, number],
          style: {
            stroke: edge.status === "assumed" ? "#b45309" : edge.status === "unavailable" ? "#a8a29e" : "#34557f",
            strokeWidth: edge.status === "assumed" ? 1.6 : Math.max(1, Math.min(3, Math.abs(edge.strength ?? 0.6) * 3)),
            strokeDasharray: edge.status === "assumed" ? "6 4" : edge.status === "unavailable" ? "2 4" : undefined,
          },
        })),
    [edges, showAssumed],
  );

  const edgeCount = flowEdges.length;

  // React Flow fits the view when it initialises, which can happen before the
  // container has its final size (the browser window decides the height). Without
  // this the graph can start zoomed in with its right-hand nodes cut off, so the
  // view is refitted whenever the node set changes or the container resizes.
  useEffect(() => {
    if (!instance) return;
    const refit = () => instance.fitView({ padding: 0.14 });
    refit();
    const target = containerRef.current;
    if (!target || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(refit);
    observer.observe(target);
    return () => observer.disconnect();
  }, [instance, flowNodes.length, containerRef]);

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
      <div className="panel overflow-hidden">
        <div className="panel-head">
          <div>
            <div className="panel-title">Failure propagation</div>
            <div className="mt-0.5 text-[11.5px] text-[var(--color-ink-faint)]">
              {flowNodes.length} nodes · {edgeCount} edges · click a node for its supporting data
            </div>
            <div className="mt-0.5 text-[11px] text-[var(--color-ink-faint)]">
              Drag to move · scroll to zoom · the frame button (bottom left) fits the whole route
            </div>
          </div>
          <label className="flex items-center gap-2 text-[11.5px] text-[var(--color-ink-dim)]">
            <input type="checkbox" checked={showAssumed} onChange={(e) => setShowAssumed(e.target.checked)} />
            show assumed edges
          </label>
        </div>
        <div ref={containerRef} className="h-[clamp(420px,60vh,660px)]">
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            fitView
            // 32 nodes across a 12-station route cannot fit at the old 0.45
            // floor - fitView simply stopped short and the route looked cut
            // off. Fitting the whole picture is the priority; the user can zoom
            // back in, and the frame button re-fits at any time.
            fitViewOptions={{ padding: 0.12, minZoom: 0.2, maxZoom: 1.1 }}
            // The route is intrinsically wide (seven stages in a row). Zooming all
            // the way out to fit it made the labels unreadable, so the floor is set
            // where text is still legible and the graph pans instead.
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
            onInit={setInstance}
            onNodeClick={(_, node) => setSelected(nodes.find((n) => n.id === node.id) ?? null)}
            onPaneClick={() => setSelected(null)}
          >
            <Background color="var(--color-edge)" gap={22} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable style={{ background: "var(--color-surface)", border: "1px solid var(--color-edge)" }} maskColor="rgba(248,250,252,0.7)" nodeColor="var(--color-edge-strong)" />
          </ReactFlow>
        </div>
        <div className="border-t border-[var(--color-edge)] p-3 text-[11.5px] leading-relaxed text-[var(--color-ink-faint)]">{notice}</div>
      </div>

      <div className="space-y-4">
        <div className="panel p-4">
          <div className="panel-title">Legend</div>
          <ul className="mt-2.5 space-y-2 text-[12px]">
            {Object.entries(legend).map(([key, value]) => (
              <li key={key} className="flex gap-2.5">
                <span className="mt-1 shrink-0">
                  <Badge tone={key === "observed" ? "info" : key === "assumed" ? "assumed" : "muted"}>{key}</Badge>
                </span>
                <span className="leading-snug text-[var(--color-ink-dim)]">{value}</span>
              </li>
            ))}
          </ul>
        </div>

        {selected ? (
          <div className="panel fade-in">
            <div className="panel-head">
              <div className="panel-title">Node detail</div>
              <Badge tone={selected.status === "observed" ? "info" : selected.status === "assumed" ? "assumed" : "muted"}>
                {selected.status}
              </Badge>
            </div>
            <div className="space-y-3 p-4">
              <div>
                <div className="text-[15px] font-semibold leading-tight">{selected.label}</div>
                <div className="mt-1 flex gap-2">
                  <Badge tone="muted">{selected.kind}</Badge>
                  <Badge tone="muted">domain: {selected.domain}</Badge>
                </div>
              </div>

              <KeyValue
                rows={Object.entries(selected.metrics)
                  .filter(([, value]) => value !== null && value !== undefined && typeof value !== "object")
                  .map(([key, value]) => [
                    key.replace(/_/g, " "),
                    typeof value === "number" ? num(value, 3) : String(value),
                  ])}
              />

              {selected.evidence?.length > 0 && (
                <div>
                  <div className="panel-title mb-1.5">Supporting data</div>
                  <EvidenceList items={selected.evidence} compact />
                </div>
              )}
              {selected.notes?.length > 0 && (
                <div>
                  <div className="panel-title mb-1.5">Notes</div>
                  <Bullets items={selected.notes} tone="faint" />
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="panel p-4">
            <div className="panel-title">Node detail</div>
            <Empty>Select a node in the graph to inspect the evidence behind it.</Empty>
          </div>
        )}
      </div>
    </div>
  );
}
