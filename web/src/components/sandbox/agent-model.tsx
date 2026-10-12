import { Cog, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { compact } from "@/components/sandbox/agent"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  agentSettingsInputSchema,
  useAgentSettings,
  useSaveAgentSettings,
  useVaultSecrets,
} from "@/lib/api_client"
import type { AgentProviderId } from "@/lib/api_client"

export function AgentModel() {
  const settings = useAgentSettings()
  const save = useSaveAgentSettings()
  const [provider, setProvider] = useState<AgentProviderId>("anthropic")
  const [model, setModel] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [apiBase, setApiBase] = useState("")
  // "" means a key typed in (or none); otherwise the vault secret to use
  const [keySecret, setKeySecret] = useState("")
  const [maxSteps, setMaxSteps] = useState("")
  const [maxMinutes, setMaxMinutes] = useState("")
  const [maxTokens, setMaxTokens] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const vault = useVaultSecrets()

  const data = settings.data
  // Load the saved choice into the form; with none saved, start from the first provider's defaults.
  useEffect(() => {
    if (!data) return
    const current =
      data.providers.find((p) => p.id === data.provider) ?? data.providers[0]
    setProvider(current.id)
    setModel(data.provider ? data.model : current.default_model)
    setApiBase(data.api_base ?? "")
    setApiKey("")
    setKeySecret(data.api_key_secret?.id ?? "")
    // a limit equal to the server's cap is shown blank: "use the cap"
    const own = (value: number, cap: number) =>
      value < cap ? String(value) : ""
    setMaxSteps(own(data.limits.max_steps, data.caps.max_steps))
    setMaxMinutes(
      data.limits.max_seconds < data.caps.max_seconds
        ? String(Math.round(data.limits.max_seconds / 60))
        : ""
    )
    setMaxTokens(own(data.limits.max_tokens, data.caps.max_tokens))
  }, [data])

  if (!data) {
    return settings.error ? null : <Skeleton className="h-48" />
  }

  const providers = data.providers.map((p) => ({ value: p.id, label: p.label }))
  const selected = data.providers.find((p) => p.id === provider)!
  const keySaved = data.has_api_key && data.provider === provider
  const secretItems = [
    { value: "", label: "No vault secret" },
    ...(vault.data ?? []).map((v) => ({ value: v.id, label: v.name })),
  ]

  function onProviderChange(next: AgentProviderId) {
    const target = data!.providers.find((p) => p.id === next)!
    setProvider(next)
    setModel(next === data!.provider ? data!.model : target.default_model)
    setApiBase(next === data!.provider ? (data!.api_base ?? "") : "")
    setApiKey("")
    setKeySecret(
      next === data!.provider ? (data!.api_key_secret?.id ?? "") : ""
    )
    setSaved(false)
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const number = (value: string) =>
      value.trim() ? Number(value.trim()) : undefined
    const minutes = number(maxMinutes)
    const parsed = agentSettingsInputSchema.safeParse({
      provider,
      model,
      api_key: apiKey.trim() || undefined,
      // a typed key wins; otherwise the chosen vault secret, or "" to drop the key
      api_key_secret_id: apiKey.trim() ? undefined : keySecret,
      api_base: apiBase,
      max_steps: number(maxSteps),
      max_seconds: minutes === undefined ? undefined : Math.round(minutes * 60),
      max_tokens: number(maxTokens),
    })
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid settings")
      return
    }
    if (
      selected.needs_key &&
      !selected.server_key &&
      !parsed.data.api_key &&
      !parsed.data.api_key_secret_id
    ) {
      setError(`Enter your ${selected.label} API key.`)
      return
    }
    setError(null)
    save.mutate(parsed.data, { onSuccess: () => setSaved(true) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cog className="size-4" />
          Model
        </CardTitle>
        <CardDescription>
          {data.provider ? (
            <>
              The agent uses <span className="font-mono">{data.model}</span>{" "}
              from {data.providers.find((p) => p.id === data.provider)?.label}.
            </>
          ) : (
            <>
              The agent uses the server default,{" "}
              <span className="font-mono">{data.default_model}</span>.
            </>
          )}{" "}
          Applies to the agent in all your sandboxes. Pick a model that supports
          computer use. Keys are kept in the vault.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <Select
              items={providers}
              value={provider}
              onValueChange={(value) => value && onProviderChange(value)}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={model}
              onChange={(e) => {
                setModel(e.target.value)
                setSaved(false)
              }}
              placeholder={selected.default_model}
              aria-label="Model"
              className="min-w-48 flex-1 font-mono"
            />
          </div>
          {selected.needs_key && (
            <div className="flex flex-wrap gap-2">
              <Select
                items={secretItems}
                value={keySecret}
                onValueChange={(value) => {
                  setKeySecret(value ?? "")
                  setSaved(false)
                }}
              >
                <SelectTrigger className="w-56" aria-label="Key from the vault">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {secretItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value)
                  setSaved(false)
                }}
                aria-label="API key"
                className="min-w-48 flex-1"
                placeholder={
                  keySaved && keySecret
                    ? "Or enter a new key to save it to the vault"
                    : selected.server_key
                      ? "Optional. The server's key is used if blank"
                      : `${selected.label} API key (saved to the vault)`
                }
              />
            </div>
          )}
          <Input
            value={apiBase}
            onChange={(e) => {
              setApiBase(e.target.value)
              setSaved(false)
            }}
            aria-label="Base URL"
            placeholder={
              selected.default_base
                ? `Base URL, default ${selected.default_base}`
                : "Base URL (optional, for a proxy or gateway)"
            }
            className="font-mono"
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">
              Limits per task. Blank uses the server's cap.
            </span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <Input
                inputMode="numeric"
                value={maxSteps}
                onChange={(e) => {
                  setMaxSteps(e.target.value)
                  setSaved(false)
                }}
                aria-label="Most actions per task"
                placeholder={`Actions, up to ${data.caps.max_steps}`}
              />
              <Input
                inputMode="numeric"
                value={maxMinutes}
                onChange={(e) => {
                  setMaxMinutes(e.target.value)
                  setSaved(false)
                }}
                aria-label="Most minutes per task"
                placeholder={`Minutes, up to ${Math.round(data.caps.max_seconds / 60)}`}
              />
              <Input
                inputMode="numeric"
                value={maxTokens}
                onChange={(e) => {
                  setMaxTokens(e.target.value)
                  setSaved(false)
                }}
                aria-label="Most tokens per task"
                placeholder={`Tokens, up to ${compact(data.caps.max_tokens)}`}
              />
            </div>
          </div>
          {(error ?? save.error?.message) && (
            <p className="text-sm text-destructive">
              {error ?? save.error?.message}
            </p>
          )}
          <div className="flex items-center justify-end gap-2">
            {saved && !save.isPending && (
              <span className="text-xs text-muted-foreground">Saved</span>
            )}
            {data.provider && (
              <Button
                type="button"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => {
                  setSaved(false)
                  save.mutate(null)
                }}
              >
                Use server default
              </Button>
            )}
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />}
              Save
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
