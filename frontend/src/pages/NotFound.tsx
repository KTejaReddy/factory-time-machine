import { Link } from "react-router-dom";

/**
 * Shown for any URL that is not a real page.
 *
 * The catch-all route used to render the Dashboard, so a mistyped or outdated
 * link quietly displayed different content than the address suggested. Saying so
 * is friendlier than pretending the page exists.
 */
export default function NotFound() {
  return (
    <div className="space-y-6 max-w-2xl">
      <header>
        <h1 className="text-[24px] font-bold tracking-tight">Page not found</h1>
        <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
          There is no page at this address. It may be an old link, or a typo.
        </p>
      </header>

      <div className="flex flex-wrap gap-2">
        <Link className="btn btn-primary" to="/">
          🏠 Overview
        </Link>
        <Link className="btn" to="/inspection">
          👁️ Inspect a product
        </Link>
        <Link className="btn" to="/forensic">
          🔎 Investigate a problem
        </Link>
      </div>
    </div>
  );
}
