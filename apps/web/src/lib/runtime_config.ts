// The API URL comes from ZOO_API_URL when the web server starts, so one image
// works for any deployment. VITE_API_URL is the fallback for `bun dev`.
const FALLBACK = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

declare global {
  interface Window {
    __ZOO_API_URL__?: string
  }
}

export function configuredApiUrl(): string {
  if (typeof window === "undefined") return process.env.ZOO_API_URL || FALLBACK
  return window.__ZOO_API_URL__ || FALLBACK
}

export function runtimeConfigScript(): string {
  const url = JSON.stringify(configuredApiUrl()).replace(/</g, "\\u003c")
  return `window.__ZOO_API_URL__=${url}`
}
