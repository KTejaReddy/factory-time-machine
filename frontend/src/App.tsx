import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import DatasetWorkspace from "./pages/DatasetWorkspace";
import Datasets from "./pages/Datasets";
import Economics from "./pages/Economics";
import Forensic from "./pages/Forensic";
import Inspection from "./pages/Inspection";
import Investigator from "./pages/Investigator";
import NotFound from "./pages/NotFound";
import Production from "./pages/Production";
import Propagation from "./pages/Propagation";
import Repairs from "./pages/Repairs";
import Reports from "./pages/Reports";
import Review from "./pages/Review";
import WhatIf from "./pages/WhatIf";

export default function App() {
  // Opting into the v7 behaviour flags keeps the console free of the router's
  // deprecation warnings, which matters because a clean console is how the audit
  // verifies that no page is silently erroring.
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<Layout />}>
          {/* One dataset = one workspace. Opening /dataset/<id> makes that case
              file active, and every page below then reads its results. */}
          <Route path="/dataset/:id" element={<DatasetWorkspace />} />
          <Route path="/" element={<Dashboard />} />
          <Route path="/inspect" element={<Inspection />} />
          <Route path="/inspection" element={<Inspection />} />
          <Route path="/investigate" element={<Forensic />} />
          <Route path="/forensic" element={<Forensic />} />
          <Route path="/flow" element={<Propagation />} />
          <Route path="/propagation" element={<Propagation />} />
          <Route path="/production" element={<Production />} />
          <Route path="/economics" element={<Economics />} />
          <Route path="/repairs" element={<Repairs />} />
          <Route path="/what-if" element={<WhatIf />} />
          <Route path="/whatif" element={<WhatIf />} />
          <Route path="/ai" element={<Investigator />} />
          <Route path="/investigator" element={<Investigator />} />
          <Route path="/review" element={<Review />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/datasets" element={<Datasets />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
