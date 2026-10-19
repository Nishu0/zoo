import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { z } from "zod"

import { AuthShell, FieldError } from "@/components/auth-shell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { loginSchema, useLogin, useMe } from "@/lib/api_client"
import type { LoginInput } from "@/lib/api_client"

export const Route = createFileRoute("/login")({ component: LoginPage })

type FieldErrors = Partial<Record<keyof LoginInput, string>>

function LoginPage() {
  const navigate = useNavigate()
  const me = useMe()
  const login = useLogin()
  const [form, setForm] = useState<LoginInput>({ email: "", password: "" })
  const [errors, setErrors] = useState<FieldErrors>({})

  useEffect(() => {
    if (me.data) navigate({ to: "/", replace: true })
  }, [me.data, navigate])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const parsed = loginSchema.safeParse(form)
    if (!parsed.success) {
      const { fieldErrors } = z.flattenError(parsed.error)
      setErrors({
        email: fieldErrors.email?.[0],
        password: fieldErrors.password?.[0],
      })
      return
    }
    setErrors({})
    login.mutate(parsed.data, { onSuccess: () => navigate({ to: "/" }) })
  }

  return (
    <AuthShell
      title="Sign in to your fleet"
      description="Enter your credentials to access the console."
      footer={
        <>
          Don&apos;t have an account?{" "}
          <Link
            to="/register"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Create one
          </Link>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            value={form.email}
            onChange={(event) =>
              setForm({ ...form, email: event.target.value })
            }
            placeholder="you@example.com"
            aria-invalid={!!errors.email}
          />
          <FieldError message={errors.email} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={form.password}
            onChange={(event) =>
              setForm({ ...form, password: event.target.value })
            }
            placeholder="••••••••"
            aria-invalid={!!errors.password}
          />
          <FieldError message={errors.password} />
        </div>
        {login.error && (
          <p className="text-sm text-destructive">{login.error.message}</p>
        )}
        <Button
          type="submit"
          className="mt-1 w-full"
          disabled={login.isPending}
        >
          {login.isPending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthShell>
  )
}
