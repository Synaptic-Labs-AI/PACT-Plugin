import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { POLL_MEDIUM } from "@/lib/polling";

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api.getHealth(),
    refetchInterval: POLL_MEDIUM,
  });
}
