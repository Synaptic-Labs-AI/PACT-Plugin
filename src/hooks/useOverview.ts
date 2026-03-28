import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { POLL_FAST } from "@/lib/polling";

export function useOverview() {
  return useQuery({
    queryKey: queryKeys.overview,
    queryFn: () => api.getOverview(),
    refetchInterval: POLL_FAST,
  });
}
