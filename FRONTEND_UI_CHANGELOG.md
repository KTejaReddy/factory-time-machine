# Factory Time Machine - Frontend UI Redesign Changelog

## Visual & Theme Updates
- **Color Palette:** Transitioned from a dark theme to a "Light Blue + Green Industrial Intelligence" palette (`tailwind.config.js`).
- **CSS Variables:** Updated all CSS root variables to support the new light industrial styling (`index.css`).

## Layout & Navigation
- **Sidebar:** Implemented a new compact sidebar (`Layout.tsx`) that acts as a sleek control panel on the left with icon-based navigation.
- **Top Bar:** Refreshed to match the clean, white/light blue aesthetic with clear breadcrumbs and dataset indicators.

## Pages Refactored
1. **Dashboard (`Dashboard.tsx` & `Datasets.tsx`):**
   - Redesigned as the central hub.
   - Integrated a prominent "Upload Dataset (CSV)" dropzone directly onto the dashboard to streamline data loading.
   - Removed redundant upload elements from `Datasets.tsx`.
   - Used hierarchical cards to clearly separate "Your Datasets", "Quick Actions", and "System Status".

2. **Inspection (`Inspection.tsx`):**
   - Implemented a clear split layout.
   - Left side: Prominent product image with Grad-CAM overlay toggle.
   - Right side: Clean, hierarchical display of prediction, confidence, TTA agreement, and actions.

3. **AI Investigator (`Investigator.tsx`):**
   - Transformed from a side-by-side widget form into a centralized, conversational "chat-like" interface.
   - Improved the presentation of AI findings (Finding, Evidence, Recommendation) into cleanly structured cards.

4. **Forensics (`Forensic.tsx`):**
   - Redesigned as an analytical workspace.
   - Replaced flat lists with expandable `<details>` panels for: Finding Summary, Evidence & Tracing, Propagation Timeline, and Limitations.

5. **Propagation (`PropagationGraph.tsx`):**
   - Updated node colors to match the new industrial theme:
     - Process: Light Blue (`var(--color-accent-soft)`)
     - State: Green (`var(--color-ok-soft)`)
     - Defect: Red (`var(--color-bad-soft)`)
     - Outcome: Orange/Yellow (`var(--color-warn-soft)`)
   - Switched the graph background from near-black to a subtle light gray (`var(--color-edge)`).

6. **Production (`Production.tsx`):**
   - Implemented a more compact grid layout.
   - Consolidated throughput and WIP stats with clear side-by-side cards.
   - Streamlined the Bottlenecks table and Unusual Activity displays.

7. **What If? (`WhatIf.tsx`):**
   - Enhanced the configuration panel.
   - Created a strong side-by-side comparison matrix (Baseline vs Simulated) for throughput, utilization, and queues.

8. **Review (`Review.tsx`):**
   - Upgraded the summary stat cards to use a top-border highlight style.
   - Refined the "Decision History" feed to present each item clearly with well-styled metadata tags.

## UI Components (`charts.tsx`)
- Changed chart styles (axes, grid lines, tooltips) from dark mode to a crisp light mode compatible with the new aesthetic.

**Note:** No backend logic, API behavior, or underlying AI models were modified during this redesign.
