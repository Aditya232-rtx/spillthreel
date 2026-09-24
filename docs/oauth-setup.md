# OAuth Setup — Google & Apple providers for Supabase Auth

Manual steps. Total time: ~30 min for Google, ~45 min for Apple (Apple requires an Apple Developer account with the App ID already registered).

Both providers are configured in **Supabase Dashboard → Authentication → Providers**. Neither can be automated via MCP/SQL/CLI — Supabase's provider toggle is a dashboard-only surface.

Email/password already works. Test the mobile auth flow with that first; OAuth is a nice-to-have for launch.

---

## Google

### 1. Get the redirect URL from Supabase first

Dashboard → Authentication → Providers → Google → copy the **Callback URL** (looks like `https://fcefeiwvnzdfazwxdejy.supabase.co/auth/v1/callback`). You'll paste this into Google Cloud Console below.

### 2. Create OAuth client in Google Cloud

1. https://console.cloud.google.com → create (or select) a project — suggest `spillthereel-prod`.
2. **APIs & Services → OAuth consent screen** → configure:
   - User Type: **External**
   - App name: `SpillTheReel`
   - User support email: yours
   - App logo: upload `assets/logo/reel-drip-primary.svg` exported as 120×120 PNG
   - Application home page: `https://spillthereel.app` (or whatever domain you buy)
   - Authorized domain: `supabase.co`
   - Scopes: `email`, `profile`, `openid`
   - Test users: add your own email while it's in Testing mode; publish before launch
3. **APIs & Services → Credentials → Create Credentials → OAuth Client ID**:
   - Application type: **Web application**
   - Name: `SpillTheReel Supabase`
   - Authorized redirect URIs: paste the Callback URL from step 1
   - Copy the **Client ID** and **Client Secret**

### 3. Paste into Supabase

Dashboard → Authentication → Providers → Google:
- Toggle **Enable Sign in with Google** on
- Paste **Client ID** and **Client Secret**
- Save

### 4. Add to mobile OAuth flow

The mobile app (once wired — see `apps/mobile/src/lib/supabase.ts` after this session's commit) calls `supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: 'spillthereel://auth-callback' }})`. The deep-link scheme `spillthereel://` is already registered in `app.json`.

---

## Apple

Apple is stricter — you need three separate pieces of Apple config, then a signed JWT that Supabase can use.

### 1. Prerequisites in Apple Developer portal (developer.apple.com)

- Active **Apple Developer Program** membership ($99/year).
- **App ID** registered for `com.spillthereel.app` (Identifiers → + → App IDs → App).
  - Enable **Sign In with Apple** capability on the App ID.

### 2. Create a Services ID (this is what Supabase uses as the client ID)

- Identifiers → + → Services IDs → Continue.
- Description: `SpillTheReel Web Auth`
- Identifier: `com.spillthereel.app.auth` — this string becomes your `SUPABASE_APPLE_CLIENT_ID`.
- Enable **Sign In with Apple** on this Services ID → Configure:
  - Primary App ID: `com.spillthereel.app`
  - Domains: `fcefeiwvnzdfazwxdejy.supabase.co`
  - Return URLs: `https://fcefeiwvnzdfazwxdejy.supabase.co/auth/v1/callback`
- Save.

### 3. Create a Sign In with Apple key

- Keys → + → new key.
- Key Name: `SpillTheReel Auth`
- Enable **Sign In with Apple** → Configure → Primary App ID: `com.spillthereel.app`.
- Register → **download the .p8 file immediately** (you cannot re-download it).
- Note the **Key ID** (10 chars, e.g. `ABC1234567`) and your **Team ID** (top-right of the portal, 10 chars).

### 4. Generate the client secret JWT

Apple's OAuth "client secret" isn't a static string — it's a JWT you sign with the p8 key. Supabase now generates this for you if you paste all four pieces (Team ID + Key ID + Services ID + p8 contents) into their dashboard.

Dashboard → Authentication → Providers → Apple:
- Toggle **Enable Sign in with Apple** on
- **Services ID (Client ID)**: `com.spillthereel.app.auth`
- **Team ID**: your 10-char team ID
- **Key ID**: the 10-char key ID from step 3
- **Secret Key (for OAuth)**: paste the entire contents of the .p8 file (including `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines)
- Save.

### 5. iOS-native Sign In with Apple (separate from web OAuth)

For the native iOS "Sign in with Apple" button (mandatory for App Store review if you offer any other 3rd-party sign-in like Google), we use `expo-apple-authentication` on device, then pass the returned identity token to `supabase.auth.signInWithIdToken({ provider: 'apple', token })`. This is a different code path from the web OAuth flow above — both need to work.

**Implemented in code** (`src/lib/oauth.ts` → `performNativeAppleSignIn`, called automatically when provider is `apple` on iOS):
- Availability check via `AppleAuthentication.isAvailableAsync()` — falls back to the web-OAuth flow where native Apple auth doesn't exist (simulators without Apple ID, etc.).
- Fresh SHA-256-hashed nonce per attempt (`expo-crypto`): hashed nonce goes to `signInAsync()`, raw nonce + `identityToken` go to `signInWithIdToken()`.
- User cancel (`ERR_REQUEST_CANCELED`) maps to the silent `OAuthCancelled` path, same as the browser flows.
- Android and web keep the existing web-OAuth Apple flow — native Apple sign-in isn't available on those platforms.

No extra dashboard step beyond §1–4 above; the same Apple provider config covers both paths.

---

## Redirect URL register (all deep links)

Supabase Dashboard → Authentication → URL Configuration → Redirect URLs must whitelist every URL the app hands to Supabase as `redirectTo`, or Supabase falls back to the Site URL and the in-app flow silently dies (user picks an account, lands back with no session):

| URL | Used by | Code ref |
|---|---|---|
| `spillthereel://auth/callback` | Native OAuth return (standalone builds) | `buildOAuthRedirectUrl()` in `src/lib/oauth.ts` |
| `spillthereel://auth/reset-password` | Native password-recovery return | `buildPasswordResetRedirectUrl()` in `src/lib/oauth.ts` |
| `exp://<lan-ip>:8081/--/auth/callback` | OAuth return under Expo Go (IP changes per network — copy from the `[OAuth] Native redirectUrl:` Metro log) | same |
| `exp://<lan-ip>:8081/--/auth/reset-password` | Recovery return under Expo Go | same |
| `http://localhost:8081` | Web OAuth (`redirectTo = window.location.origin`) | `performWebOAuthSignIn` |
| `http://localhost:8081/auth/reset-password` | Web recovery return | `buildPasswordResetRedirectUrl` |

---

## Verification checklist (once configured)

- [ ] Google: sign in via mobile → check `SELECT provider, count(*) FROM auth.identities GROUP BY provider;` shows `google | 1`.
- [ ] Apple: same query shows `apple | 1`.
- [ ] Apple on a real iOS device uses the native sheet (no in-app browser opens).
- [ ] Backend `/v1/me` returns the correct user profile for both providers.
- [ ] Supabase Dashboard → Authentication → Users list shows the accounts with the correct provider badge.
- [ ] Forgot-password: request link → open from email → set new password → lands signed in. Expired/used link shows "request a fresh one".
