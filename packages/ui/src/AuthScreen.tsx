import { ApiError } from "@careerpilot/api-client";
import { useHealth } from "@careerpilot/api-client/react";
import { PASSWORD_MIN_LENGTH } from "@careerpilot/contracts";
import { isMessageKey, type Translate } from "@careerpilot/i18n";
import {
  loginFormSchema,
  registerFormSchema,
  type LoginForm,
  type RegisterForm,
} from "@careerpilot/validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Alert, Badge, Button, TextField } from "./components";
import { useAuth, useI18n } from "./providers";

type Mode = "login" | "register";

/** Map a failed sign-in/registration to a message. Never shows server internals. */
export function authErrorMessage(err: unknown, t: Translate): string {
  if (err instanceof ApiError) {
    if (err.kind === "network" || err.kind === "timeout") return t("auth.error.network");
    switch (err.code) {
      case "invalid_credentials":
        return t("auth.error.invalidCredentials");
      case "email_taken":
        return t("auth.error.emailTaken");
      case "account_disabled":
        return t("auth.error.accountDisabled");
      case "rate_limited":
        return t("auth.error.rateLimited", {
          minutes: Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60)),
        });
    }
  }
  return t("auth.error.generic");
}

function fieldError(t: Translate, message: string | undefined): string | undefined {
  if (!message) return undefined;
  return isMessageKey(message) ? t(message) : t("auth.error.generic");
}

function LoginFormView({ onSwitch }: { onSwitch: () => void }) {
  const { t } = useI18n();
  const { login } = useAuth();
  const [failure, setFailure] = useState<string | null>(null);
  const form = useForm<LoginForm>({
    resolver: zodResolver(loginFormSchema),
    defaultValues: { email: "", password: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    setFailure(null);
    try {
      await login(values);
    } catch (err) {
      setFailure(authErrorMessage(err, t));
      form.resetField("password");
    }
  });

  return (
    <form className="cp-auth-form" onSubmit={onSubmit} noValidate data-testid="login-form">
      <header>
        <h1 className="cp-page-title">{t("auth.signIn.title")}</h1>
        <p className="cp-page-subtitle">{t("auth.signIn.subtitle")}</p>
      </header>
      {failure ? <Alert data-testid="auth-error">{failure}</Alert> : null}
      <TextField
        label={t("auth.field.email")}
        type="email"
        autoComplete="username"
        inputMode="email"
        dir="ltr"
        error={fieldError(t, errors.email?.message)}
        {...form.register("email")}
      />
      <TextField
        label={t("auth.field.password")}
        type="password"
        autoComplete="current-password"
        dir="ltr"
        revealLabels={{ show: t("auth.showPassword"), hide: t("auth.hidePassword") }}
        error={fieldError(t, errors.password?.message)}
        {...form.register("password")}
      />
      <Button type="submit" variant="primary" disabled={isSubmitting}>
        {isSubmitting ? t("auth.submitting") : t("auth.signIn.submit")}
      </Button>
      <button type="button" className="cp-link" onClick={onSwitch}>
        {t("auth.signIn.switch")}
      </button>
    </form>
  );
}

function RegisterFormView({ onSwitch }: { onSwitch: () => void }) {
  const { t, locale } = useI18n();
  const { register } = useAuth();
  const [failure, setFailure] = useState<string | null>(null);
  const form = useForm<RegisterForm>({
    resolver: zodResolver(registerFormSchema),
    defaultValues: { email: "", password: "", confirmPassword: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async ({ email, password }) => {
    setFailure(null);
    try {
      await register({ email, password, locale });
    } catch (err) {
      setFailure(authErrorMessage(err, t));
    }
  });

  return (
    <form className="cp-auth-form" onSubmit={onSubmit} noValidate data-testid="register-form">
      <header>
        <h1 className="cp-page-title">{t("auth.register.title")}</h1>
        <p className="cp-page-subtitle">{t("auth.register.subtitle")}</p>
      </header>
      {failure ? <Alert data-testid="auth-error">{failure}</Alert> : null}
      <TextField
        label={t("auth.field.email")}
        type="email"
        autoComplete="email"
        inputMode="email"
        dir="ltr"
        error={fieldError(t, errors.email?.message)}
        {...form.register("email")}
      />
      <TextField
        label={t("auth.field.password")}
        type="password"
        autoComplete="new-password"
        dir="ltr"
        hint={t("auth.passwordHint", { min: PASSWORD_MIN_LENGTH })}
        revealLabels={{ show: t("auth.showPassword"), hide: t("auth.hidePassword") }}
        error={fieldError(t, errors.password?.message)}
        {...form.register("password")}
      />
      <TextField
        label={t("auth.field.confirmPassword")}
        type="password"
        autoComplete="new-password"
        dir="ltr"
        error={fieldError(t, errors.confirmPassword?.message)}
        {...form.register("confirmPassword")}
      />
      <Button type="submit" variant="primary" disabled={isSubmitting}>
        {isSubmitting ? t("auth.submitting") : t("auth.register.submit")}
      </Button>
      <button type="button" className="cp-link" onClick={onSwitch}>
        {t("auth.register.switch")}
      </button>
    </form>
  );
}

/** Compact backend connectivity hint, so sign-in problems are diagnosable. */
function ConnectionHint() {
  const { t } = useI18n();
  const health = useHealth();
  if (health.isPending) return null;
  const ok = health.data?.status === "ok";
  return (
    <Badge
      tone={health.isError ? "danger" : ok ? "success" : "warning"}
      data-testid="auth-connection"
    >
      {health.isError
        ? t("status.unreachable")
        : t(ok ? "status.ok" : health.data?.status === "down" ? "status.down" : "status.degraded")}
    </Badge>
  );
}

/** Sign-in / registration, shared by Desktop and Web. */
export function AuthScreen({ initialMode = "login" }: { initialMode?: Mode }) {
  const { t, locale, setLocale } = useI18n();
  const [mode, setMode] = useState<Mode>(initialMode);

  return (
    <div className="cp-auth">
      <div className="cp-auth-panel">
        <div className="cp-auth-top">
          <div className="cp-brand">
            <span className="cp-brand-name">{t("app.name")}</span>
            <span className="cp-brand-tagline">{t("app.tagline")}</span>
          </div>
          <Button
            data-testid="locale-toggle"
            onClick={() => setLocale(locale === "en" ? "ar" : "en")}
            lang={locale === "en" ? "ar" : "en"}
          >
            {t("locale.switchTo")}
          </Button>
        </div>
        {mode === "login" ? (
          <LoginFormView onSwitch={() => setMode("register")} />
        ) : (
          <RegisterFormView onSwitch={() => setMode("login")} />
        )}
        <div className="cp-auth-footer">
          <ConnectionHint />
        </div>
      </div>
    </div>
  );
}
