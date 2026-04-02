import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { GuidelineFilterOptionsResponse } from '../lib/types'

const EMPTY_OPTIONS: GuidelineFilterOptionsResponse = {
  publishers: [],
  ten_benhs: [],
}

export default function useGuidelineFilterOptions() {
  const [options, setOptions] = useState<GuidelineFilterOptionsResponse>(EMPTY_OPTIONS)

  const fetchOptions = useCallback(async () => {
    try {
      const response = await api.get<GuidelineFilterOptionsResponse>('/guidelines/filter-options')
      setOptions({
        publishers: response.data.publishers,
        ten_benhs: response.data.ten_benhs,
      })
    } catch (error) {
      console.error('Failed to fetch guideline filter options:', error)
    }
  }, [])

  useEffect(() => {
    void fetchOptions()
  }, [fetchOptions])

  return options
}
