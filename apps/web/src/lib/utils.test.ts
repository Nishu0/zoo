import { afterEach, describe, expect, it, vi } from "vitest"

import { runtimeConfigScript } from "./runtime_config"
import { formatBytes, timeAgo } from "./utils"

describe("formatBytes", () => {
  it("picks the largest unit that keeps the value above 1", () => {
    expect(formatBytes(512)).toBe("512 B")
    expect(formatBytes(1536)).toBe("1.5 KB")
    expect(formatBytes(50 * 1024 * 1024)).toBe("50 MB")
    expect(formatBytes(3 * 1024 ** 5)).toBe("3072 TB")
  })
})

describe("timeAgo", () => {
  afterEach(() => vi.useRealTimers())

  it("reads API timestamps as UTC", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"))
    expect(timeAgo("2026-10-05T11:59:30")).toBe("just now")
    expect(timeAgo("2026-10-05T11:15:00")).toBe("45m ago")
    expect(timeAgo("2026-10-05T07:00:00")).toBe("5h ago")
    expect(timeAgo("2026-10-02T12:00:00")).toBe("3d ago")
  })
})

describe("runtimeConfigScript", () => {
  it("can't be used to close the script tag it is inlined into", () => {
    vi.stubEnv("ZOO_API_URL", "https://x</script><script>alert(1)//")
    expect(runtimeConfigScript()).not.toContain("</script>")
    vi.unstubAllEnvs()
  })
})
