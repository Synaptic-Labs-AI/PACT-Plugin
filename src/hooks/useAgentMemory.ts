import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { POLL_SLOW } from "@/lib/polling";

export function useAgentMemory() {
  return useQuery({
    queryKey: queryKeys.agentMemory,
    queryFn: () => api.getAgentMemory(),
    refetchInterval: POLL_SLOW,
  });
}
