import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { Mail } from "lucide-react"

import { Page } from "@/components/page"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ApiError,
  useAcceptInvitation,
  useMe,
  useSwitchWorkspace,
} from "@/lib/api_client"

export const Route = createFileRoute("/_app/invite/$token")({
  component: InvitePage,
})

function InvitePage() {
  const { token } = Route.useParams()
  const { data: me } = useMe()
  const accept = useAcceptInvitation()
  const switchTo = useSwitchWorkspace()
  const navigate = useNavigate()

  return (
    <Page icon={Mail} title="Workspace invitation">
      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>Join a workspace</CardTitle>
          <CardDescription>
            You're signed in as {me?.email}. The invitation works only for the
            address it was sent to.
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex flex-col items-start gap-2 border-t border-border pt-4">
          {accept.error && (
            <p className="text-sm text-destructive">
              {accept.error instanceof ApiError
                ? accept.error.message
                : "Couldn't accept the invitation."}
            </p>
          )}
          <Button
            disabled={accept.isPending}
            onClick={() =>
              accept.mutate(token, {
                onSuccess: (workspace) => {
                  switchTo(workspace)
                  navigate({ to: "/sandboxes" })
                },
              })
            }
          >
            Accept and switch to it
          </Button>
        </CardFooter>
      </Card>
    </Page>
  )
}
