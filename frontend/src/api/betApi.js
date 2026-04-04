import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

export const decodeBookingCode = async (bookingCode) => {
  const { data } = await api.post('/decode/', { booking_code: bookingCode })
  return data
}

export const fetchLiveStats = async (bookingCode) => {
  const { data } = await api.get('/live-stats/', {
    params: { booking_code: bookingCode },
  })
  return data
}

export const fetchLiveNow = async () => {
  const { data } = await api.get('/live-now/')
  return data
}

export const fetchMatchesToday = async () => {
  const { data } = await api.get('/matches-today/')
  return data
}

export const fetchFixturesByDate = async (date) => {
  const { data } = await api.get('/matches-today/', { params: { date } })
  return data
}

export const fetchMatchDetail = async (fixtureId) => {
  const { data } = await api.get(`/match-detail/${fixtureId}/`)
  return data
}

export const fetchMatchPreview = async (fixtureId) => {
  const { data } = await api.get(`/match-preview/${fixtureId}/`)
  return data
}
