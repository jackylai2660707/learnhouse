import * as Sentry from "@sentry/nextjs";
import { redactSentryEvent } from "@/lib/sentry-redaction";

const rc = typeof window !== 'undefined' ? (window as any).__RUNTIME_CONFIG__ || {} : {};
const SENTRY_DSN = rc.NEXT_PUBLIC_LEARNHOUSE_SENTRY_DSN || process.env.NEXT_PUBLIC_LEARNHOUSE_SENTRY_DSN;
const LEARNHOUSE_ENV = rc.NEXT_PUBLIC_LEARNHOUSE_ENV || process.env.NEXT_PUBLIC_LEARNHOUSE_ENV || "dev";

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tunnel: '/monitoring',
    environment: LEARNHOUSE_ENV,
    sendDefaultPii: false,
    enableLogs: false,
    tracesSampleRate: LEARNHOUSE_ENV === "dev" ? 1.0 : 0.1,
    replaysSessionSampleRate: 0.0,
    replaysOnErrorSampleRate: 0.0,
    beforeSend(event, hint) {
      const msg =
        (hint?.originalException as Error)?.message ??
        event?.exception?.values?.[0]?.value ??
        "";

      if (msg.includes("Failed to find Server Action")) return null;
      if (msg.includes("Organization not found")) return null;
      if (msg.includes("Organization has no config")) return null;

      return redactSentryEvent(event);
    },
    beforeSendTransaction(event) {
      return redactSentryEvent(event);
    },
  });
}
