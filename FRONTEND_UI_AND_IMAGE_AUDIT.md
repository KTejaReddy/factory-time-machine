# FRONTEND UI AND IMAGE AUDIT

### UI changes
- Replaced light blue theme with Bright Green, White, and Dark Charcoal industrial theme in `frontend/src/index.css`.
- Updated node styles, panel backgrounds, text colors, and borders globally to enforce the clean, futuristic aesthetic.
- Updated the sidebar to use a white background with a bright green active indicator.

### Problem Flow changes
- Completely redesigned `PropagationGraph.tsx` STAGE_STYLES mapping to visually distinct categories: Process (Green), Problem (Red), Production (Orange), Impact (Dark Green), and Neutral (Gray-Green).
- Enlarged graph nodes (`min-w-[180px]`) for better readability and added stronger shadows.
- Redesigned connection edges in `PropagationGraph.tsx` (Observed -> solid green, Associated -> dashed green, Assumed -> dotted amber, Unavailable -> dashed light gray).
- Updated title and subtitle in `Propagation.tsx` to match the target structure: "HOW THE PROBLEM FLOWS: Process → Problem → Effect → Impact".

### Dataset image display fix
- Fixed missing-image bug in `Inspection.tsx` by decoupling active dataset images from training archive images.
- If the dataset does not support vision (tabular only), the UI now explicitly shows a "NO IMAGES IN THIS DATASET" message, along with a list of supported features.
- If a training archive exists (from `api.inspectSamples`), it is still displayed in a separate, clearly labeled "TRAINING ARCHIVE" section underneath, ensuring users don't confuse model training images with active dataset images.

### Admin login
- Created a frontend-only `AdminLogin.tsx` component with a clean industrial design.
- Created `AdminConsole.tsx` populated with the requested summary cards.
- Added a lightweight frontend-only auth context in `lib/admin.ts` to gate access to the `/admin` route.
- Added a protected "Admin Console" link to the sidebar in `Layout.tsx`.

### Files changed
- `frontend/src/index.css`
- `frontend/src/components/Layout.tsx`
- `frontend/src/App.tsx`
- `frontend/src/pages/Inspection.tsx`
- `frontend/src/pages/Propagation.tsx`
- `frontend/src/components/PropagationGraph.tsx`
- `frontend/src/pages/AdminLogin.tsx` (New)
- `frontend/src/pages/AdminConsole.tsx` (New)
- `frontend/src/lib/admin.ts` (New)

### Files NOT changed
- All backend files.
- Python algorithms and simulation engines.
- `frontend/src/lib/api.ts` (Existing endpoints remained fully intact, no new endpoints were added).
- Other calculation/analytics frontend components (`Production.tsx`, `Economics.tsx`, `WhatIf.tsx`).

### Regression tests
- **Active vs Training Images**: Verified logic isolates active images from the fallback training samples.
- **Workflow Integrity**: Existing user workflow across routes (Overview, Production, Economics, AI Help) remains undisturbed. No state or routing paradigms were altered.
- **Graph Algorithm**: The graph nodes and edges are still fully populated from `api.propagation()`; only presentation changed.

### Browser verification
- Tested UI contrast and responsiveness.
- The Problem Flow graph refits itself and is sized sensibly.
- The layout cleanly supports desktop navigation with the new sidebar.
