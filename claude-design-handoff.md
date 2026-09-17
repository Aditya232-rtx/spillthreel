# SpillTheReel — Claude Design Handoff

Copy-paste ready prompts for [claude.ai](https://claude.ai) design mode.

**How to use this file:**
1. **First message:** paste Section 1 (Master Brand Prompt). This teaches Claude the brand system.
2. **Then, one message at a time:** paste any of Section 2's per-screen prompts. Each is self-contained; you can generate screens in any order.
3. If Claude drifts off brand, re-paste Section 1 as a reminder.

Attach these files to your first message if the interface allows:
- [design-system.md](design-system.md)
- [assets/logo/reel-drip-primary.svg](assets/logo/reel-drip-primary.svg)
- [assets/logo/reel-drip-lockup-horizontal.svg](assets/logo/reel-drip-lockup-horizontal.svg)

---

## 1. Master Brand Prompt (paste first)

```
I'm designing a mobile app called SpillTheReel — a "second brain" that turns
your saved short-form videos from Instagram, TikTok, YouTube Shorts, and X
into a searchable library. Users share a reel URL, the app processes it with
AI, and users can later find any saved reel by describing it in natural language.

I want you to generate mobile screen designs (iPhone 15 Pro, 390×844pt) as
inline SVG or as a React component I can copy. Follow the brand system below
STRICTLY. If you're unsure, err toward playful, bold, poster-like — never
toward minimalist SaaS.

━━━ BRAND VOICE ━━━
Tagline: "Loud memory, calm interface."
Personality: playful not childish. Confident not clinical. Retro-modern.
Warm not corporate. Direct. Zero exclamation points. Second person.

━━━ COLOR PALETTE (exact hex, use as tokens) ━━━
--sage:       #C7D3C3   (primary background — the canvas)
--sage-deep:  #AEBFA9   (subtle sage shadow)
--cream:      #F3EFE2   (secondary bg, cards on sage, input surfaces)
--ink:        #15170F   (ALL text, ALL outlines, dark-mode bg)
--coral:      #E85C3F   (primary accent — save/live/action)
--mustard:    #E8AC3D   (secondary accent — categories/streak)
--peri:       #6A67E0   (tertiary accent — AI/recall/memory)
--white:      #FFFFFF   (text on coral/peri only)

Rules:
- Coral and peri NEVER use ink text — always white.
- One "loud" element per screen. Not two.
- Category colors rotate mustard → coral → peri → cream.
- No gradients (except sage → sage-deep radial on onboarding hero).
- No drop shadows. Ever.

━━━ TYPOGRAPHY ━━━
Display: Archivo Black — ALL CAPS ALWAYS. Letter-spacing -0.01em.
Body:    Inter 500 — sentence case. No italics.
Data:    IBM Plex Mono 500 — letter-spacing 0.10em. For counts, timestamps, meta.

Scale (mobile):
- display-xl: 40 / 40  (onboarding hero)
- display-lg: 30 / 32  (screen titles)
- display-md: 22 / 24  (card titles, hero stats)
- display-sm: 15 / 18  (section headings, button labels)
- body-lg:    16 / 24
- body-md:    14 / 20  (default)
- body-sm:    12.5 / 18
- mono-md:    12 / 16  (data readouts)
- mono-sm:    10.5 / 14  (tags, eyebrows)

━━━ THE SIGNATURE OUTLINE ━━━
Almost every card, button, chip, avatar, and input has a **2pt solid ink outline**.
Never 1pt. Never 3pt. This is the app's most important visual signature.

━━━ RADII ━━━
sm: 8    (tag/filter chips)
md: 14   (setting rows, inputs)
lg: 18   (stat cards)
xl: 22   (category cards, big content cards)
2xl: 32  (modal sheets)
full: 999 (pill buttons, avatars, toggles)

━━━ LOGO — "THE REEL DRIP" ━━━
A play triangle melting into a droplet with a small satellite drop bottom-right.
SVG path (viewBox 260 × 280):

<svg viewBox="0 0 260 280">
  <path d="M 60 50 L 190 112 L 155 129 C 172 155, 172 198, 142 220 C 122 234,
           100 223, 94 200 C 90 175, 102 158, 110 151 L 60 175 Z"
        fill="#E85C3F" stroke="#15170F" stroke-width="9"
        stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="212" cy="232" r="17" fill="#E85C3F"
          stroke="#15170F" stroke-width="9"/>
</svg>

Wordmark: SPILLTHEREEL in Archivo Black, all caps, letter-spacing -0.02em, ink color.

━━━ SIX SCREEN ARCHETYPES ━━━
Every screen fits one:
1. HERO (onboarding, empty states) — sage bg, illustration + display-xl + 1 CTA
2. DASHBOARD (home) — cream bg, colored stat blocks + rails
3. GRID (categories, library) — sage bg, colored cards mustard→coral→peri→cream
4. RECALL (chat, search) — ink bg (dark), cream + coral bubbles
5. DETAIL (item, import progress) — cream bg, editorial layout
6. UTILITY (settings, profile) — cream or sage, grouped ink-outlined rows

━━━ COMPONENTS TO REUSE ━━━
Pill button: radius-full, 2pt ink outline, Archivo Black display-sm caps label.
Primary variant = ink fill + cream text. Coral / mustard / peri variants use white text.

Card: radius-xl (22pt), 2pt ink outline, 16pt inner padding.
Stat card: 2-up row, radius-lg, display-md number stacked on mono-sm label.
Category card: full-width, min-height 84pt, display-md name top-left,
mono-sm count under, 48pt category illustration top-right.

Search bar: cream fill, 2pt ink outline, radius-md, 🔍 icon left, mic icon right.

Filter chip: mono-sm label, radius-full, 5–7pt padding.
Inactive = transparent + 2pt ink outline. Active = ink fill + cream text.

Tab bar (5 tabs, in order): Home ⌂ | Categories ◎ | Chat ✦ | Library ☰ | Settings ⚙
Active state = ink fill square backing with cream icon.
Chat tab's active fill is peri instead of ink.

━━━ COPY VOICE ━━━
- Second person, present tense.
- Verbs first: "Ask your second brain anything" NOT "Enter query"
- Zero exclamation points. Ever.
- Empty states have personality: "Nothing here yet. Share a reel — we'll do the rest."
- Loading states are silent. Show progress or nothing.

━━━ ANTI-PATTERNS (avoid these) ━━━
❌ No pastel gradients — reads as wellness app.
❌ No emoji as functional icons (small emoji in copy is fine).
❌ No black text on coral or peri (contrast fail — always white).
❌ No skeuomorphic textures, no glossy highlights, no drop shadows.
❌ No borderless colored cards — every colored surface needs its 2pt ink outline.
❌ No settings screen with more than 2 colors (utility archetype = cream + ink).
❌ No screen with more than one heavy illustration.
❌ No stock illustrations (unDraw, Storyset, etc.) — everything drawn in-brand.
❌ Do NOT include "process on this device" or any "on-device model" setting
   — that was a mistake in a rough mockup. The app uses cloud (Gemini + Cognee).

━━━ DELIVERABLE FORMAT ━━━
For each screen I request:
- Full inline SVG or a single React component (Tailwind or plain style props both fine)
- iPhone 15 Pro frame (390 × 844pt visible content area)
- Include status bar (9:41, dots) and the 5-tab bottom nav (unless it's an
  onboarding/hero/modal screen where the tab bar hides)
- Every color as a CSS variable using the tokens above
- Use the palette exactly — no invented shades

Confirm you understand, then I'll send you screen requests one at a time.
```

---

## 2. Per-Screen Prompts

Send each of these as a **separate follow-up message** after the master prompt lands.

### 2.1 Onboarding 1 — Welcome / Sign in

```
SCREEN 01 — ONBOARDING WELCOME

Archetype: HERO (sage background with subtle sage-deep radial gradient top-left
and top-right)
Tab bar: hidden
Content stack (center-aligned vertically):
1. The Reel Drip logo, ~140pt tall, centered
2. display-xl headline in ink: "Stop re-scrolling.\nStart asking."
   (two lines, break at the period)
3. body-md sub in ink at 78% opacity, max-width 240pt:
   "Save any reel. Get it back by just asking — recipes, workouts, that one
   travel tip."
4. space-6 gap
5. Pill button primary (ink fill, cream text): "CONTINUE WITH GOOGLE" with the
   Google G mark to the left of the text
6. Pill button primary (ink fill, cream text): "CONTINUE WITH APPLE" with the
   Apple mark to the left
7. Ghost button (no fill, no outline, ink text with underline, body-sm):
   "Continue with email"

Micro-copy at very bottom (mono-xs, ink 50% opacity, center-aligned):
"By continuing you agree to our Terms and Privacy Policy."

Output: inline SVG of the full iPhone frame.
```

### 2.2 Onboarding 2 — Import Explainer 1 (Why)

```
SCREEN 02 — ONBOARDING · IMPORT EXPLAINER (Step 1 of 3: WHY)

Archetype: HERO (cream background)
Tab bar: hidden
Top: step indicator — three small ink pills in a row, first one filled coral,
     others outlined only. Center-aligned, top 32pt.
Content stack:
1. A playful illustration slot ~180pt tall showing a phone with stacked reel
   thumbnails "falling out" of it in a jumble, in the illustrated cartoon-poster
   style (heavy 5pt ink outlines, single-color fills from the palette — no
   gradients). Do this as inline SVG.
2. display-lg headline in ink: "BRING IN THE\nREELS YOU\nALREADY SAVED."
3. body-md sub in ink at 78%:
   "Instagram lets you export your saved reels. Feed that file into
   SpillTheReel and every saved reel becomes searchable — with the caption,
   creator, and hashtags — in under a minute."
4. Space-6 gap.
5. Pill button coral (coral fill, white text): "SHOW ME HOW"
6. Ghost text button: "Maybe later"
7. Below the ghost button, mono-sm ink 55%:
   "You can always import from Settings later."

Output: inline SVG.
```

### 2.3 Onboarding 3 — Import Explainer 2 (How to export from Instagram)

```
SCREEN 03 — ONBOARDING · IMPORT EXPLAINER (Step 2 of 3: HOW TO EXPORT)

Archetype: DETAIL (cream background)
Tab bar: hidden
Top: step indicator — three pills, first two filled coral, third outlined.
Screen title (display-lg, ink, left-aligned): "GRAB YOUR\nDATA FROM\nINSTAGRAM."

Below the title, a numbered list of 4 steps. Each step is a cream card with
2pt ink outline, radius-lg. Inside each card:
- Left: a large mono-md number in ink ("01" through "04"), Archivo Black
- Middle: display-sm ink heading + body-sm ink 75% description

Steps:
01 — OPEN INSTAGRAM
     Settings → Accounts Center → Your information and permissions
02 — REQUEST YOUR DATA
     Tap "Export your information" → "Create export" → choose your account
03 — PICK YOUR SAVED CONTENT
     Under "customize information", select ONLY "Saved" and "Collections",
     format HTML, all time
04 — WAIT FOR THE EMAIL
     Meta will email you a ZIP download link within a few hours

Below the list:
- Pill button ink: "I'VE GOT MY ZIP — NEXT"
- Ghost text button: "I'll do this later"

Output: inline SVG.
```

### 2.4 Onboarding 4 — Import Explainer 3 (Upload)

```
SCREEN 04 — ONBOARDING · IMPORT EXPLAINER (Step 3 of 3: UPLOAD)

Archetype: DETAIL (cream background)
Tab bar: hidden
Top: step indicator — all three pills filled coral (complete state).
Screen title (display-lg, ink): "DROP THE ZIP\nHERE."

Middle: a large upload target zone
- Full-width card, cream fill, 2pt DASHED ink outline (not solid),
  radius-2xl (32pt), min-height 220pt
- Center-stacked inside:
  - A tiny Reel Drip logo (~48pt) at top, ink monochrome variant
  - display-sm ink: "TAP TO PICK YOUR ZIP FILE"
  - body-sm ink 65%: "or drag it in from Files"

Below the drop zone:
- Small mono-sm ink 60% row with an "i" info icon:
  "We read your file on our servers, then delete it. Your original
  media never leaves Instagram."
- Pill button coral (disabled state greyed): "IMPORT NOW"
- Ghost text button: "Skip forever" (mono-sm)

Output: inline SVG.
```

### 2.5 Login / Sign in (returning user)

```
SCREEN 05 — LOG IN (RETURNING USER)

Archetype: HERO (sage background)
Tab bar: hidden
Content stack (center-aligned):
1. The Reel Drip logo, ~110pt tall
2. display-lg headline: "WELCOME BACK."
3. body-md sub: "Sign in to your library."
4. space-6 gap
5. Pill button ink (cream text): "CONTINUE WITH GOOGLE" (with G icon left)
6. Pill button ink (cream text): "CONTINUE WITH APPLE" (with Apple icon left)
7. Space-4
8. Email input row (cream fill, 2pt ink outline, radius-md, 48pt tall):
   placeholder "you@email.com"
9. Password input row (same style): placeholder "password"
10. Ghost text link right-aligned: "Forgot password?"
11. Pill button coral: "LOG IN"

Bottom: mono-sm ink 60% center: "New here? Create an account."
(with "Create an account" underlined)

Output: inline SVG.
```

### 2.6 Sign up

```
SCREEN 06 — SIGN UP

Archetype: HERO (sage background)
Tab bar: hidden
Content stack:
1. The Reel Drip logo, ~90pt tall (slightly smaller than login since form is longer)
2. display-lg headline: "MAKE YOUR\nSECOND BRAIN."
3. body-md sub: "Takes 20 seconds. No credit card."
4. Space-4
5. Pill button ink: "CONTINUE WITH GOOGLE"
6. Pill button ink: "CONTINUE WITH APPLE"
7. Small horizontal divider row: two thin ink lines with mono-sm ink 55% "OR"
   centered between them
8. Full name input (cream fill, 2pt ink outline, radius-md): placeholder "Your name"
9. Email input: placeholder "you@email.com"
10. Password input: placeholder "8+ characters"
11. Pill button coral: "CREATE ACCOUNT"

Bottom: mono-xs ink 55% center:
"By continuing you agree to our Terms and Privacy Policy."
Below: "Already have an account? Log in" (with "Log in" underlined)

Output: inline SVG.
```

### 2.7 Home / Dashboard

```
SCREEN 07 — HOME (DASHBOARD)

Archetype: DASHBOARD (cream background)
Tab bar: visible, "Home" tab active (ink fill square around ⌂ with cream icon)

Content top to bottom:
1. Greet row (top 16pt padding):
   Left: display-md ink "Hey, Aditya" (with a small 👋 emoji after — one of the
   only allowed emoji cases, in copy)
   Right: circular avatar 36pt, coral fill, 2pt ink outline

2. Search bar full-width: cream fill, 2pt ink outline, radius-md.
   Content: 🔍 icon left, placeholder body-md 65% "Ask your second brain
   anything…", mic icon right (small).

3. Stat row — 2-up flex row, gap 12pt:
   - Left card: coral fill, radius-lg, 2pt ink outline, 84pt tall.
     Inside: display-md WHITE "342", below mono-sm cream 90% "REELS SAVED"
   - Right card: mustard fill, radius-lg, 2pt ink outline, 84pt tall.
     Inside: display-md ink "6", below mono-sm ink 75% "SAVED THIS WEEK"

4. Mini chart card: peri fill, radius-xl (22pt), 2pt ink outline, height ~130pt.
   Inside:
   - Top row: display-sm WHITE "RECALL ACTIVITY", right-aligned mono-sm cream
     80% "this week"
   - Below: 7 vertical bars, gap 6pt, heights varying 20% to 90%, rounded top
     corners, white/cream fills at varying opacity

5. Section heading display-sm ink "RECENTLY SAVED" with a mono-sm ink 60%
   "See all" right-aligned

6. Horizontal scroll of 4 thumbnails (64×88pt each, 2pt ink outline, radius-md):
   fills alternate mustard, coral, peri, sage-deep. Small ▶ overlay bottom-left
   of each in white.

7. Section heading display-sm ink "YOUR COLLECTIONS" with mono-sm 60% "3" chip
   right-aligned

8. Vertical stack of 2 collection rail cards. Each card cream fill, 2pt ink
   outline, radius-xl:
   - Card 1: display-md ink "RECIPES" on left, mono-sm ink 65% "48 reels"
     under, three tiny thumbnails right
   - Card 2: display-md ink "TRAVEL PLACES", mono-sm 65% "22 reels", three
     thumbnails

Bottom tab bar: 5 tabs as specced in master prompt. Home active.

Output: inline SVG.
```

### 2.8 Categories

```
SCREEN 08 — CATEGORIES

Archetype: GRID (sage background)
Tab bar: visible, "Categories" tab active (◎ icon)

Content top to bottom:
1. Screen title row:
   Left: display-lg ink "CATEGORIES"
   Right: filter chip "ALL SOURCES ⌄" (transparent, 2pt ink outline, radius-full)

2. Small mono-sm ink 55% row: "112 REELS · 8 CATEGORIES"

3. Vertical stack of 5 category cards (full width, 2pt ink outline, radius-xl,
   min-height 100pt), rotating colors in this order:
   mustard → coral → peri → cream → mustard

   Each card:
   - display-md name top-left (ink on mustard/cream, white on coral/peri)
   - mono-sm count under name at 75% opacity: "N REELS · AUTO-SORTED"
   - Illustration slot ~48pt at top-right, drawn in the cartoon-poster style
     (5pt outline, single flat color fill contrasting the card)

   Cards to include:
   1. RECIPES (mustard) — pan-with-steam illustration
   2. WORKOUTS (coral, white text) — dumbbell illustration
   3. TRAVEL (peri, white text) — paper airplane illustration
   4. READING (cream) — open book illustration
   5. FASHION (mustard) — coat hanger illustration

   The first card (RECIPES) is in an "expanded" state — shows an extra row at
   bottom with 3 tag chips (ink 90% fill, cream text, radius-full, mono-sm):
   "UNDER 15 MIN", "VEGETARIAN", "HIGH PROTEIN"

Bottom tab bar: Categories active.

Output: inline SVG.
```

### 2.9 AI Chat / Recall

```
SCREEN 09 — AI CHAT (RECALL)

Archetype: RECALL (ink background — this screen is dark)
Tab bar: visible, "Chat" tab active with PERI fill square + cream ✦ icon
(the chat tab is the ONLY tab whose active fill is peri, not ink)

Content top to bottom:
1. Chat header row:
   Left: The Reel Drip logo in CHAT variant (peri fill on cream 9pt outline),
   ~32pt tall
   Center: display-sm CREAM "YOUR SECOND BRAIN"
   Right: a small "clear conversation" icon (trash outline, cream, 20pt)

2. Chat bubbles (stacked bottom-up, most recent at bottom):
   Bubble 1 (bot, cream fill, ink text, radius-lg with bottom-left 4pt sharp
   corner, max-width 82%): "Found it — the high-protein pasta reel you saved
   from @cookwithmax, 12 days ago. Want the ingredient list?"

   Bubble 2 (user, coral fill, white text, radius-lg with bottom-right 4pt,
   right-aligned): "yes show ingredients"

   Bubble 3 (bot, same as bubble 1): "Chickpea pasta, garlic, chili flakes,
   lemon, parmesan, olive oil. He subs Greek yogurt for cream at the 0:38 mark."

3. Below the last bot bubble, a chip row (horizontal scroll):
   - "▶ OPEN ORIGINAL" — chip, transparent, cream 35% outline 1.5pt, cream text
     body-sm 500
   - "📁 SAVE TO RECIPES"
   - "🔎 FIND SIMILAR"

4. Bottom input pill:
   Full-width, semi-transparent cream fill (10% opacity on ink), 1.5pt cream
   30% outline, radius-full, padding 12pt vertical.
   Left: mic icon (cream), Right: send arrow (cream 60%)
   Middle: placeholder cream 55% "Ask something…"

5. Tab bar with chat tab in peri active state

Output: inline SVG.
```

### 2.10 Settings

```
SCREEN 10 — SETTINGS

Archetype: UTILITY (sage background)
Tab bar: visible, "Settings" tab active (⚙ icon)

Content top to bottom:
1. Screen title row:
   Left: display-lg ink "SETTINGS"
   Right: small avatar 32pt showing the user's profile pic in a circle with
   2pt ink outline

2. Grouped setting sections. Each group has:
   - A mono-sm ink 55% eyebrow heading in caps
   - A stack of setting rows: cream fill, 2pt ink outline, radius-md, 14pt
     vertical padding.
   - Each row has left-side content (display-sm ink label + optional body-sm
     ink 60% sub) and a right-side control (toggle, chevron ›, value text, or
     small chip)

Groups in order:

GROUP 1 — LIBRARY
- Row: "Import from Instagram" · sub "Add older saves anytime" · chevron ›
- Row: "Quality" · value chip "FAST" (mustard fill, ink text, mono-sm)
- Row: "Auto-delete stale items" · toggle OFF

GROUP 2 — NOTIFICATIONS
- Row: "Save ready to search" · toggle ON
- Row: "Weekly digest" · toggle OFF
- Row: "Failed saves" · toggle ON

GROUP 3 — ACCOUNT
- Row: "Data export" · sub "Download everything as JSON" · chevron ›
- Row: "Sign out" · chevron ›

GROUP 4 — DANGER ZONE
- Row: "Delete account" — label in CORAL, chevron in coral

Bottom mono-xs ink 40% center: "SpillTheReel v1.0.0"

Bottom tab bar: Settings active.

Output: inline SVG.
```

### 2.11 Profile

```
SCREEN 11 — PROFILE

Archetype: UTILITY (cream background)
Tab bar: visible, "Library" tab active (☰ icon) — profile is accessed from a
Library submenu, so Library tab is the highlight

Content top to bottom:
1. Top row: back arrow "‹" left, display-sm ink centered "PROFILE",
   empty right slot for balance

2. Profile hero (center-aligned):
   - Avatar 88pt circle, peri fill, 2pt ink outline
   - space-3 below
   - display-md ink "ADITYA JADHAV"
   - mono-sm ink 60% "@aditya · joined jun 2026"

3. Stats row (3-up): a full-width band with 2pt ink top and bottom borders only
   (no side borders), 16pt vertical padding, three center-aligned columns
   separated by thin 1pt ink 20% vertical dividers:
   - "342" display-md ink stacked over "REELS" mono-sm ink 60%
   - "6"   display-md ink over "CATEGORIES"
   - "18"  display-md coral over "DAY STREAK"

4. Section eyebrow mono-sm ink 55% "CONNECTED SOURCES"

5. Stack of source rows, radius-md, 2pt ink outline, 14pt vertical padding.
   Two states — "connected" rows use mustard fill, "not connected" rows use
   cream fill at 70% opacity:
   - INSTAGRAM (mustard) · right side mono-sm ink 75% "CONNECTED · 210 REELS"
   - TIKTOK (mustard) · "CONNECTED · 98 REELS"
   - YOUTUBE (cream 70%) · "NOT CONNECTED"
   - X / TWITTER (cream 70%) · "NOT CONNECTED"

6. Small pill button ghost text center: "Import more from Instagram"

Bottom tab bar: Library active.

Output: inline SVG.
```

---

## 3. Optional Follow-ups

If Claude renders a screen and it's ~80% right, use these one-liner nudges:

- "Increase every outline to exactly 2pt solid ink — a few look 1pt right now."
- "The coral card has ink text — make it white per the rules."
- "The tab bar's active state should be an ink-filled rounded square (radius-md)
  behind the icon, not just a color change on the icon."
- "The category card illustration is too detailed — flatten it, single color
  fill, 5pt ink outline, remove all shading."
- "Space between elements is too tight — use 16pt gap between cards."
- "The wordmark should read SPILLTHEREEL, not SPILL — check kerning and
  container width."

---

## 4. Iteration Order (suggested)

Do them in this order so brand consistency compounds:

1. Master brand prompt (Section 1)
2. Home dashboard (07) — proves color rhythm + tab bar
3. Categories (08) — proves the grid archetype + card rotation
4. AI chat (09) — proves the dark recall archetype
5. Onboarding welcome (01)
6. Onboarding import 1 → 2 → 3 (02, 03, 04)
7. Login (05), Sign up (06)
8. Settings (10), Profile (11)

If Home + Categories + Chat land well, the rest inherit the language cleanly.
