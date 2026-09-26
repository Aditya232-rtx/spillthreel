# Changelog

## Unreleased — Production-grade authentication

Why: the auth plumbing (Supabase JWT verify, PKCE OAuth, AuthGate) worked,
but the surrounding flows had gaps that blocked launch: no password reset,
weak password rules, web-only Apple sign-in (an App Store rejection risk),
session tokens in device logs, no handling for dead sessions, silent env
fallbacks, and zero backend rate limiting with wide-open CORS.

### Mobile (`apps/mobile`)
- **Password reset**: new `(auth)/forgot-password` screen (email →
  `resetPasswordForEmail` with deep-link `redirectTo`) and `auth/reset-password`
  deep-link landing screen (verifies the one-time link, new-password +
  confirm fields, `updateUser`). Login's "Forgot password?" link is wired up.
  `AuthGate` exempts the reset screen so the recovery session isn't bounced
  to home mid-flow.
- **Password strength**: shared `src/lib/password.ts` (8+ chars, ≥1 letter,
  ≥1 number). Enforced on signup and reset before any network call, with a
  confirm-password field on both screens. Breach screening is intentionally
  left to Supabase's dashboard-side leaked-password protection.
- **Native Sign In with Apple on iOS**: `performNativeAppleSignIn`
  (`expo-apple-authentication` + `expo-crypto` nonce, `signInWithIdToken`),
  auto-selected for Apple on iOS with fallback to web OAuth when unavailable.
  Android/web keep the existing web flow.
- **Log hygiene**: all `oauth.ts` logging is `__DEV__`-gated; callback URLs
  are sanitized (`access_token`/`refresh_token`/`code`/`id_token` →
  `[redacted]`) before printing.
- **Session expiry**: authenticated API calls that still get HTTP 401 now
  `signOut()` locally before throwing, so `AuthGate` routes to welcome
  automatically.
- **Env**: missing `EXPO_PUBLIC_SUPABASE_URL` / `..._PUBLISHABLE_KEY` throws
  at boot instead of falling back to baked-in project values. Added
  `apps/mobile/.env.example`.

### Backend (`apps/api`)
- **Rate limiting** (`slowapi`, new `app/ratelimit.py`): 60 req/min default
  on every route, keyed by JWT `sub` (unverified read, bucketing only —
  auth is still enforced by the middleware) falling back to client IP
  (X-Forwarded-For aware). Stricter 10/min on `POST /v1/saves` (paid LLM
  path). Breaches return 429 with `Retry-After: 60`. Limits resolve from
  settings per-request (`RATE_LIMIT_DEFAULT`, `RATE_LIMIT_SAVES`).
- **CORS**: `"*"` only in dev; staging/prod require explicit
  `CORS_ALLOWED_ORIGINS` and refuse to boot without it.
- **Tests**: `test_auth_security.py` (expired / malformed / wrong-audience /
  HS256-without-secret / unsupported-alg JWTs) and
  `test_rate_limit.py` (429 + `Retry-After` on the Nth request, global and
  per-route paths).

## Unreleased — Dynamic identity, verify nudges & chat rebuild (2026-09-26)

Why: real-device testing showed the auth loop worked but identity was frozen
(hardcoded "Aditya" everywhere, renames never stuck), pending-confirmation
users dead-ended, and the chat screen was static demo content with broken
proportions on native.

### Mobile (`apps/mobile`)
- **Dynamic identity**: home greeting + avatar initial + profile (name,
  handle, join date, avatar initial) all read the live session's
  `user_metadata` via new `src/lib/display-name.ts`. New `(auth)/your-name`
  screen in the OAuth-signup path (prefilled, skippable) writes the name
  before import. Profile name is tappable → inline editor.
- **Duplicate-signup rename fix**: signing up on an existing address used to
  discard the typed name; it now applies it via `updateUser`, and an
  inline "already has an account — log in instead" note shows under the
  email field instead of a dead-end "check your email".
- **Last-choice-wins guard**: explicit name choices are remembered per email
  and re-applied on every `SIGNED_IN`, so a later Google login can't
  silently overwrite them with the provider profile name.
- **Confirmation nudge flow**: pending-confirmation signups proceed to the
  import guide (AuthGate + index exempt pending users from the welcome
  bounce); `emailRedirectTo` returns link taps to the app; import3 shows a
  confirm card with working resend; home shows a coral dot on the avatar +
  auto-popup anchored at the profile icon (dismiss persists). Shared
  `src/lib/verification.ts` backs it.
- **Chat rebuild**: live per-user conversations (no more canned thread),
  typing indicator, honest stub replies until RAG lands, persisted history
  drawer (bottom hamburger, slide-in panel, new/delete/switch), mic/send
  morph button (coral mic, hold-to-talk with Web Speech on web), suggestion
  chips that send on tap, fixed-height chip row, back-chevron header drawn
  as SVG (optically centered), frosted-glass back buttons matching the dock.
- **Welcome wordmark** now coral; login "forgot password" moved below the
  Log in button as minimal coral text.

### Backend (`apps/api`)
- **Profile display-name sync**: auth middleware updates
  `profiles.display_name` from JWT claims whenever it changes (was
  write-once at creation), so DB mirrors every rename from either flow.
