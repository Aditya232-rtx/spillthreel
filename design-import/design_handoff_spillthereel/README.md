# Handoff: Spillthereel Mobile App

## Overview
Spillthereel is a mobile app that imports a user's saved Instagram Reels (via Instagram's data-export ZIP) and makes them searchable/askable through an AI chat interface — "save it, then just ask for it back." This package covers the full first-run + core-loop flow: onboarding, auth, import, home, categories, AI chat, profile, and settings.

## About the Design Files
The bundled `SpillTheReel.dc.html` is a **design reference built in HTML** — a clickable prototype showing intended look, motion, and behavior. It is not production code to copy directly. The task is to **recreate these screens natively in the target codebase's environment** (React Native, SwiftUI, Kotlin/Compose, or whatever the project already uses — if no mobile stack exists yet, choose the most appropriate one) using that environment's own component and animation primitives, matching this reference pixel-for-pixel on layout/type/color and 1:1 on motion timing/easing.

Open `SpillTheReel.dc.html` directly in a browser to click through the flow. It renders inside an iOS device frame (`ios-frame.jsx`, included for reference only — do not port this file, it's a browser-only mock of iOS chrome).

## Fidelity
**High-fidelity.** Every screen has final colors, typography, spacing, and copy. Recreate pixel-perfectly using the design tokens below.

## Visual Style
Aesthetic direction: **kitsch-minimal** — a clean, minimal UX skeleton carrying a bold, playful, slightly campy surface treatment (thick 2–3px borders, offset hard drop-shadows with no blur, rotated sticker-style badges, saturated flat color blocks). Not soft/corporate; not neo-brutalist noise — controlled and legible.

## Screens / Views
All screens are 390×844 (iPhone-standard) frames.

### 1. Welcome (`isWelcome`)
- **Purpose:** First-run splash; brand intro; routes to Log in / Sign up.
- **Background:** `#F3EFE2` (off-white/cream — the app's primary background).
- **Badge:** "AI-POWERED ✦" pill, top-right, rotated 8°, `#E8AC3D` fill, 2.5px `#15170F` border, hard shadow `3px 3px 0 #15170F`.
- **Logo animation (the centerpiece — see Interactions & Behavior for full timing):** a single black dot pops in center → bursts into a ring of 6 dots (Claude-style loader arrangement) → holds → collapses back to a single dot → crossfades into the real logo mark image (`logo-mark-transparent.png`) → the whole mark shrinks/settles → wordmark + headline + subhead + CTAs fade up beneath it.
- **Wordmark:** "Spillthereel" in the custom script font `MyLove`, 19px, `#15170F`.
- **Headline:** "STOP RE-SCROLLING. START ASKING." — Oswald 700, 46px/44px line-height, uppercase, letter-spacing -0.03em; "START ASKING." in `#E85C3F` (coral).
- **Subhead:** "Save any reel. Get it back by just asking — recipes, workouts, that one travel tip." Oswald 600, 14px/20px, `rgba(21,23,15,0.78)`, max-width 250px.
- **CTAs:** two full-width pill buttons, 54px min-height, 2.5px `#15170F` border:
  - "Log in" — `#15170F` fill, white text, shadow `4px 4px 0 #E8AC3D` → goes to Login.
  - "Sign up" — `#E85C3F` fill, white text, shadow `4px 4px 0 #15170F` → goes to Signup.
  - Active/press state: `translate(3px,3px)` + shadow shrinks to `1px 1px 0` (button "presses into" its own shadow).
- **Footer:** "BY CONTINUING YOU AGREE TO OUR TERMS AND PRIVACY POLICY." Oswald 9.5px, letter-spacing 0.1em, `rgba(21,23,15,0.5)`, pinned bottom.

### 2. Login (`isLogin`)
- **Background:** `#F3EFE2`, three small decorative accent dots (coral/violet/gold, low opacity) scattered around the frame — purely ambient, no function.
- **Badge:** "HEY AGAIN ✦" pill, rotated -4°, `#E85C3F` fill, white text.
- **Logo:** small static version of the same dot-mark (`logo-mark-transparent.png`, ~56px).
- **Headline:** "WELCOME BACK." Oswald 700, 32px, uppercase.
- **Subhead:** "Sign in to your library." Oswald 500, 14px, `rgba(21,23,15,0.65)`.
- **Auth buttons:** "Continue with Google" / "Continue with Apple" — outline style, 1.5px `#15170F` border, `#F3EFE2` fill (minimal, no shadow), 48px min-height.
- **Divider:** "OR" between two 1.5px hairlines at 20% opacity.
- **Inputs:** email + password, pill shape, 1.5px `#15170F` border, white fill, 48px min-height, no shadow (minimal treatment vs. the louder buttons).
- **"Forgot password?"** right-aligned link, underlined.
- **Primary CTA:** "Log in" — `#E85C3F` fill, white text, 2px border, shadow `3px 3px 0 #15170F`, 50px min-height.
- **Footer link:** "NEW HERE? CREATE AN ACCOUNT." → Signup.

### 3. Signup (`isSignup`)
Same visual system as Login, mirrored:
- Badge: "20 SEC ✦ FREE", rotated +4°, `#E8AC3D` fill.
- Headline: "Find stuff easily" (Oswald 700, 28px).
- Subhead: "Takes 20 seconds. No credit card."
- Fields: Name, Email, Password (8+ characters), same pill/outline treatment as Login.
- Primary CTA: "Create account" (same coral/shadow treatment as Login's CTA).
- Footer link: "ALREADY HAVE AN ACCOUNT? LOG IN." → Login.
- On submit → Import flow (screen 4).

### 4. Import 1 — Intro (`isImport1`)
- **Background:** `#F3EFE2`.
- **Progress dots:** 3 pill segments top-left, first filled coral, rest outlined — step indicator (1 of 3).
- **Illustration:** `import1-illustration.svg`, 190×190, centered.
- **Headline:** "BRING IN THE REELS YOU ALREADY SAVED." Oswald 700, 30px/32px, uppercase.
- **Body copy:** explains the Instagram-export mechanism (see file for exact copy).
- **Primary CTA:** "Show me how" — coral fill, outline border, → Import 2.
- **Secondary:** "Maybe later" — text link, underlined → Home.
- **Footnote:** "YOU CAN ALWAYS IMPORT FROM SETTINGS LATER."
- Content (illustration+headline+body) is vertically centered in the remaining space below the progress dots.

### 5. Import 2 — Steps (`isImport2`)
- Progress dots: steps 1–2 filled coral, step 3 outlined, **left-aligned** (matches Import 1/3 alignment).
- Headline: "GRAB YOUR DATA FROM INSTAGRAM."
- 4 numbered instruction cards (`exportSteps`), each: big Oswald numeral, bold title, description, in a bordered card (`2px #15170F` border, `18px` radius).
- CTA: "I've got my zip — next" → Import 3. Secondary: "I'll do this later" → Home.

### 6. Import 3 — Upload (`isImport3`)
- Progress dots: all 3 filled coral (step 3 of 3).
- Headline: "DROP THE ZIP HERE."
- Dropzone: dashed 2px border, 32px radius, upload icon, drag/tap-to-pick target, centered in remaining vertical space along with the disclaimer line below it.
- Disclaimer: "WE READ YOUR FILE ON OUR SERVERS, THEN DELETE IT. YOUR ORIGINAL MEDIA NEVER LEAVES INSTAGRAM."
- CTA button label/state changes through picking → importing → done (see `dropzoneLabel`/`dropzoneSub`/`importLabel`/`importBtnBg` bindings in the file for the exact state copy and colors). On completion → Home.
- Secondary: "Skip forever" → Home.

### 7. Home (`isHome`)
- Background `#F3EFE2`. Main library/dashboard screen — reels feed, source pills, collections. Reference the file directly for full layout (search bar → routes to Chat; source toggle pills; collection rail; recent thumbnails grid).
- **Bottom tab bar:** floating, glassmorphic (semi-transparent blur pill), persistent across Home / Categories / Chat / Profile / Settings — see Interactions section for the glass treatment spec.

### 8. Categories (`isCategories`)
- Background `#F3EFE2` (matches Home).
- Header: "CATEGORIES" (Oswald 700, 40px) + "112 REELS · 8 CATEGORIES · SCROLL THE DIAL" meta line.
- Source-filter toggle: two overlapping rotated pills (stacked-card look), tap to flip which is "front."
- **Categories dial (signature interaction):** a continuously-scrollable, infinitely-looping vertical carousel of category cards arranged on a virtual circle — see Interactions for full math/behavior. Each card: colored background (`bg` per category), title, count, and a category icon (PNG, e.g. `icon-recipes.png`) — no border on the icon.
- Categories: Recipes (`#E8AC3D`), Workouts (`#E85C3F`), Travel (`#6A67E0`), Reading (`#15170F`/cream text), Fashion (`#C7D3C3`, the mint/sage accent — intentionally not reused on other tiles).
- Tapping a card expands it in place to reveal tag chips (e.g. "UNDER 15 MIN", "VEGETARIAN").

### 9. Chat (`isChat`)
- **Background:** `#0F110C` (near-black — the one screen that inverts to a dark theme).
- **No bottom tab bar on this screen** — replaced by a back button (top-left, circular, translucent white stroke) that returns to Home, and the header shows the wordmark "Spillthereel" in `MyLove` font, centered, `#F3EFE2`.
- **Message list:** user bubbles right-aligned; bot replies left-aligned, can include a rich "card" attachment (image + creator handle + description) — bordered dark card, `#1B1D15` fill, 210px wide.
- **Input bar:** fixed bottom, dark pill, rounded, placeholder text at `rgba(243,239,226,0.55)` (must be explicitly scoped — do not let it inherit the light-theme placeholder color used elsewhere in the app), send button coral circle.
- Suggestion chips available above the input for quick prompts.

### 10. Profile (`isProfile`)
Standard account/library summary screen — avatar, stats, saved collections. Reference file for full layout; visual system matches Home (cream background, same card/border/shadow language).

### 11. Settings (`isSettings`)
- Background `#C7D3C3` (the sage/mint tone — the one screen with a distinct background from the rest of the app, used intentionally to separate "system" screens from "content" screens).
- Grouped settings list (`settingsGroups`) with toggles, connected-sources management, delete-account confirmation flow, toast notifications.

## Interactions & Behavior

### Welcome logo animation (full timing spec)
All timings below are from screen mount (t=0). Colors: dots `#15170F` (ink/black), no connecting lines during the dot phase (matches reference motion: pure dot choreography, not a connected line-drawing).
1. **0.00–0.30s:** single dot (r=17, centered at the mark's center) pops in — spring scale `0 → 1.22 → 1`, easing `cubic-bezier(0.34,1.56,0.64,1)`.
2. **0.30–0.32s:** that center dot fades out (quick linear fade).
3. **0.30–0.70s:** simultaneously, 6 dots burst outward from the center point to a hexagonal ring (radius 42 from center, 60° apart — positions: top, upper-right, lower-right, bottom, lower-left, upper-left). Each dot animates `translate` from its own position back to center (scale 0, opacity 0) up to its resting position (scale 1, opacity 1) — i.e. they appear to fly outward from the collapsed center point. Same spring easing as step 1, slight overshoot at 55% (scale 1.2).
4. **0.70–1.35s:** ring holds static (loading-spinner look).
5. **1.35–1.70s:** the 6 ring dots reverse the burst — translate back to center, scale to 0, fade out (ease-in, no overshoot).
6. **1.55–1.95s:** overlapping with the collapse, the real logo image (`logo-mark-transparent.png`) fades and scales in at the same center point (`opacity 0→1`, `scale 0.3→1`, ease-out) — reads as "the dots become the logo."
7. **2.00–2.40s:** the whole logo group (now just the image, dots invisible) scales down from 1 → 0.55 (ease-in-out), settling into a smaller "header" size and holding.
8. **2.30–2.85s:** wordmark + headline + subhead + both CTA buttons — grouped as one block — fade up together (`opacity 0→1`, `translateY 14px→0`, ease-out).
- **Implementation note:** build every stage as fixed-duration/fixed-delay keyframe animations on elements whose props never change after mount (no dependency on component re-renders) — this sequence must play once, deterministically, regardless of unrelated state updates elsewhere in the app. Use `animation-fill-mode: both/forwards` throughout so an interrupted or backgrounded tab always resolves to a sane held frame, never a half-drawn/broken-looking intermediate state.
- Button press state on the two CTAs: `transform: translate(3px,3px)` + shadow offset shrinks from `4px 4px 0` to `1px 1px 0` — a "pressing into its own shadow" micro-interaction, present on all shadowed buttons app-wide, not just Welcome.

### Floating glass tab bar (Home / Categories / Chat-excluded / Profile / Settings)
- Pill-shaped, floating above the bottom edge (not edge-to-edge).
- Glassmorphism: semi-transparent background + backdrop blur (`backdrop-filter: blur(...)`) so content scrolls visibly behind it.
- On tab switch, the active-state indicator/icon should transition smoothly (not an instant swap) — treat the blur/opacity as "alive" during the switch, not just on a static resting frame.
- Not present on the Chat screen (replaced by back button + header, per screen 9 above).

### Categories dial
- A vertical, **infinitely-looping** carousel: cards are laid out along a virtual circle (not a flat list) so that as the user scrolls, each card's position is computed from its angular offset from the "center" (currently-focused) slot — not from linear scroll distance.
- At any time, ~3 cards are visible on screen — one centered/frontal, one above and one below, curving away.
- Looping: data repeats seamlessly (implementation duplicates the category list enough times, or uses modulo arithmetic on an infinite virtual index) so scrolling never hits a hard start/end — matches "scroll the dial" framing in the header copy.
- Cards nearer the center render larger/flatter (frontal); cards further away rotate/foreshorten and fade slightly — evoking a rotating dial/rolodex, not a flat scroll list.

## State Management
The prototype is a single component with a `screen` string driving which view renders (`welcome | login | signup | import1 | import2 | import3 | home | categories | chat | profile | settings`) — a straightforward navigation/router state in production.

Other state needed per screen:
- **Login/Signup:** email, password, name (controlled inputs), form submit → navigate.
- **Import 3:** upload state machine — idle → file picked → importing (brief simulated delay) → done → navigate to Home.
- **Categories:** which category is expanded (tag chips reveal), current dial scroll/rotation position, active source-filter toggle.
- **Chat:** message list (user/bot), input text, typing/loading indicator, scripted or real bot response.
- **Settings:** toggle states per setting, connected-sources list, delete-account confirmation modal, transient toast messages (auto-dismiss ~2.2s).

## Design Tokens

### Colors
| Token | Hex | Usage |
|---|---|---|
| Ink (primary text/border) | `#15170F` | body text, borders, dark fills |
| Cream (primary background) | `#F3EFE2` | default screen background |
| Warm cream (outer chrome) | `#EDEAE0` | outside the device frame only |
| Coral (primary accent) | `#E85C3F` | primary CTAs, highlights |
| Gold | `#E8AC3D` | secondary accent, badges |
| Violet | `#6A67E0` | tertiary accent |
| Sage/mint | `#C7D3C3` | Settings background; Fashion category tile |
| Chat dark background | `#0F110C` | Chat screen only |
| Chat card fill | `#1B1D15` | rich message cards in Chat |

### Typography
- **Headings/UI/body:** Oswald (variable weight 200–700). Uppercase for headlines/labels, tight letter-spacing (-0.01em to -0.03em) at large sizes.
- **Wordmark only:** "MyLove" custom script font (`assets/fonts/MyLove.otf`) — used exclusively for the literal "Spillthereel" wordmark, nowhere else.
- Minimum body size 12.5–14px; headline sizes range 26–52px across screens.

### Shape & Elevation
- Border weight: 2–3px solid `#15170F` on cards/buttons/inputs (bold, not hairline) — except where a screen calls for a deliberately "minimal" treatment (Login/Signup inputs use 1.5px, no shadow).
- Shadows are **hard offset shadows, no blur**: `Npx Npx 0 <color>` — typically 3–7px depending on element size. This is a core signature of the visual style; never swap in soft/blurred shadows.
- Border radius: pill (`999px`) for buttons/inputs/badges; 18–32px for cards/panels.
- Badges/stickers: small pills rotated ±4–8° for a playful, hand-placed feel.

### Spacing
- Screen padding: ~18–28px horizontal, ~54px top (below status bar), 24–40px bottom.
- Card/section gaps: 12–20px typical.

## Assets
All included in `assets/`:
- `logo-mark-transparent.png` — primary logo mark (transparent background), used on Welcome (animated), Login, Signup.
- `welcome-illustration.png` — legacy/secondary illustration asset (check current file for active usage before porting).
- `import1-illustration.svg` — Import 1 screen illustration.
- `icon-recipes.png`, `icon-workouts.png`, `icon-travel.png`, `icon-reading.png`, `icon-fashion.png` — category tile icons (no border, flat PNG).
- `chat-ui-libraries.png` — image used inside a chat message card (example content, replace with real content model).
- `fonts/` — Oswald and MyLove font files.

Icons throughout the rest of the UI (nav, back button, send button, etc.) are simple inline vector shapes in the source file — recreate as your platform's icon system (SF Symbols, Material Icons, or custom vector assets) rather than porting raw SVG paths.

## Files
- `SpillTheReel.dc.html` — the full interactive prototype, source of truth for all layout/copy/color/spacing. Open in a browser to click through every screen.
- `ios-frame.jsx` — browser-only iOS chrome mock used purely for presentation in the prototype; not part of the actual product surface, do not port.
