import { MessagesSquare, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { PLATFORMS } from "@/components/sandbox/agent"
import { Badge } from "@/components/ui/badge"
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
import {
  agentChannelInputSchema,
  apiUrl,
  useAddAgentChannel,
  useAgentChannels,
  useIntegrations,
  useRemoveAgentChannel,
  useUpdateAgentChannel,
} from "@/lib/api_client"
import type { AgentChannel, ChatPlatform } from "@/lib/api_client"
import { timeAgo } from "@/lib/utils"

export function AgentChannels({ sandboxId }: { sandboxId: string }) {
  const channels = useAgentChannels(sandboxId)
  const integrations = useIntegrations()
  const add = useAddAgentChannel(sandboxId)
  const remove = useRemoveAgentChannel(sandboxId)
  const update = useUpdateAgentChannel(sandboxId)
  const [platform, setPlatform] = useState<ChatPlatform>("slack")
  const [externalId, setExternalId] = useState("")
  const [allowed, setAllowed] = useState("")
  const [error, setError] = useState<string | null>(null)

  const selected = PLATFORMS.find((p) => p.value === platform)!
  const integration = integrations.data?.find((i) => i.platform === platform)

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = agentChannelInputSchema.safeParse({
      platform,
      external_id: externalId,
      allowed_users: platform === "whatsapp" ? [] : splitUsers(allowed),
    })
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid channel")
      return
    }
    if (platform !== "whatsapp" && parsed.data.allowed_users.length === 0) {
      setError(
        "List the user IDs that may command this sandbox, or * for anyone in the channel."
      )
      return
    }
    setError(null)
    add.mutate(parsed.data, {
      onSuccess: () => {
        setExternalId("")
        setAllowed("")
      },
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessagesSquare className="size-4" />
          Chat channels
        </CardTitle>
        <CardDescription>
          Messages in a linked Slack or Discord channel, or from a linked
          WhatsApp number, go to this sandbox's agent and its progress streams
          back. In Slack and Discord only the user IDs you allow can command it
          (* lets anyone in the channel); a WhatsApp link only answers its own
          number. Send <span className="font-mono">stop</span> to cancel a task
          or <span className="font-mono">reset</span> to clear the conversation.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form onSubmit={onSubmit} className="flex flex-wrap gap-2">
          <Select
            items={PLATFORMS}
            value={platform}
            onValueChange={(value) => value && setPlatform(value)}
          >
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PLATFORMS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
            placeholder={selected.placeholder}
            className="min-w-48 flex-1"
          />
          {platform !== "whatsapp" && (
            <Input
              value={allowed}
              onChange={(e) => setAllowed(e.target.value)}
              placeholder={
                platform === "slack"
                  ? "Allowed user IDs, e.g. U0123ABCD, U0456EFGH, or *"
                  : "Allowed user IDs, comma-separated, or *"
              }
              aria-label="Allowed user IDs"
              className="min-w-64 basis-full"
            />
          )}
          <Button type="submit" disabled={add.isPending}>
            <Plus />
            Link
          </Button>
        </form>
        {integration && !integration.configured && (
          <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            {selected.label} isn't set up on this server yet. Set{" "}
            <span className="font-mono">{selected.env}</span> on the API
            {integration.webhook_path && (
              <>
                {" "}
                and point the webhook at{" "}
                <span className="font-mono break-all">
                  {apiUrl(integration.webhook_path)}
                </span>
              </>
            )}
            .
          </p>
        )}
        {(error ??
          add.error?.message ??
          remove.error?.message ??
          update.error?.message) && (
          <p className="text-sm text-destructive">
            {error ??
              add.error?.message ??
              remove.error?.message ??
              update.error?.message}
          </p>
        )}
        {channels.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {channels.data.map((c) => (
              <ChannelRow
                key={c.id}
                channel={c}
                saving={update.isPending}
                removing={remove.isPending}
                onSave={(users) =>
                  update.mutate({ channelId: c.id, allowedUsers: users })
                }
                onRemove={() => remove.mutate(c.id)}
              />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No linked channels.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function splitUsers(value: string) {
  return value
    .split(/[\s,]+/)
    .map((u) => u.trim())
    .filter(Boolean)
}

export function ChannelRow({
  channel,
  saving,
  removing,
  onSave,
  onRemove,
}: {
  channel: AgentChannel
  saving: boolean
  removing: boolean
  onSave: (users: Array<string>) => void
  onRemove: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(channel.allowed_users.join(", "))
  const anyone = channel.allowed_users.includes("*")
  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">
            {PLATFORMS.find((p) => p.value === channel.platform)?.label}
          </Badge>
          <span className="font-mono">{channel.external_id}</span>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          Linked {timeAgo(channel.created_at)}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Unlink channel"
            disabled={removing}
            onClick={onRemove}
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      {channel.platform !== "whatsapp" &&
        (editing ? (
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              const users = splitUsers(draft)
              if (!users.length) return
              onSave(users)
              setEditing(false)
            }}
          >
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="Allowed user IDs"
              className="min-w-48 flex-1 font-mono"
            />
            <Button
              type="submit"
              size="sm"
              disabled={saving || !splitUsers(draft).length}
            >
              Save
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {anyone ? (
              <span className="text-amber-600 dark:text-amber-400">
                Anyone in the channel can command this sandbox
              </span>
            ) : (
              <span>
                Allowed:{" "}
                <span className="font-mono">
                  {channel.allowed_users.join(", ")}
                </span>
              </span>
            )}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => {
                setDraft(channel.allowed_users.join(", "))
                setEditing(true)
              }}
            >
              Edit
            </button>
          </div>
        ))}
    </div>
  )
}
