import { useEffect, useState } from "react";
import { NavLink, Link, Outlet } from "react-router-dom";
import {
  api,
  rememberFirstPresentDataset,
  adoptServerActive,
  getActiveDataset,
  setActiveDataset,
  type DatasetRecord,
  type ProcessingStatus,
} from "../lib/api";
import { useAdminAuth } from "../lib/admin";
import { Badge } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: "🏠", end: true },
  { to: "/inspection", label: "Inspect", icon: "👁️" },
  { to: "/forensic", label: "Investigate", icon: "🔎" },
  { to: "/propagation", label: "Problem Flow", icon: "🕸️" },
  { to: "/production", label: "Production", icon: "🏭" },
  { to: "/economics", label: "Economics", icon: "💰" },
  { to: "/repairs", label: "Repairs", icon: "🛠️" },
  { to: "/whatif", label: "What-If", icon: "🧪" },
  { to: "/investigator", label: "AI Help", icon: "💬" },
  { to: "/review", label: "Review", icon: "👨‍🔧" },
  { to: "/reports", label: "Reports", icon: "📄" },
];

function useCatalogStatus() {
  const [status, setStatus] = useState<ProcessingStatus | null>(null);
  const [ai, setAi] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await api.status();
        if (alive) setStatus(res.status);
        if (res.status.state === "ready") {
          rememberFirstPresentDataset(res.datasets);
          return true;
        }
      } catch {
        /* backend may still be starting */
      }
      return false;
    };
    void tick();
    const timer = window.setInterval(async () => {
      const done = await tick();
      if (done) window.clearInterval(timer);
    }, 2000);
    api.aiStatus().then((res) => alive && setAi(res)).catch(() => undefined);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  return { status, ai };
}

/** The dataset registry, refreshed on every dataset switch or upload. */
export function useWorkspaceDatasets(): { records: DatasetRecord[]; reload: () => void } {
  const [records, setRecords] = useState<DatasetRecord[]>([]);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const res = await api.workspaceDatasets();
        if (!alive) return;
        setRecords(res.datasets);
        // First visit in this browser: adopt the dataset the server remembers.
        // A remembered key that no longer exists (deleted dataset, stale browser)
        // is replaced too, so no page keeps requesting a dataset the backend does
        // not know - and each stale key is resolved exactly once.
        const known = new Set(res.datasets.map((r) => r.key));
        const current = getActiveDataset();
        if (!res.active) return;
        if (!current) adoptServerActive(res.active);
        else if (!known.has(current)) adoptServerActive(res.active, current);
      } catch {
        /* registry is unavailable until the catalog is ready */
      }
    };
    // Coalesce bursts of events (an upload dispatches selection + workspace
    // changes) into a single reload.
    const schedule = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => void load(), 120);
    };
    void load();
    window.addEventListener("datasetChanged", schedule);
    window.addEventListener("workspaceChanged", schedule);
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
      window.removeEventListener("datasetChanged", schedule);
      window.removeEventListener("workspaceChanged", schedule);
    };
  }, [nonce]);
  return { records, reload: () => setNonce((n) => n + 1) };
}

function useActiveDatasetKey() {
  const [key, setKey] = useState(getActiveDataset());
  useEffect(() => {
    const handler = () => setKey(getActiveDataset());
    window.addEventListener("datasetChanged", handler);
    return () => window.removeEventListener("datasetChanged", handler);
  }, []);
  return key;
}

const shortDate = (value: string | null) => {
  if (!value) return "supplied archive";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
};

