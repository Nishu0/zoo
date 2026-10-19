export { cn } from "cn"

export function timeAgo(timestamp: string) {
  const seconds = Math.max(
    0,
    Math.round((Date.now() - new Date(`${timestamp}Z`).getTime()) / 1000)
  )
  if (seconds < 60) return "just now"
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB", "TB"]
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

/** A UTC timestamp from the API (`YYYY-MM-DD HH:MM:SS`) relative to now, in the past or the future. */
export function relativeTime(timestamp: string) {
  const seconds = Math.round(
    (new Date(`${timestamp.replace(" ", "T")}Z`).getTime() - Date.now()) / 1000
  )
  const span = Math.abs(seconds)
  const text =
    span < 3600
      ? `${Math.max(1, Math.round(span / 60))}m`
      : span < 86400
        ? `${Math.round(span / 3600)}h`
        : `${Math.round(span / 86400)}d`
  return seconds >= 0 ? `in ${text}` : `${text} ago`
}

/** The date part of an API timestamp, for an <input type="date">. */
export function dateOf(timestamp: string | null) {
  return timestamp ? timestamp.slice(0, 10) : ""
}
