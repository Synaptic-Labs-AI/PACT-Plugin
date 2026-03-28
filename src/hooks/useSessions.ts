import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { POLL_MEDIUM } from "@/lib/polling";

export function useSessions(filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.sessions(filters),
    queryFn: () => api.getSessions(filters),
    refetchInterval: POLL_MEDIUM,
  });
}
