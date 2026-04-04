import { useQuery } from '@tanstack/react-query'
import { fetchLiveStats } from '../api/betApi'

const POLL_INTERVAL = 10_000 // 10 seconds

export function useLiveStats(bookingCode) {
  return useQuery({
    queryKey: ['liveStats', bookingCode],
    queryFn: () => fetchLiveStats(bookingCode),
    enabled: !!bookingCode,
    refetchInterval: POLL_INTERVAL,
    refetchIntervalInBackground: false,
    staleTime: 0,
  })
}