export default function Layout() {
  const { status, ai } = useCatalogStatus();
  const { records } = useWorkspaceDatasets();
  const activeKey = useActiveDatasetKey();
  const active = records.find((r) => r.key === activeKey);
  const selectable = records.filter((r) => r.present || r.key === activeKey);
  const { isAuthenticated, logout } = useAdminAuth();

  const building = status && status.state !== "ready";
  const failed = status?.state === "failed";

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-[248px] shrink-0 flex-col border-r border-[rgba(20,180,100,0.18)] bg-[rgba(255,255,255,0.5)] backdrop-blur-lg px-3 py-4 md:flex shadow-[4px_0_24px_rgba(0,0,0,0.02)] z-30 fixed inset-y-0 left-0 overflow-y-auto">
        <div className="px-2 pb-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-md bg-[var(--color-accent)] text-[15px] text-white">
              ⛭
            </span>
            <div className="leading-tight">
              <div className="text-[13.5px] font-semibold tracking-tight">Factory Time Machine</div>
              <div className="text-[10.5px] text-[var(--color-ink-faint)]">manufacturing forensics</div>
            </div>
          </div>
        </div>

        <nav className="flex flex-col gap-0.5">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 text-[13px] font-bold tracking-wide transition-all duration-300 border-l-2 rounded-r-lg ${
                  isActive
                    ? "bg-[rgba(255,255,255,0.8)] text-[var(--color-accent)] border-[var(--color-accent)] shadow-[0_2px_10px_rgba(34,197,94,0.1),inset_0_1px_0_rgba(255,255,255,1)] translate-x-1"
                    : "text-[var(--color-ink-dim)] border-transparent hover:bg-[rgba(255,255,255,0.5)] hover:text-[var(--color-ink)]"
                }`
              }
            >
              <span className="text-[16px] opacity-90">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
          <NavLink
            to="/datasets"
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 text-[13px] font-bold tracking-wide transition-all duration-300 border-l-2 rounded-r-lg ${
                isActive ? "bg-[rgba(255,255,255,0.8)] text-[var(--color-accent)] border-[var(--color-accent)] shadow-[0_2px_10px_rgba(34,197,94,0.1),inset_0_1px_0_rgba(255,255,255,1)] translate-x-1" : "text-[var(--color-ink-dim)] border-transparent hover:bg-[rgba(255,255,255,0.5)] hover:text-[var(--color-ink)]"
              }`
            }
          >
            <span className="text-[16px] opacity-90">🗂️</span>
            Datasets
          </NavLink>
          <NavLink
            to={isAuthenticated ? "/admin" : "/admin-login"}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 text-[13px] font-bold tracking-wide transition-all duration-300 border-l-2 rounded-r-lg ${
                isActive ? "bg-[rgba(255,255,255,0.8)] text-[var(--color-accent)] border-[var(--color-accent)] shadow-[0_2px_10px_rgba(34,197,94,0.1),inset_0_1px_0_rgba(255,255,255,1)] translate-x-1" : "text-[var(--color-ink-dim)] border-transparent hover:bg-[rgba(255,255,255,0.5)] hover:text-[var(--color-ink)]"
              }`
            }
          >
            <span className="text-[16px] opacity-90">🛡️</span>
            Admin Console
          </NavLink>
        </nav>

        <div className="mt-auto space-y-2 px-2 pt-4">
          <div className="panel-flat p-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-[var(--color-ink-faint)]">
              Data pipeline
            </div>
            {building ? (
              <div className="mt-1.5">
                <div className="flex items-center justify-between text-[11.5px] text-[var(--color-warn)]">
                  <span>{status?.step ?? "starting"}</span>
                  <span className="mono">{Math.round((status?.progress ?? 0) * 100)}%</span>
                </div>
                <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-[rgba(148,163,184,0.16)]">
                  <div className="h-full bg-[var(--color-warn)]" style={{ width: `${(status?.progress ?? 0) * 100}%` }} />
                </div>
              </div>
            ) : failed ? (
              <div className="mt-1.5 text-[11.5px] text-[var(--color-bad)]">{status?.error ?? "failed"}</div>
            ) : (
              <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-[var(--color-ok)]">
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-ok)]" />
                ready
                {status?.seconds ? <span className="mono text-[var(--color-ink-faint)]">{status.seconds.toFixed(1)}s</span> : null}
              </div>
            )}
          </div>
          <div className="panel-flat p-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-[var(--color-ink-faint)]">
              AI narrative
            </div>
            <div className="mt-1.5">
              {ai?.llm_enabled ? (
                <Badge tone="info">LLM: {ai?.model}</Badge>
              ) : (
                <Badge tone="muted" title={ai?.reason}>
                  deterministic engine
                </Badge>
              )}
            </div>
          </div>
          <div className="px-0.5 text-[10.5px] leading-snug text-[var(--color-ink-faint)]">
            Advisory only. No equipment, PLC or production line is ever controlled.
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col z-0 md:pl-[248px]">
        <header className="glass-header">
          <div className="flex flex-1 items-center gap-6">
            <span className="text-[14px] font-bold tracking-wider uppercase text-[var(--color-ink)]">Factory Time Machine</span>
            <span className="hidden h-5 w-px bg-[var(--color-edge)] md:inline" />
            <label className="flex items-center gap-3">
              <span className="eyebrow tracking-widest text-[var(--color-accent)]">ACTIVE DATASET</span>
              <select
                aria-label="Active dataset"
                className="field !w-auto !py-1 text-[13px] font-medium min-w-[200px]"
                value={activeKey}
                onChange={(event) => setActiveDataset(event.target.value)}
              >
                {!activeKey && <option value="">— none selected —</option>}
                {selectable.map((record) => (
                  <option key={record.key} value={record.key}>
                    Uploaded dataset: {record.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex items-center gap-3">
            {building ? <Badge tone="warn">● Processing datasets…</Badge> : failed ? <Badge tone="bad">● Dataset error</Badge> : <Badge tone="ok">● Dataset Ready</Badge>}
            <Badge tone="muted" title="Every finding in this tool is advisory; it cannot command machinery.">
              ● Advisory Only
            </Badge>
            <div className="h-5 w-px bg-[var(--color-edge)] mx-1" />
            {isAuthenticated ? (
              <div className="flex items-center gap-3">
                <span className="text-[12px] font-bold text-[var(--color-accent)] uppercase tracking-wider">● Admin</span>
                <button onClick={() => logout()} className="btn !py-1 !px-2 text-[11px] font-bold">
                  Logout
                </button>
              </div>
            ) : (
              <Link to="/admin-login" className="btn !py-1 !px-2 text-[11px] font-bold uppercase">
                Admin Login
              </Link>
            )}
          </div>
        </header>

        {/* Workspace identity strip: which case file is open, and what it is. */}
        <div className="location-bar">
          {active ? (
            <>
              <span className="text-[13px] font-semibold tracking-tight text-[var(--color-ink)]">{active.name}</span>
              <span className="mono text-[11px] text-[var(--color-ink-faint)]">{active.id}</span>
              <span className="text-[11.5px] text-[var(--color-ink-dim)]">
                uploaded: {shortDate(active.uploaded_at)}
              </span>
              <span className="text-[11.5px] text-[var(--color-ink-dim)]">
                status: <span className="font-medium">{active.status_label}</span>
              </span>
              <span className="ml-auto flex items-center gap-2">
                <Link to={`/dataset/${active.id}`} className="btn btn-primary">
                  Open workspace
                </Link>
              </span>
            </>
          ) : (
            <span className="text-[12.5px] text-[var(--color-ink-dim)]">
              No dataset selected. <Link className="underline" to="/datasets">Upload one</Link> to start a case file.
            </span>
          )}
        </div>

        <main className="min-w-0 flex-1 p-6">
          {building && (
            <div className="panel mb-4 p-3 text-[12.5px] text-[var(--color-ink-dim)]">
              Building the analysis caches from the source archives (first run reads {status?.message || "the datasets"}).
              Pages will populate automatically. {status?.step ? <span className="mono"> step: {status.step}</span> : null}
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
