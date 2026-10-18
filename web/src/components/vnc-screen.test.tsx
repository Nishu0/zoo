// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { VncScreen } from "./vnc-screen"

const { clients } = vi.hoisted(() => ({
  clients: [] as Array<
    EventTarget & { disconnect: () => void; showDotCursor: boolean }
  >,
}))

vi.mock("@novnc/novnc", () => ({
  default: class extends EventTarget {
    constructor(target: HTMLElement) {
      super()
      target.append(document.createElement("canvas"))
      clients.push(this)
    }
    showDotCursor = false
    disconnect = vi.fn(() => this.dispatchEvent(new Event("disconnect")))
    focus = vi.fn()
  },
}))

async function settle() {
  await act(async () => {})
}

describe("VncScreen connection lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    clients.length = 0
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    })
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("keeps noVNC's software dot disabled when the CSS pointer reconnects", async () => {
    render(
      <VncScreen socketUrl={async () => "ws://base-vm"} active message={null} />
    )
    await settle()
    act(() => clients[0].dispatchEvent(new Event("connect")))
    expect(clients[0].showDotCursor).toBe(false)

    act(() => clients[0].dispatchEvent(new Event("disconnect")))
    await act(async () => vi.advanceTimersByTime(1500))
    await settle()
    expect(clients).toHaveLength(2)
    expect(clients[1].showDotCursor).toBe(false)
  })

  it("opens a fresh connection when returning to a suspended tab", async () => {
    render(
      <VncScreen socketUrl={async () => "ws://first"} active message={null} />
    )
    await settle()
    act(() => clients[0].dispatchEvent(new Event("connect")))
    Object.defineProperty(document, "visibilityState", { value: "hidden" })
    act(() => document.dispatchEvent(new Event("visibilitychange")))
    Object.defineProperty(document, "visibilityState", { value: "visible" })
    act(() => document.dispatchEvent(new Event("visibilitychange")))
    await settle()
    expect(clients).toHaveLength(2)
    expect(clients[0].disconnect).toHaveBeenCalledOnce()
  })

  it("ignores late events from the previous sandbox", async () => {
    const first = async () => "ws://first"
    const second = async () => "ws://second"
    const view = render(<VncScreen socketUrl={first} active message={null} />)
    await settle()
    const old = clients[0]
    view.rerender(<VncScreen socketUrl={second} active message={null} />)
    await settle()
    act(() => clients[1].dispatchEvent(new Event("connect")))
    act(() => old.dispatchEvent(new Event("disconnect")))
    expect(screen.queryByText("Connecting…")).toBeNull()
    await act(async () => vi.advanceTimersByTime(2000))
    expect(clients).toHaveLength(2)
  })

  it("reconnects when the network comes back", async () => {
    render(
      <VncScreen socketUrl={async () => "ws://first"} active message={null} />
    )
    await settle()
    act(() => window.dispatchEvent(new Event("online")))
    await settle()
    expect(clients).toHaveLength(2)
  })
})
