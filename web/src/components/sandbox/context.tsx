import { createContext, useContext } from "react"

import type { Sandbox } from "@/lib/api_client"

export const SandboxContext = createContext<Sandbox | null>(null)

/** The sandbox the page is about; tab routes render only once it has loaded. */
export function useCurrentSandbox(): Sandbox {
  const sandbox = useContext(SandboxContext)
  if (!sandbox) throw new Error("useCurrentSandbox outside a sandbox page")
  return sandbox
}
