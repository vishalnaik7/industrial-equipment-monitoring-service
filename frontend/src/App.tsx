import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate, RouterProvider, useRouteError, Link } from "react-router-dom";
import { ErrorBox, EmptyState, Skeleton } from "./components/feedback";
import { Layout } from "./components/Layout";

// One chunk per page: the overview loads without pulling in the ingest editor or trace views.
const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const MachinesPage = lazy(() => import("./pages/MachinesPage"));
const IngestPage = lazy(() => import("./pages/IngestPage"));
const BatchesPage = lazy(() => import("./pages/BatchesPage"));
const DeadLettersPage = lazy(() => import("./pages/DeadLettersPage"));
const TracePage = lazy(() => import("./pages/TracePage"));

const page = (el: React.ReactNode) => <Suspense fallback={<Skeleton height={240} />}>{el}</Suspense>;

function RouteError() {
  const error = useRouteError();
  return <div className="content"><ErrorBox error={error} /><p><Link to="/overview">Back to overview</Link></p></div>;
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/overview" replace /> },
      { path: "overview", element: page(<OverviewPage />) },
      { path: "machines/:machineId?", element: page(<MachinesPage />) },
      { path: "ingest", element: page(<IngestPage />) },
      { path: "batches/:batchId?", element: page(<BatchesPage />) },
      { path: "dead-letters", element: page(<DeadLettersPage />) },
      { path: "trace/:vendor?/:recordId?", element: page(<TracePage />) },
      { path: "*", element: <EmptyState title="Page not found" hint={<Link to="/overview">Go to the overview</Link>} icon="help" /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
