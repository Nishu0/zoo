import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { z } from "zod"

import { AuthShell, FieldError } from "@/components/auth-shell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { registerSchema, useMe, useRegister } from "@/lib/api_client"
import type { RegisterInput } from "@/lib/api_client"

export const Route = createFileRoute("/register")({ component: RegisterPage })

type FieldErrors = Partial<Record<keyof RegisterInput, string>>

const fields: {
  key: keyof RegisterInput
  label: string
  type: string
  autoComplete: string
  placeholder: string
}[] = [
  {
    key: "name",
    label: "Name",
    type: "text",
    autoComplete: "name",
    placeholder: "Ada Lovelace",
  },
  {
    key: "email",
    label: "Email",
    type: "email",
    autoComplete: "email",
    placeholder: "you@example.com",
  },
  {
    key: "password",
    label: "Password",
    type: "password",
    autoComplete: "new-password",
    placeholder: "At least 8 characters",
  },
  {
    key: "confirmPassword",
    label: "Confirm password",
    type: "password",
    autoComplete: "new-password",
    placeholder: "••••••••",
  },
]

function RegisterPage() {
  const navigate = useNavigate()
  const me = useMe()
  const register = useRegister()
  const [form, setForm] = useState<RegisterInput>({
    name: "",
    email: "",
    password: "",
    confirmPassword: "",
  })
  const [errors, setErrors] = useState<FieldErrors>({})

  useEffect(() => {
    if (me.data) navigate({ to: "/", replace: true })
  }, [me.data, navigate])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const parsed = registerSchema.safeParse(form)
    if (!parsed.success) {
      const { fieldErrors } = z.flattenError(parsed.error)
      setErrors(
        Object.fromEntries(
          Object.entries(fieldErrors).map(([k, v]) => [k, v[0]])
        )
      )
      return
    }
    setErrors({})
    register.mutate(parsed.data, { onSuccess: () => navigate({ to: "/" }) })
  }

  return (
    <AuthShell
      title="Create your account"
      description="Sign up to start managing your fleet."
      footer={
        <>
          Already have an account?{" "}
          <Link
            to="/login"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
        {fields.map((field) => (
          <div key={field.key} className="flex flex-col gap-1.5">
            <Label htmlFor={field.key}>{field.label}</Label>
            <Input
              id={field.key}
              type={field.type}
              autoComplete={field.autoComplete}
              value={form[field.key]}
              onChange={(event) =>
                setForm({ ...form, [field.key]: event.target.value })
              }
              placeholder={field.placeholder}
              aria-invalid={!!errors[field.key]}
            />
            <FieldError message={errors[field.key]} />
          </div>
        ))}
        {register.error && (
          <p className="text-sm text-destructive">{register.error.message}</p>
        )}
        <Button
          type="submit"
          className="mt-1 w-full"
          disabled={register.isPending}
        >
          {register.isPending ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthShell>
  )
}
