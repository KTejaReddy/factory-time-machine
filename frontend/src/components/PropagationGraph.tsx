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
const STAGE_STYLES: Record<string, { border: string; bg: string; text?: string; mutedText?: string; shadow?: string }> = {
  process: { border: "rgba(34,197,94,0.6)", bg: "rgba(255,255,255,0.75)", text: "var(--color-ink)", mutedText: "var(--color-ink-dim)", shadow: "0 8px 30px rgba(34,197,94,0.15)" },
  state: { border: "rgba(203,213,225,0.6)", bg: "rgba(248,250,252,0.75)", text: "var(--color-ink)", mutedText: "var(--color-ink-dim)", shadow: "0 4px 15px rgba(0,0,0,0.05)" },
  outcome: { border: "rgba(245,158,11,0.6)", bg: "rgba(255,251,235,0.85)", text: "#92400E", mutedText: "#B45309", shadow: "0 8px 30px rgba(245,158,11,0.15)" },
  economic: { border: "rgba(6,78,59,0.8)", bg: "rgba(6,78,59,0.85)", text: "#FFFFFF", mutedText: "#A7F3D0", shadow: "0 8px 30px rgba(6,78,59,0.3)" },
  defect: { border: "rgba(239,68,68,0.6)", bg: "rgba(254,242,242,0.85)", text: "#991B1B", mutedText: "#B91C1C", shadow: "0 8px 30px rgba(239,68,68,0.15)" },
};

function GraphNode({ data, selected }: NodeProps<FlowNode>) {
  const style = STAGE_STYLES[data.kind] ?? STAGE_STYLES.process;
  
  return (
    <div 
      className={`relative w-[240px] rounded-xl border-2 backdrop-blur-md transition-all duration-300 ${selected ? "ring-4 ring-[rgba(34,197,94,0.3)] scale-105 z-10" : "hover:scale-105"}`}
      style={{ 
        borderColor: style.border, 
        background: style.bg, 
        color: style.text || "var(--color-ink)",
        boxShadow: selected ? style.shadow : "0 4px 6px rgba(0,0,0,0.05)",
      }}
    >
      <div className="absolute inset-0 rounded-xl bg-gradient-to-b from-white/40 to-transparent pointer-events-none" />
      <Handle type="target" position={Position.Left} style={{ background: style.border, width: 8, height: 8, border: "2px solid white", left: -5 }} />
      
      <div className="p-4 relative z-10">
        <div className="text-[10px] font-bold uppercase tracking-widest opacity-80 mb-1">{data.kind}</div>
        <div className="text-[16px] font-bold leading-tight mb-3">{data.label}</div>
        
        <div className="space-y-1.5 text-[12px] font-medium" style={{ color: style.mutedText }}>
          {data.utilisation !== undefined && data.utilisation !== null && (
            <div className="flex justify-between"><span>Utilization</span> <span className="mono">{pct(data.utilisation * 100, 1)}</span></div>
          )}
          {data.queue !== undefined && data.queue !== null && (
            <div className="flex justify-between"><span>Queue</span> <span className="mono">{num(data.queue, 1)}</span></div>
          )}
          {data.images !== undefined && data.images !== null && (
            <div className="flex justify-between"><span>Images</span> <span className="mono">{num(data.images, 0)}</span></div>
          )}
        </div>
        
        <div className="mt-4 pt-3 border-t border-black/10 flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            {data.status === "observed" && <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ backgroundColor: style.border }}></span>}
            <span className="relative inline-flex rounded-full h-2 w-2" style={{ backgroundColor: data.status === "unavailable" ? "#94A3B8" : style.border }}></span>
          </span>
          <span className="text-[11px] font-bold uppercase tracking-wider opacity-80">{data.status}</span>
        </div>
      </div>
      
      <Handle type="source" position={Position.Right} style={{ background: style.border, width: 8, height: 8, border: "2px solid white", right: -5 }} />
    </div>
  );
}

const nodeTypes = { flow: GraphNode };

// Removed unused layout constants

export function layoutNodes(nodes: PropNode[]): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  
  // Group nodes by their logical stage in the story
  const layers: Record<string, PropNode[]> = {
    process: [],
    defect: [],
    outcome: [],
    economic: [],
    state: []
  };

  nodes.forEach((node) => {
    if (layers[node.kind]) {
      layers[node.kind].push(node);
    } else {
      layers.state.push(node); // Default unknown to state layer
    }
  });

  const COLUMN_WIDTH = 320;
  const ROW_HEIGHT = 160;

  // Horizontal story layout
  let currentX = 0;
  
  const order = ["process", "defect", "outcome", "economic", "state"];
  
  order.forEach((layerKey) => {
    const layerNodes = layers[layerKey];
    if (layerNodes.length === 0) return;

    // Center nodes vertically in their column based on how many there are
    const totalHeight = layerNodes.length * ROW_HEIGHT;
    const startY = -totalHeight / 2;

    layerNodes.forEach((node, index) => {
      positions[node.id] = {
        x: currentX,
        y: startY + (index * ROW_HEIGHT)
      };
    });

    currentX += COLUMN_WIDTH;
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
    const positions = layoutNodes(nodes);
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
  }, [nodes, selected]);

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
            stroke: edge.status === "assumed" ? "#F59E0B" : edge.status === "unavailable" ? "#D1E5DB" : edge.relation === "associated" || edge.method === "association" ? "#6EE7B7" : "#22C55E",
            strokeWidth: edge.status === "assumed" ? 2 : Math.max(1.5, Math.min(4, Math.abs(edge.strength ?? 0.6) * 4)),
            strokeDasharray: edge.status === "assumed" ? "2 4" : edge.status === "unavailable" ? "4 4" : edge.relation === "associated" || edge.method === "association" ? "6 6" : undefined,
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
            <div className="text-[14px] font-bold tracking-wider text-[var(--color-ink)] uppercase">PROBLEM FLOW</div>
            <div className="mt-1 text-[13px] text-[var(--color-ink-dim)]">
              How the problem moves through the process
            </div>
            <div className="mt-1 text-[11px] font-medium text-[var(--color-ink-faint)]">
              {flowNodes.length} nodes · {edgeCount} relationships
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
