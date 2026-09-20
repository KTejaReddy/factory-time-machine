import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

const AXIS = { stroke: "#94a3b8", fontSize: 11 };
const GRID = "#e2e8f0";

const tooltipStyle = {
  contentStyle: {
    background: "rgba(255,255,255,0.96)",
    border: "1px solid #e2e8f0",
    borderRadius: 8,
    fontSize: 12,
    color: "#0f172a",
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
  },
  labelStyle: { color: "#64748b", fontSize: 11, fontWeight: 600, marginBottom: 4 },
  itemStyle: { color: "#0f172a", fontWeight: 500 },
} as const;

export function UtilisationChart({ data, height = 280 }: { data: Array<{ label: string; utilization: number | null; rank?: number | null }>; height?: number }) {
  const rows = data.filter((d) => d.utilization !== null && d.utilization !== undefined);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={GRID} horizontal={false} />
        <XAxis type="number" domain={[0, 1]} tick={AXIS} tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`} />
        <YAxis type="category" dataKey="label" tick={{ ...AXIS, fontSize: 11 }} width={132} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [`${(Number(v) * 100).toFixed(1)}%`, "utilisation"]} />
        <ReferenceLine x={0.9} stroke="#ef4444" strokeDasharray="3 3" />
        <Bar dataKey="utilization" radius={[0, 3, 3, 0]} barSize={13}>
          {rows.map((row) => (
            <Cell
              key={row.label}
              fill={(row.utilization ?? 0) >= 0.9 ? "#ef4444" : (row.utilization ?? 0) >= 0.75 ? "#f59e0b" : "#0ea5e9"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function QueueChart({ data, height = 240 }: { data: Array<{ label: string; queue_mean: number | null }>; height?: number }) {
  const rows = data.filter((d) => d.queue_mean !== null && d.queue_mean !== undefined);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 6, right: 12, bottom: 34, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={{ ...AXIS, fontSize: 10 }} interval={0} angle={-32} textAnchor="end" height={48} />
        <YAxis tick={AXIS} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [Number(v).toFixed(1), "mean queue (parts)"]} />
        <Bar dataKey="queue_mean" fill="#0ea5e9" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DemandCurve({
  points,
  series,
  height = 260,
}: {
  points: Array<Record<string, any>>;
  series: Array<{ key: string; label: string; extract: (point: Record<string, any>) => number | null }>;
  height?: number;
}) {
  const rows = points.map((point) => {
    const row: Record<string, any> = { demand: point.demand };
    series.forEach((s) => {
      row[s.key] = s.extract(point);
    });
    return row;
  });
  const colors = ["#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 6, left: 0 }}>
        <CartesianGrid stroke={GRID} />
        <XAxis dataKey="demand" tick={AXIS} label={{ value: "Demand factor level", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 11 }} />
        <YAxis tick={AXIS} />
        <Tooltip {...tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 11, color: "#334155" }} />
        {series.map((s, i) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={colors[i % colors.length]} dot={false} strokeWidth={2} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function ComparisonBars({
  data,
  height = 300,
}: {
  data: Array<{ label: string; current: number | null; simulated: number | null }>;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={GRID} horizontal={false} />
        <XAxis type="number" tick={AXIS} tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`} />
        <YAxis type="category" dataKey="label" tick={{ ...AXIS, fontSize: 11 }} width={132} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => `${(Number(v) * 100).toFixed(1)}%`} />
        <Legend wrapperStyle={{ fontSize: 11, color: "#334155" }} />
        <Bar dataKey="current" name="current (dataset)" fill="#94a3b8" radius={[0, 3, 3, 0]} barSize={9} />
        <Bar dataKey="simulated" name="simulated" fill="#0ea5e9" radius={[0, 3, 3, 0]} barSize={9} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function AssociationScatter({
  points,
  xLabel,
  yLabel,
  height = 300,
}: {
  points: Array<{ x: number; y: number; label: string }>;
  xLabel: string;
  yLabel: string;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 18, left: 4 }}>
        <CartesianGrid stroke={GRID} />
        <XAxis type="number" dataKey="x" tick={AXIS} name={xLabel} />
        <YAxis type="number" dataKey="y" tick={AXIS} name={yLabel} />
        <ZAxis range={[26, 26]} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => Number(v).toFixed(4)} />
        <Scatter data={points} fill="#0ea5e9" fillOpacity={0.75} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function ResidualChart({ rows, height = 260 }: { rows: Array<{ label: string; residual_pct: number }>; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 44, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={{ ...AXIS, fontSize: 10 }} interval={0} angle={-32} textAnchor="end" height={56} />
        <YAxis tick={AXIS} tickFormatter={(v) => `${Math.round(Number(v))}%`} />
        <ReferenceLine y={0} stroke="#94a3b8" />
        <ReferenceLine y={15} stroke="#f59e0b" strokeDasharray="3 3" />
        <ReferenceLine y={-15} stroke="#f59e0b" strokeDasharray="3 3" />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [`${Number(v).toFixed(1)}%`, "simulated vs observed"]} />
        <Bar dataKey="residual_pct" radius={[3, 3, 0, 0]}>
          {rows.map((row) => (
            <Cell key={row.label} fill={Math.abs(row.residual_pct) <= 15 ? "#10b981" : "#ef4444"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
