import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreferencesProvider } from "@/context/PreferencesContext";
import { Layout } from "@/components/layout/Layout";
import { DashboardPage } from "@/components/dashboard/DashboardPage";
import { SessionDetailPage } from "@/components/session/SessionDetailPage";
import { KnowledgePage } from "@/components/knowledge/KnowledgePage";
import { HealthPage } from "@/components/health/HealthPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<DashboardPage />} />
              <Route
                path="sessions/:teamName"
                element={<SessionDetailPage />}
              />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="health" element={<HealthPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </PreferencesProvider>
    </QueryClientProvider>
  );
}
