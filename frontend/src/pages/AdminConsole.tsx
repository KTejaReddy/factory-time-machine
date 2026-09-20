import { Navigate } from "react-router-dom";
import { useAdminAuth } from "../lib/admin";
import { Card } from "../components/ui";

export default function AdminConsole() {
  const { isAuthenticated, logout } = useAdminAuth();

  if (!isAuthenticated) {
    return <Navigate to="/admin-login" replace />;
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-[24px] font-bold tracking-tight">Admin Console</h1>
          <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
            System administration and overview.
          </p>
        </div>
        <button onClick={logout} className="btn">Sign Out</button>
      </header>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Datasets">
          <p className="text-[13px] text-[var(--color-ink-dim)]">View and manage uploaded datasets.</p>
        </Card>
        <Card title="Analysis History">
          <p className="text-[13px] text-[var(--color-ink-dim)]">Review past pipeline runs.</p>
        </Card>
        <Card title="AI Activity">
          <p className="text-[13px] text-[var(--color-ink-dim)]">Monitor AI API usage and recommendations.</p>
        </Card>
        <Card title="Feedback">
          <p className="text-[13px] text-[var(--color-ink-dim)]">User feedback and model corrections.</p>
        </Card>
        <Card title="Reports">
          <p className="text-[13px] text-[var(--color-ink-dim)]">Generated system reports.</p>
        </Card>
        <Card title="System Status">
          <p className="text-[13px] text-[var(--color-ok)] font-medium">All systems operational.</p>
        </Card>
      </div>
    </div>
  );
}
