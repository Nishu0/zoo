import { Check, Copy, Link2, Trash2 } from "lucide-react"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useCreateViewerLink,
  useRevokeViewerLink,
  useViewerLinks,
} from "@/lib/api_client"
import type { Sandbox, ViewerLinkMode } from "@/lib/api_client"
import { relativeTime } from "@/lib/utils"

export const LINK_EXPIRY = [
  { value: "15", label: "15 minutes" },
  { value: "60", label: "1 hour" },
  { value: "480", label: "8 hours" },
  { value: "1440", label: "24 hours" },
]

export const LINK_MODES: Record<ViewerLinkMode, string> = {
  view: "View only",
  control: "View and control",
}

export function ShareTab({ sandbox }: { sandbox: Sandbox }) {
  const links = useViewerLinks(sandbox.id)
  const create = useCreateViewerLink(sandbox.id)
  const revoke = useRevokeViewerLink(sandbox.id)
  const [mode, setMode] = useState<ViewerLinkMode>("view")
  const [expiry, setExpiry] = useState("60")
  const [endUser, setEndUser] = useState("")
  const [copied, setCopied] = useState<string | null>(null)
  const made = create.data
  const error = create.error ?? revoke.error ?? links.error

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
    } catch {
      setCopied(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Share the screen</CardTitle>
        <CardDescription>
          A link opens this sandbox&apos;s screen without signing in: for an end
          user, a support person, or an iframe in your own app (the API&apos;s
          ZOO_EMBED_ORIGINS must list its origin). View-only links can&apos;t
          type or click. With control, acting while the agent runs takes the
          screen over until it is handed back.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-2 sm:flex-row sm:flex-wrap"
          onSubmit={(event) => {
            event.preventDefault()
            setCopied(null)
            create.mutate({
              mode,
              expires_in_minutes: Number(expiry),
              end_user: endUser.trim() || null,
            })
          }}
        >
          <Select
            value={mode}
            onValueChange={(value) => value && setMode(value)}
          >
            <SelectTrigger className="sm:w-48">
              <SelectValue>{LINK_MODES[mode]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(LINK_MODES).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={expiry}
            onValueChange={(value) => value && setExpiry(value)}
          >
            <SelectTrigger className="sm:w-40">
              <SelectValue>
                {LINK_EXPIRY.find((e) => e.value === expiry)?.label}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {LINK_EXPIRY.map((e) => (
                <SelectItem key={e.value} value={e.value}>
                  {e.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="For (optional), e.g. alice@example.com"
            value={endUser}
            maxLength={200}
            onChange={(event) => setEndUser(event.target.value)}
            className="sm:w-64"
          />
          <Button type="submit" disabled={create.isPending}>
            <Link2 />
            Create link
          </Button>
        </form>
        {made && (
          <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
            <p className="text-sm">
              Copy the link now; it isn&apos;t shown again.
            </p>
            <div className="flex gap-2">
              <Input readOnly value={made.url} className="font-mono text-xs" />
              <Button
                type="button"
                variant="outline"
                onClick={() => void copy(made.url, "url")}
              >
                {copied === "url" ? <Check /> : <Copy />}
                Copy
              </Button>
            </div>
            <div className="flex gap-2">
              <Input
                readOnly
                value={`<iframe src="${made.url}" style="width:100%;height:600px;border:0"></iframe>`}
                className="font-mono text-xs"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  void copy(
                    `<iframe src="${made.url}" style="width:100%;height:600px;border:0"></iframe>`,
                    "iframe"
                  )
                }
              >
                {copied === "iframe" ? <Check /> : <Copy />}
                Embed
              </Button>
            </div>
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error.message}</p>}
        {links.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : !links.data?.length ? (
          <p className="text-sm text-muted-foreground">No links yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {links.data.map((link) => {
              const expired =
                new Date(`${link.expires_at.replace(" ", "T")}Z`) < new Date()
              const live = !link.revoked_at && !expired
              return (
                <li
                  key={link.id}
                  className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      {LINK_MODES[link.mode]}
                      {link.end_user && (
                        <span className="text-muted-foreground">
                          for {link.end_user}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {link.revoked_at
                        ? `Revoked ${relativeTime(link.revoked_at)}`
                        : expired
                          ? `Expired ${relativeTime(link.expires_at)}`
                          : `Expires ${relativeTime(link.expires_at)}`}
                      {` · made ${relativeTime(link.created_at)}`}
                    </div>
                  </div>
                  {live && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(link.id)}
                    >
                      <Trash2 />
                      Revoke
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
