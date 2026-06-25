import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 180000,
  headers: { 'Content-Type': 'application/json' },
})

export default client
