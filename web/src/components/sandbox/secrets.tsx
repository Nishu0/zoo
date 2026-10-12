import { Link } from "@tanstack/react-router"
import { KeyRound, Plus, Trash2, Vault } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import {
  secretInputSchema,
  useAttachedSecrets,
  useRemoveSecret,
  useSecrets,
  useSetSecret,
  useToggleAttachedSecret,
  useVaultSecrets,
} from "@/lib/api_client"
import { timeAgo } from "@/lib/utils"

export function SecretsTab({ sandboxId }: { sandboxId: string }) {
  const secrets = useSecrets(sandboxId)
  const setSecret = useSetSecret(sandboxId)
  const removeSecret = useRemoveSecret(sandboxId)
  const [error, setError] = useState<string | null>(null)

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const parsed = secretInputSchema.safeParse(
      Object.fromEntries(new FormData(form))
    )
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid secret")
      return
    }
    setError(null)
    setSecret.mutate(parsed.data, { onSuccess: () => form.reset() })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" />
          Secrets
        </CardTitle>
        <CardDescription>
          Encrypted at rest and injected as environment variables. Changes apply
          on the next start.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
          <Input name="name" placeholder="API_TOKEN" className="sm:w-56" />
          <Input
            name="value"
            type="password"
            placeholder="Value"
            className="flex-1"
          />
          <Button type="submit" disabled={setSecret.isPending}>
            <Plus />
            Save secret
          </Button>
        </form>
        {(error ?? setSecret.error) && (
          <p className="text-sm text-destructive">
            {error ?? setSecret.error?.message}
          </p>
        )}
        {secrets.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {secrets.data.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <span className="font-mono">{s.name}</span>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  Added {timeAgo(s.created_at)}
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove secret"
                    disabled={removeSecret.isPending}
                    onClick={() => removeSecret.mutate(s.id)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No secrets yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function VaultSecretsCard({ sandboxId }: { sandboxId: string }) {
  const vault = useVaultSecrets()
  const attached = useAttachedSecrets(sandboxId)
  const toggle = useToggleAttachedSecret(sandboxId)
  const on = new Set((attached.data ?? []).map((s) => s.id))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Vault className="size-4" />
          From vault
        </CardTitle>
        <CardDescription>
          Shared secrets from your{" "}
          <Link to="/vault" className="underline underline-offset-2">
            vault
          </Link>
          . Only the ones switched on reach this sandbox, on its next start.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {toggle.error && (
          <p className="text-sm text-destructive">{toggle.error.message}</p>
        )}
        {vault.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {vault.data.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="font-mono">{s.name}</span>
                  {s.description && (
                    <span className="truncate text-xs text-muted-foreground">
                      {s.description}
                    </span>
                  )}
                </div>
                <Switch
                  checked={on.has(s.id)}
                  disabled={toggle.isPending || attached.isLoading}
                  onCheckedChange={(checked) =>
                    toggle.mutate({ id: s.id, attach: checked })
                  }
                />
              </div>
            ))}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-muted-foreground">
            Your vault is empty.{" "}
            <Link to="/vault" className="underline underline-offset-2">
              Add a secret
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  )
}
