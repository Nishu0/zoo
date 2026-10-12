import { Globe, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { z } from "zod"
import { PolicyNotice } from "@/components/sandbox/permissions"
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
import { Switch } from "@/components/ui/switch"
import {
  networkRuleInputSchema,
  useAddRule,
  useNetwork,
  useRemoveRule,
  useSetNetwork,
} from "@/lib/api_client"
import type { NetworkRuleInput } from "@/lib/api_client"

export const RULE_TYPES = [
  { value: "domain", label: "Domain" },
  { value: "ip", label: "IP" },
  { value: "cidr", label: "CIDR" },
]

export const EFFECTS = [
  { value: "allow", label: "Allow" },
  { value: "deny", label: "Deny" },
]

export function NetworkTab({ sandboxId }: { sandboxId: string }) {
  const network = useNetwork(sandboxId)
  const setNetwork = useSetNetwork(sandboxId)
  const addRule = useAddRule(sandboxId)
  const removeRule = useRemoveRule(sandboxId)
  const [rule, setRule] = useState<NetworkRuleInput>({
    rule_type: "domain",
    value: "",
    effect: "allow",
  })
  const [ruleError, setRuleError] = useState<string>()

  function onAddRule(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = networkRuleInputSchema.safeParse(rule)
    if (!parsed.success) {
      setRuleError(z.flattenError(parsed.error).fieldErrors.value?.[0])
      return
    }
    setRuleError(undefined)
    addRule.mutate(parsed.data, {
      onSuccess: () => setRule((r) => ({ ...r, value: "" })),
    })
  }

  if (network.isPending) return <Skeleton className="h-64 rounded-xl" />
  if (network.error)
    return <p className="text-sm text-destructive">{network.error.message}</p>

  const policy = network.data
  const error = setNetwork.error ?? addRule.error ?? removeRule.error

  return (
    <div className="flex flex-col gap-4">
      <PolicyNotice />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe className="size-4" />
            Egress policy
          </CardTitle>
          <CardDescription>
            Default action for outbound traffic that no rule matches.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col divide-y divide-border">
          <div className="flex items-center justify-between pb-3">
            <div>
              <p className="text-sm font-medium">Block by default</p>
              <p className="text-xs text-muted-foreground">
                Only destinations allowed below are reachable.
              </p>
            </div>
            <Switch
              checked={policy.default_action === "deny"}
              disabled={setNetwork.isPending}
              onCheckedChange={(checked) =>
                setNetwork.mutate({
                  default_action: checked ? "deny" : "allow",
                  allow_dns: policy.allow_dns,
                })
              }
            />
          </div>
          <div className="flex items-center justify-between pt-3">
            <div>
              <p className="text-sm font-medium">Allow DNS</p>
              <p className="text-xs text-muted-foreground">
                Let the sandbox resolve hostnames.
              </p>
            </div>
            <Switch
              checked={policy.allow_dns}
              disabled={setNetwork.isPending}
              onCheckedChange={(checked) =>
                setNetwork.mutate({
                  default_action: policy.default_action,
                  allow_dns: checked,
                })
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Rules</CardTitle>
          <CardDescription>
            Allow or deny specific destinations.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form onSubmit={onAddRule} className="flex flex-wrap gap-2">
            <Select
              items={RULE_TYPES}
              value={rule.rule_type}
              onValueChange={(value) =>
                value && setRule((r) => ({ ...r, rule_type: value }))
              }
            >
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RULE_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={rule.value}
              onChange={(e) =>
                setRule((r) => ({ ...r, value: e.target.value }))
              }
              placeholder={
                rule.rule_type === "domain"
                  ? "github.com"
                  : rule.rule_type === "ip"
                    ? "1.1.1.1"
                    : "10.0.0.0/8"
              }
              className="h-8 min-w-48 flex-1"
            />
            <Select
              items={EFFECTS}
              value={rule.effect}
              onValueChange={(value) =>
                value && setRule((r) => ({ ...r, effect: value }))
              }
            >
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EFFECTS.map((e) => (
                  <SelectItem key={e.value} value={e.value}>
                    {e.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button type="submit" disabled={addRule.isPending}>
              <Plus />
              Add rule
            </Button>
          </form>
          {(ruleError || error) && (
            <p className="text-sm text-destructive">
              {ruleError ?? error?.message}
            </p>
          )}
          {policy.rules.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No rules yet.
            </p>
          ) : (
            <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {policy.rules.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      className={
                        r.effect === "allow"
                          ? "w-12 text-xs font-medium text-emerald-400"
                          : "w-12 text-xs font-medium text-red-400"
                      }
                    >
                      {r.effect}
                    </span>
                    <span className="w-14 text-xs text-muted-foreground uppercase">
                      {r.rule_type}
                    </span>
                    <span className="truncate font-mono">{r.value}</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove rule"
                    disabled={removeRule.isPending}
                    onClick={() => removeRule.mutate(r.id)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
