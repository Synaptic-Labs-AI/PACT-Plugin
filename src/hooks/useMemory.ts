import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { POLL_SLOW } from "@/lib/polling";

export function useMemory(filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.memory(filters),
    queryFn: () => api.getMemory(filters),
    refetchInterval: POLL_SLOW,
  });
}

export function useMemoryStats() {
  return useQuery({
    queryKey: queryKeys.memoryStats,
    queryFn: () => api.getMemoryStats(),
    refetchInterval: POLL_SLOW,
  });
}
