import { Plus, Trash2 } from "lucide-react"
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
import {
  useApplyProfile,
  useCaptureProfile,
  useProfileApps,
  useProfiles,
  useProfileVersions,
  useRemoveProfileVersion,
} from "@/lib/api_client"
import type { Profile } from "@/lib/api_client"
import { formatBytes, timeAgo } from "@/lib/utils"

export function ProfilesTab({
  sandboxId,
  running,
  platform,
}: {
  sandboxId: string
  running: boolean
  platform: string
}) {
  const allProfiles = useProfiles()
  const profiles = {
    data: allProfiles.data?.filter((p) => p.platform === platform),
  }
  const apps = useProfileApps(platform)
  const capture = useCaptureProfile(sandboxId)
  const apply = useApplyProfile(sandboxId)
  const [app, setApp] = useState("firefox")
  const [name, setName] = useState("")
  const appItems = Object.keys(apps.data ?? { firefox: "" }).map((a) => ({
    value: a,
    label: a,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>App profiles</CardTitle>
        <CardDescription>
          Save an app's logins, cookies and settings from this sandbox, then
          load them into any sandbox. Saving under an existing name adds a
          version; load the latest or any earlier one. Loading is refused while
          the app is running, so quit it first.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault()
            if (!name.trim()) return
            capture.mutate(
              { name: name.trim(), app },
              { onSuccess: () => setName("") }
            )
          }}
        >
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Profile name"
            className="flex-1"
          />
          <Select
            items={appItems}
            value={app}
            onValueChange={(next) => next && setApp(next)}
          >
            <SelectTrigger className="sm:w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {appItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="submit"
            disabled={!running || capture.isPending || !name.trim()}
          >
            <Plus />
            {capture.isPending ? "Saving…" : "Save from sandbox"}
          </Button>
        </form>
        {(capture.error ?? apply.error) && (
          <p className="text-sm text-destructive">
            {(capture.error ?? apply.error)?.message}
          </p>
        )}
        {profiles.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {profiles.data.map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                running={running}
                apply={apply}
                saving={capture.isPending}
                onSaveVersion={() =>
                  capture.mutate({ name: p.name, app: p.app, profile_id: p.id })
                }
              />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No saved profiles yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function ProfileRow({
  profile,
  running,
  apply,
  saving,
  onSaveVersion,
}: {
  profile: Profile
  running: boolean
  apply: ReturnType<typeof useApplyProfile>
  saving: boolean
  onSaveVersion: () => void
}) {
  const versions = useProfileVersions(profile.versions > 1 ? profile.id : null)
  const removeVersion = useRemoveProfileVersion()
  // "" loads the latest
  const [version, setVersion] = useState("")
  const items = [
    { value: "", label: `Latest (v${profile.version})` },
    ...(versions.data ?? []).slice(1).map((v) => ({
      value: String(v.version),
      label: `v${v.version} · ${timeAgo(v.created_at)}`,
    })),
  ]
  const mine = apply.variables?.profileId === profile.id
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
      <div>
        <div className="font-medium">{profile.name}</div>
        <div className="text-xs text-muted-foreground">
          {profile.app} · v{profile.version}
          {profile.versions > 1 && ` (${profile.versions} versions)`} ·{" "}
          {formatBytes(profile.size_bytes)} ·{" "}
          {timeAgo(profile.updated_at ?? profile.created_at)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {profile.versions > 1 && (
          <Select
            items={items}
            value={version}
            onValueChange={(next) => setVersion(next ?? "")}
          >
            <SelectTrigger className="w-44" aria-label="Version to load">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {version && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete v${version}`}
            disabled={removeVersion.isPending}
            onClick={() =>
              removeVersion.mutate(
                { id: profile.id, version: Number(version) },
                { onSuccess: () => setVersion("") }
              )
            }
          >
            <Trash2 />
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={!running || saving}
          onClick={onSaveVersion}
          title="Save this sandbox's app data as a new version"
        >
          Save new version
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!running || apply.isPending}
          onClick={() =>
            apply.mutate({
              profileId: profile.id,
              version: version ? Number(version) : undefined,
            })
          }
        >
          {apply.isPending && mine
            ? "Loading…"
            : apply.isSuccess && mine
              ? "Loaded"
              : "Load"}
        </Button>
      </div>
      {removeVersion.error && (
        <p className="basis-full text-xs text-destructive">
          {removeVersion.error.message}
        </p>
      )}
    </div>
  )
}
