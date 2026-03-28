import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { POLL_FAST } from "@/lib/polling";

export function useTeamDetail(teamName: string) {
  return useQuery({
    queryKey: queryKeys.teamDetail(teamName),
    queryFn: () => api.getTeamDetail(teamName),
    refetchInterval: POLL_FAST,
    enabled: !!teamName,
  });
}
