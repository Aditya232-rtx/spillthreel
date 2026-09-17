# SpillTheReel — Design System v1

**Version:** 1.0
**Status:** Design direction locked — component specs ready for build
**Owner:** Aditya Jadhav
**Last updated:** 2026-09-10

Companion documents: [prd.md](./prd.md), [trd.md](./trd.md), [architecture.md](./architecture.md), [buildphase.md](./buildphase.md).

**Purpose of this file:** feed directly into a design agent (Claude design skill, Figma AI, or a human designer) to produce screen designs consistent with SpillTheReel's brand. Every section is written as *rules* not suggestions, so downstream tooling can apply them mechanically.

---

## 1. Brand Voice & Design Philosophy

### 1.1 One-line brand promise

**"Loud memory, calm interface."**

The product is aggressive at *remembering* — categories snap into place, recall answers in one shot, share-and-go feels instant. The interface is calm and confident where the user is *navigating*: no jitter, no anxious progress bars, no cognitive load.

### 1.2 Personality vector

We are:
- **Playful, not childish.** Bold color, illustrated mascots, hand-drawn energy — but never comic sans, never sticker-spam, never emoji-first.
- **Confident, not clinical.** No Silicon-Valley SaaS grey. No Material stock. The app doesn't apologize for having a personality.
- **Retro-modern.** Heavy black outlines. Cartoon-poster illustration language. Type set in Archivo Black caps like a 1970s Penguin paperback cover. Motion is subtle and rubbery, not techy.
- **Warm, not corporate.** Sage green + cream feel like a well-worn journal, not a dashboard.
- **Direct.** Copy is short, imperative, and second-person. "Ask your second brain anything." Not "Please enter your query."

We are not:
- Minimalist / anonymous SaaS
- Neomorphic / glassmorphic
- Neon / dark-mode-only
- Emoji-laden
- Fintech-formal

### 1.3 The moments the brand gets loud vs. calm

| Loud (bold color blocks, illustrations, display caps) | Calm (cream/sage, small type, quiet) |
|---|---|
| Onboarding | Settings |
| Home stat cards | Item detail sheet |
| Category screens | Search filters |
| Chat / recall answer | Import progress screen |
| Empty states | Legal / danger zone |
| Confirmation toasts ("SAVED ✓") | Notification permission dialogs |

Rule: **one loud element per screen carries the moment.** Two loud elements compete. Three is a jumble.

---

## 2. Color System

### 2.1 Palette

| Token | Hex | Role |
|---|---|---|
| `sage` | `#C7D3C3` | Primary background — the "canvas" |
| `sage-deep` | `#AEBFA9` | Sage shadow, subtle gradient shifts |
| `cream` | `#F3EFE2` | Secondary background, input surfaces, cards on sage |
| `ink` | `#15170F` | All text, all outlines, dark-mode background |
| `coral` | `#E85C3F` | Primary accent — save actions, live states, hot stats |
| `mustard` | `#E8AC3D` | Secondary accent — categories, streaks, warmth |
| `peri` | `#6A67E0` | Tertiary accent — AI moments (chat, recall, search) |
| `white` | `#FFFFFF` | Reserved for text on coral/peri, chart highlights |

Optional (v1.5+):
- `sage-mist` `#E1E8DE` — extra-light sage for split-card backgrounds
- `plum` `#5B2C6F` — reserved for future error states if coral gets overloaded

### 2.2 Color rules

1. **Sage is the default page background** for anything content-forward (categories, library browse).
2. **Cream is the default for "safe" pages** (Home dashboard, Profile, Settings). It's the paper.
3. **Ink is the default for AI / recall screens** (Chat, Search answer). Dark = "the brain is thinking."
4. **Each of `coral`, `mustard`, `peri` gets exactly one hero role per screen.** They are never used as background for text >2 lines unless paired with `cream` or `white` text.
5. **Category colors rotate deliberately in order:** `mustard → coral → peri → cream` and repeat. This gives the category grid its visual rhythm.
6. **Never more than one dark-on-dark card per screen.** Contrast pockets, not walls.
7. **No gradient fills** except the sage → sage-deep radial background gradient on marketing / onboarding surfaces. Everything else is flat.
8. **No color-only status.** State colors (success/error) always pair with an icon or label — accessibility floor.

### 2.3 Accent-role assignments (fixed)

| Accent | Semantic |
|---|---|
| Coral | "New" / "Live" / "Save action" / "Now" — anything that is happening or just happened |
| Mustard | "Warmth" / "Warning-lite" / "Streak" / "Recipe/Food category default" |
| Peri | "AI" / "Recall" / "Memory" / "Understanding" |
| Ink | "Author" / "Structural" — never accent, only ground |
| Cream | "Rest" / "Paper" / "Text canvas" |

### 2.4 Contrast requirements

All body text meets **WCAG AA 4.5:1** minimum. Display caps text ≥ 24pt allowed at 3:1 (WCAG AA large). The following pairs are validated:

- Ink on sage ✓
- Ink on cream ✓
- Ink on mustard ✓
- White on coral ✓
- White on peri ✓
- Cream on ink ✓
- **Ink on coral is NOT allowed** (too close). Use white on coral.
- **Ink on peri is NOT allowed.** Use white on peri.

---

## 3. Typography

### 3.1 Type stack

| Role | Family | Weights | Fallback |
|---|---|---|---|
| Display | **Archivo Black** | 900 only | Arial Black, Helvetica Neue |
| Body | **Inter** | 400, 500, 600, 700, 800 | -apple-system, Segoe UI, Roboto |
| Data / Mono | **IBM Plex Mono** | 400, 500 | SF Mono, Consolas |

Fonts loaded via `expo-font` at app boot. Fallbacks must render acceptably during the ~200ms font-load window.

### 3.2 Type scale (mobile, 375pt reference width)

| Token | Size / LH | Weight | Usage |
|---|---|---|---|
| `display-xl` | 40 / 40 | Archivo Black | Onboarding hero, empty-state headlines |
| `display-lg` | 30 / 32 | Archivo Black | Screen titles ("CATEGORIES", "SETTINGS") |
| `display-md` | 22 / 24 | Archivo Black | Card titles (category name, stat card number) |
| `display-sm` | 15 / 18 | Archivo Black | Section headings inside cards |
| `body-lg` | 16 / 24 | Inter 500 | Paragraph copy in modals, chat bubbles |
| `body-md` | 14 / 20 | Inter 500 | Default UI copy, list items |
| `body-sm` | 12.5 / 18 | Inter 500 | Meta info, secondary labels |
| `mono-md` | 12 / 16 | Plex Mono 500 | Data readouts, counts (48 REELS · AUTO-SORTED) |
| `mono-sm` | 10.5 / 14 | Plex Mono 500 | Tag chips, timestamps, section eyebrows |
| `mono-xs` | 9.5 / 12 | Plex Mono 500 | Legal-adjacent micro-copy |

### 3.3 Typography rules

1. **All display text is UPPERCASE.** No mixed case. No exceptions.
2. **Letter-spacing:** display = `-0.01em` (tight), mono = `0.08–0.14em` (open).
3. **Body copy is always sentence case.** Never title-case a UI label unless it's a proper noun or platform name.
4. **Numbers are always Archivo Black** when they're a hero stat (342 REELS SAVED). They are always IBM Plex Mono when they're incidental data (48 reels · auto-sorted).
5. **Never justify.** Left-align only.
6. **Line length:** body copy caps at 55–65 characters. On mobile, breakpoints handle this naturally at 375pt.
7. **No italics** in the app UI. Reserved for editorial writing only (blog, PR).
8. **Punctuation in display type:** allowed; keep it minimal. Line break with `<br>` over comma for headline rhythm.

### 3.4 Copy voice — writing rules

- Second person, present tense: "You saved 342 reels this week."
- Verbs first: "Ask your second brain anything." NOT "Enter your query."
- No em-dashes in UI microcopy (they read as design filler on small screens). Use commas or periods.
- Zero exclamation points anywhere in the app. The energy comes from color and type, not punctuation.
- Numbers in copy: use digits from 10 up, spell "one" through "nine" as words. Exception: hero stat cards always use digits.
- No branded jargon in UI. "Reels" is safe (colloquial). "Second brain" is our brand name for the memory. "Enhance" is our internal verb (visible only on the enhance CTA).
- Empty states have personality: "Nothing here yet. Share a reel — we'll do the rest."
- Loading states are silent: no "Please wait" / "Fetching data". Show progress or nothing.

---

## 4. Layout & Spacing

### 4.1 Spacing scale (4pt base)

| Token | Value | Usage |
|---|---|---|
| `space-0` | 0 | — |
| `space-1` | 4 | Icon-to-label inline padding |
| `space-2` | 8 | Tight stacked elements |
| `space-3` | 12 | Default gap inside cards |
| `space-4` | 16 | Card padding, section gap |
| `space-5` | 20 | Wide card padding |
| `space-6` | 24 | Section-to-section vertical rhythm |
| `space-8` | 32 | Screen top padding above nav |
| `space-10` | 40 | Hero → next block on onboarding |
| `space-12` | 48 | Legal footer breathing room |

### 4.2 Screen padding

- Default horizontal safe area: **20pt left/right** on standard screens.
- Onboarding hero screens: **28pt** to feel more spacious.
- Full-bleed screens (category cards, chat): **18pt**.

### 4.3 Grid

- Single-column vertical stack on mobile. No true columnar layouts in v1.
- Home screen stat cards use a 2-up **flex row** with `gap: space-3`.
- Category card grid is 1-up (full width per card) — designed to feel poster-like, not gridded.
- Recently-saved thumbnail row is a horizontal `ScrollView` with `gap: space-2`.

### 4.4 Corner radii

| Token | Radius | Usage |
|---|---|---|
| `radius-sm` | 8pt | Tag chips, filter chips, small pills |
| `radius-md` | 14pt | Setting rows, input surfaces |
| `radius-lg` | 18pt | Stat cards |
| `radius-xl` | 22pt | Category cards, big content cards |
| `radius-2xl` | 32pt | Modal sheets, phone-frame outer |
| `radius-full` | 999 | Pill buttons, avatars, toggles |

### 4.5 Borders / outlines

- **The heavy black outline is the signature.** Nearly every card, button, chip, avatar, and input carries a **2pt solid `ink` outline**.
- Exceptions: text inside a bubble on a dark-mode screen (no outline needed), toggles (custom).
- Border width is **always 2pt**. Never 1pt (too thin for the poster feel). Never 3pt (too clunky).
- **Border color is always `ink`.** Never sage or cream. The outline is what makes the color blocks pop.

### 4.6 Elevation / shadows

- **We don't use drop shadows.** Elevation is expressed by color-block layering and the black outline.
- One exception: the phone-frame outer shadow in marketing/onboarding hero SVGs uses `0 24px 60px rgba(20, 22, 15, 0.28)`.

---

## 5. Iconography & Illustration

### 5.1 Icon language

Two icon styles coexist:

1. **UI icons** — small flat outlined marks (18pt, 2pt stroke, `ink` color) for tab bar, back buttons, filter chevrons, share arrows. These stay quiet.
2. **Signature illustrations** — larger cartoon-poster scenes with heavy 5–7pt black outlines and one flat color fill. These carry the brand voice. Reserved for onboarding, empty states, chat header mascot, category card hero, and the app icon itself.

Do not mix a signature illustration and a hero color-block card on the same screen. One personality moment per screen.

### 5.2 The signature mark — "the brain-reel"

The app mascot is a **stylized brain rendered as a blob with a film-strip band across the middle**. Referenced as `reel-brain-mark`.

- Base shape: irregular organic blob (not a symmetric anatomical brain).
- Fill: `mustard`, `peri`, or `cream` depending on surface.
- Outline: 7pt `ink` stroke.
- Film-strip band: horizontal `ink` bar across the middle, with 6 evenly-spaced `cream` circle "sprockets".
- Optional swirl line inside the blob suggests memory / thought.

Usage rules:
- Appears in exactly two places in v1: **onboarding hero** and **AI chat screen header**.
- Never appears in the tab bar (would over-brand).
- Never appears at < 28pt (loses legibility).

### 5.3 Category & content illustrations

Every canonical category has an illustration slot on its card:

| Category | Illustration motif |
|---|---|
| Recipes | Pan + steam + herbs |
| Workouts | Dumbbell |
| Travel | Paper airplane + compass |
| Reading / Education | Open book |
| Fashion / Style | Coat hanger |
| Tech | Retro monitor |
| Finance | Coin stack |
| DIY | Hammer + nail |
| Comedy | Speech balloon with "HA" |
| Product | Shopping bag |
| Music | Wavy line + note |
| Other | Question-mark tag |

All illustrations follow the same rules:
- 5–7pt black outline
- Single-color fill matching a palette accent
- Flat, no gradients, no shading
- No small serifs, no photorealism, no isometric perspective
- Illustration lives in a ~48pt slot in the top-right corner of category cards

Line-drawing style reference: bold vintage children's book illustration + 1970s poster art. Think Tomi Ungerer, not modern SaaS spot illustration.

### 5.4 Tab bar icons (5 tabs, in order)

| Tab | Icon | Notes |
|---|---|---|
| Home | House glyph `⌂` — flat outlined 20pt | |
| Categories | Circle-in-circle `◎` — |
| Chat / Recall | Four-point star `✦` — this tab uses the **peri** active-state fill because "recall = AI" |
| Library | Three-line stack `☰` | |
| Settings | Gear glyph `⚙` | |

Active state: filled `ink` square backing with the icon in `cream`. Inactive: icon-only `ink` at 60% opacity.

---

## 6. Components

Every component is spec'd here as tokens + one canonical variant + key states.

### 6.1 Button — Pill

**Base:** `radius-full`, `2pt ink` outline, Archivo Black `display-sm` label all-caps, 15pt vertical / 20pt horizontal padding.

Variants:

| Variant | Background | Text | Use |
|---|---|---|---|
| Primary | `ink` | `cream` | Main CTAs ("CONTINUE WITH GOOGLE") |
| Coral | `coral` | `white` | Save / new-action CTAs |
| Peri | `peri` | `white` | Recall / AI CTAs |
| Mustard | `mustard` | `ink` | Confirmation / streak CTAs |
| Ghost | transparent | `ink` | Secondary text-link buttons ("Continue with email") — no outline, underline instead |

States:
- **Default:** flat.
- **Pressed:** darkens fill by 8% (blend with `ink` at 8%).
- **Disabled:** fill drops to `sage-deep`, text drops to 40% ink.
- **Loading:** replace label with a 3-dot bouncing indicator in the same color as the label.

Always min-height 48pt for touch target.

### 6.2 Card

**Base:** `radius-xl` (22pt), `2pt ink` outline, `space-4` (16pt) inner padding.

Card variants: colored blocks — pick fill from the palette per §2.

- **Stat card** — 2-up row, `radius-lg` (18pt), display-md number on top, mono-sm label below, height 84pt.
- **Category card** — full width, min-height 84pt, 22pt radius, display-md name top-left, mono-sm count under, 48pt illustration slot top-right. Optional expanded state shows tag-chip row at bottom.
- **Item card** (search result) — cream background, 22pt radius, 64×88 thumbnail on left, display-sm title, body-sm summary, mono-xs meta row (platform · saved date · state badge).
- **Mini chart card** — 22pt radius, display-sm title top with mono-sm subtitle right-aligned, bars 56pt tall below.
- **Collection rail card** — same as category card but with a horizontal `ScrollView` of thumbnails inside instead of tag chips.

### 6.3 Input — Search bar

**Base:** cream fill, 2pt `ink` outline, `radius-md` (16pt), 13–14pt padding vertical / 16pt horizontal.

- Left: 🔍 icon (18pt) or `expo-image` custom icon in `ink`.
- Middle: placeholder in body-md at 65% opacity.
- Right (optional): mic icon (voice input trigger). Long-press activates.
- On focus: outline stays black, but a 2pt `coral` shadow inset appears at the bottom (a hint at "you're active").
- On error: outline flashes coral for 240ms.

### 6.4 Chip

**Base:** `radius-full`, mono-sm label, 5–7pt vertical padding.

| Chip type | Fill | Outline | Text |
|---|---|---|---|
| Filter chip (inactive) | transparent | 2pt ink | ink |
| Filter chip (active) | ink | none | cream |
| Tag chip (informational) | 90% ink | none | cream |
| Category clarity tag | cream | 2pt ink | ink |
| State badge — text_indexed | cream | 2pt ink | ink + small dot-icon |
| State badge — fully_indexed | mustard | 2pt ink | ink + small ✓ |
| State badge — queued/analyzing | peri | none | white + spinning dot |
| State badge — failed | coral | none | white + `!` |

### 6.5 Bubble — chat

- **Bot bubble:** cream fill, 18pt radius except bottom-left = 4pt (chat-tail), body-lg text at 13–14pt, max-width 82% of screen.
- **User bubble:** coral fill, white text, 18pt radius except bottom-right = 4pt, right-aligned, max-width 82%.
- **Suggestion chip row** below the last bot bubble: transparent chips with 1.5pt semi-cream outline, cream text at 60% opacity. Six tokens like "▶ Open original reel", "📁 Save to Recipes".

### 6.6 Toggle

**Base:** 38 × 22pt, `radius-full`, `ink` fill when ON, 16pt cream circle knob.

- OFF state: fill drops to `#CCC4B3` (sage-deep at low sat), knob shifts left to white.
- Animation: knob slides 240ms cubic-bezier(0.4, 0, 0.2, 1). Fill fades in 200ms.

### 6.7 Toast (Android "Saved" confirmation)

- 300 × 44pt floating pill at bottom-center, 32pt above the tab bar area.
- Fill: `ink` at 92% opacity, cream text (mono-md), 2pt `cream` outline (not ink — inverse rule for dark surfaces).
- Content: small ✓ icon + "SAVED TO SPILLTHEREEL"
- Duration: 1600ms, fades in 200ms, out 300ms.
- Only used by the Android share intent path.

### 6.8 iOS in-share-sheet mini popup

- Full-width, ~100pt tall, cream background, 2pt ink outline top only.
- Center-stacked: reel-brain-mark mini (28pt) + display-sm status text.
- Sequence: "SAVING…" (with animated dots) → "SAVED ✓" → auto-dismiss in 1.2s.
- Status text color: ink on cream. ✓ icon in coral for the success frame.

### 6.9 Avatar

- Circular, 2pt `ink` outline.
- Sizes: 28pt (chip), 36pt (header), 72pt (profile hero).
- Fill: user's identicon-color derived deterministically from `user_id` — pick from `coral / mustard / peri / sage-deep`. Never all-cream (invisible).
- If a Google/Apple sign-in photo exists, show it inside the ring.

### 6.10 Progress bar (import screen)

- 8pt tall pill, `radius-full`, cream background, 2pt `ink` outline.
- Fill: coral (or mustard when > 70% complete).
- Above the bar: display-sm "N / M PROCESSED", right-aligned.
- Below: mono-sm ETA and current status.

### 6.11 Empty state

Every empty state has three elements stacked, center-aligned, `space-6` between:

1. Reel-brain-mark or category illustration (~120pt tall).
2. Display-lg headline in 2 lines max.
3. Body-md sub-copy in 2 lines max.
4. One pill button.

Never show a raw `no results` string. Never show a generic sad-face icon.

### 6.12 Modal / bottom sheet

- Slides from bottom, `radius-2xl` top corners only.
- Fill: cream by default, ink for AI-context modals.
- Top has a 4×36pt drag handle in `ink` at 30% opacity.
- Content-scrolls internally.

---

## 7. Motion & Animation

### 7.1 Principles

- **Rubbery, not techy.** Spring physics (stiffness ~180, damping ~22) rather than fixed easing curves for card transitions.
- **Motion serves confirmation.** Every state change gets one short motion cue (< 320ms). Nothing decorative.
- **Nothing floats or drifts.** No parallax. No ambient background motion. Backgrounds are static.
- **60fps floor.** If a motion drops below 60fps on a mid-tier Android, cut it.

### 7.2 Motion tokens

| Token | Duration | Curve | Usage |
|---|---|---|---|
| `motion-tap` | 120ms | ease-out | Button press feedback |
| `motion-swap` | 220ms | cubic-bezier(0.4, 0, 0.2, 1) | Tab change, toggle, chip toggle |
| `motion-sheet` | 320ms | spring(180, 22) | Bottom sheet in/out |
| `motion-hero` | 480ms | spring(160, 20) | Onboarding hero enter |
| `motion-toast` | 200ms in / 300ms out | ease-out / ease-in | Confirmation toast |

### 7.3 Signature motions

- **Save confirmation** — the ✓ icon in the Saved toast draws itself in 240ms as if traced by an invisible pen. This is the app's small delight moment.
- **Category tap → category detail** — the card "expands" full-screen via shared-element transition (`react-native-reanimated`). Outline and color fill morph; content inside crossfades.
- **Item state transition** — when an item moves from `analyzing` → `fully_indexed`, the state chip pulses once (scale 1 → 1.06 → 1 over 320ms) in mustard. The user notices even when passively scrolling.
- **Chat bubble entry** — bot bubbles fade + slide-up 8pt over 200ms. User bubbles pop from bottom-right corner over 180ms.

### 7.4 Haptics

- Save confirmation: soft-medium impact (iOS `impactAsync(Medium)`, Android `HapticFeedbackConstants.CONFIRM`).
- Enhance CTA tap: light impact.
- Long-press bulk-select: medium impact.
- Delete confirmation: heavy impact.
- Everything else: no haptic. Restraint keeps the meaningful ones meaningful.

---

## 8. Screen Archetypes

Every screen in the app falls into one of six archetypes. Each has default color, layout, and mood.

### 8.1 Hero (onboarding, empty state, achievement)

- Background: sage with subtle radial gradient
- Contents: illustration + display-xl headline + body sub + 1 CTA
- One personality moment; no cards
- Reference screens: onboarding, first-import success, "you've saved 100 reels" milestone

### 8.2 Dashboard (Home)

- Background: cream
- Contents: greet row → search bar → stat row (2-up colored cards) → mini chart card (peri) → recently-saved thumb row → collection rails → category rails
- Multiple colored blocks; the "poster" moment
- Reference screen: Home / Dashboard

### 8.3 Grid (Categories, Library)

- Background: sage
- Contents: title + filter chip → vertical stack of colored category cards, rotating colors mustard → coral → peri → cream
- Poster-column feel; scrollable
- Reference screens: Categories, Collections list

### 8.4 Recall (Chat, Search answer)

- Background: ink (dark)
- Contents: brain-mark header → chat bubbles (cream + coral) → suggestion chips → input pill
- Only screen archetype in the app that uses dark mode
- Peri active-state accent visible in tab bar
- Reference screens: Chat, Search results with answer synthesis

### 8.5 Detail (Item, Collection, Import progress)

- Background: cream
- Contents: full-bleed hero image or color block → title + meta → body content → actions row at bottom
- Calm, editorial layout
- Reference screens: Item detail sheet, Import progress, Collection detail

### 8.6 Utility (Settings, Profile, Legal)

- Background: cream or sage (calm)
- Contents: title → grouped setting rows in cream with 2pt outlines
- No illustrations, minimal color
- Reference screens: Settings, Profile, Data export, Danger zone

**Screen archetype → tab mapping (v1 navigation):**

| Tab | Root screen | Archetype |
|---|---|---|
| Home | Home dashboard | Dashboard |
| Categories | Category grid + Collections rails | Grid |
| Chat | AI recall chat | Recall |
| Library | Full library + filters | Grid / Detail |
| Settings | Settings | Utility |

---

## 9. Screen Inventory (v1)

Every screen we need to design for v1, mapped to archetype + color + priority.

| # | Screen | Archetype | Bg | Priority | Notes |
|---|---|---|---|---|---|
| 1 | Sign-in | Hero | Sage | P0 | Reel-brain mark hero |
| 2 | Post-sign-in bulk-import invitation | Hero | Sage | P0 | Skippable |
| 3 | IG export guide (3 steps) | Detail | Cream | P0 | Screenshots + arrows |
| 4 | Import upload | Detail | Cream | P0 | File picker CTA |
| 5 | Import progress | Detail | Cream | P0 | Live progress bar |
| 6 | Import complete | Hero | Sage | P0 | "3,240 REELS READY" |
| 7 | Share-permission walkthrough (iOS + Android) | Detail | Cream | P0 | Native OS screenshots |
| 8 | Home dashboard | Dashboard | Cream | P0 | The poster |
| 9 | Categories | Grid | Sage | P0 | Rotating card colors |
| 10 | Category detail | Detail / Grid | Cream | P0 | Filter chips + item list |
| 11 | Collection detail | Detail / Grid | Cream | P0 | Same as category, IG source badge |
| 12 | AI chat | Recall | Ink | P0 | Reel-brain header |
| 13 | Search results with answer | Recall / Detail | Ink → cream split | P0 | Answer on ink, cards on cream |
| 14 | Item detail sheet | Detail | Cream | P0 | Full item data + Enhance CTA |
| 15 | Library browse | Grid | Sage | P0 | Chronological, filters |
| 16 | Saved audio tab | Detail | Cream | P1 | Simple list |
| 17 | Profile | Utility | Cream | P0 | Stats + connected sources |
| 18 | Settings | Utility | Cream | P0 | Grouped rows |
| 19 | Data export | Utility | Cream | P1 | One CTA |
| 20 | Account deletion (danger) | Utility | Cream + coral accent | P0 | Two-step confirmation |
| 21 | Notifications preferences | Utility | Cream | P0 | Toggle list |
| 22 | Empty state — no imports, no saves | Hero | Sage | P0 | "Share your first reel" |
| 23 | Failed item — retry state | Detail | Cream + coral state | P1 | Explain failure |
| 24 | Share-extension mini popup (iOS) | Component | Cream | P0 | 100pt tall |
| 25 | Toast (Android) | Component | Ink | P0 | Floating pill |

---

## 10. Handoff-Ready Design Tokens (JSON)

This block is copy-pasteable into Figma Tokens, Style Dictionary, or Tamagui.

```json
{
  "color": {
    "sage":       { "value": "#C7D3C3" },
    "sage-deep":  { "value": "#AEBFA9" },
    "sage-mist":  { "value": "#E1E8DE" },
    "cream":      { "value": "#F3EFE2" },
    "ink":        { "value": "#15170F" },
    "coral":      { "value": "#E85C3F" },
    "mustard":    { "value": "#E8AC3D" },
    "peri":       { "value": "#6A67E0" },
    "white":      { "value": "#FFFFFF" }
  },
  "font-family": {
    "display": { "value": "Archivo Black" },
    "body":    { "value": "Inter" },
    "mono":    { "value": "IBM Plex Mono" }
  },
  "font-size": {
    "display-xl": { "value": 40 },
    "display-lg": { "value": 30 },
    "display-md": { "value": 22 },
    "display-sm": { "value": 15 },
    "body-lg":    { "value": 16 },
    "body-md":    { "value": 14 },
    "body-sm":    { "value": 12.5 },
    "mono-md":    { "value": 12 },
    "mono-sm":    { "value": 10.5 },
    "mono-xs":    { "value": 9.5 }
  },
  "line-height": {
    "display-xl": { "value": 40 },
    "display-lg": { "value": 32 },
    "display-md": { "value": 24 },
    "display-sm": { "value": 18 },
    "body-lg":    { "value": 24 },
    "body-md":    { "value": 20 },
    "body-sm":    { "value": 18 },
    "mono-md":    { "value": 16 },
    "mono-sm":    { "value": 14 },
    "mono-xs":    { "value": 12 }
  },
  "letter-spacing": {
    "display":  { "value": "-0.01em" },
    "body":     { "value": "0" },
    "mono":     { "value": "0.10em" },
    "mono-loose": { "value": "0.14em" }
  },
  "space": {
    "0":  { "value": 0 },
    "1":  { "value": 4 },
    "2":  { "value": 8 },
    "3":  { "value": 12 },
    "4":  { "value": 16 },
    "5":  { "value": 20 },
    "6":  { "value": 24 },
    "8":  { "value": 32 },
    "10": { "value": 40 },
    "12": { "value": 48 }
  },
  "radius": {
    "sm":   { "value": 8 },
    "md":   { "value": 14 },
    "lg":   { "value": 18 },
    "xl":   { "value": 22 },
    "2xl":  { "value": 32 },
    "full": { "value": 999 }
  },
  "border": {
    "default": { "width": 2, "color": "{color.ink}" },
    "inverse": { "width": 2, "color": "{color.cream}" }
  },
  "motion": {
    "tap":   { "duration": 120, "curve": "ease-out" },
    "swap":  { "duration": 220, "curve": "cubic-bezier(0.4, 0, 0.2, 1)" },
    "sheet": { "duration": 320, "curve": "spring(180, 22)" },
    "hero":  { "duration": 480, "curve": "spring(160, 20)" },
    "toast-in":  { "duration": 200, "curve": "ease-out" },
    "toast-out": { "duration": 300, "curve": "ease-in" }
  }
}
```

---

## 11. Prompt Templates for Feeding a Design Agent

Use these as-is when handing a screen to Claude design or a designer.

### 11.1 Standard screen prompt

> Design the [SCREEN NAME] screen for SpillTheReel, a mobile second-brain app.
> **Brand rules:** see `design-system.md`. Follow the palette (sage / cream / ink / coral / mustard / peri), Archivo Black for display all-caps, Inter for body, IBM Plex Mono for data. Every card and button has a 2pt ink outline. Radii per token table. No drop shadows.
> **Archetype:** [pick one of the six from §8].
> **Screen intent:** [1-sentence goal].
> **Content elements to include:** [list from PRD].
> **One personality moment:** [pick illustration OR hero color card, not both].
> **Copy voice:** short, imperative, second person, zero exclamation points.
> **Deliverable:** iPhone 15 Pro (390 × 844pt) mockup + Android Pixel 8 (412 × 915pt) variant. Both mockups on a `sage` canvas with subtle rotation (~1.5°) for the marketing shot.

### 11.2 Component prompt

> Design the [COMPONENT] component. Follow the spec in `design-system.md` §6.[X]. Deliverable: default + all documented states, each in a labeled frame, on a cream artboard. Include token annotations (e.g., `radius-full`, `2pt ink`, `display-sm`).

### 11.3 Illustration prompt

> Draw a [subject] illustration for the [category / empty state / hero] slot. Style: cartoon-poster, 5–7pt black outline, single flat color fill from [palette color]. No gradients, no shading, no small serifs, no isometric. Reference: 1970s Penguin paperback covers + Tomi Ungerer. Deliverable: SVG, 200 × 200 viewBox, on transparent background.

---

## 12. What NOT to Do (anti-patterns)

- ❌ **No pastel gradients.** Sage-to-cream fade may look tempting; it looks like a wellness app. Reject.
- ❌ **No emoji as primary iconography.** OK in copy microdoses (👋 in a greeting is fine); never as a functional icon.
- ❌ **No black-on-coral text.** Fails contrast — always white on coral.
- ❌ **No skeuomorphic textures.** Paper, leather, film-grain overlays are out.
- ❌ **No auto-generated dark mode.** The Recall / Chat archetype is our "dark mode." Everything else stays warm.
- ❌ **No screen with more than one heavy illustration.** Two mascots on one screen is dead on arrival.
- ❌ **No borderless colored cards.** Every colored surface needs its 2pt ink outline or it looks like a bad SaaS.
- ❌ **No stock illustration.** Never source from unDraw / Storyset / etc. Everything drawn in-brand.
- ❌ **No animated splash screens > 800ms.** People perceive them as slow, not premium.
- ❌ **No settings screen that uses more than 2 colors.** Utility archetype = cream + ink. Enforce.
- ❌ **No progress bar without a numeric value beside it.** Users deserve the number.
- ❌ **No text-only error states.** Every error gets a coral state chip and a retry action.
- ❌ **No "on-device model" / "process on this device" language** — this is a myth from the rough HTML. We use cloud (Gemini + Cognee). If a settings toggle says anything about local processing, cut it.
- ❌ **No inflated stats.** The Home stat card shows real counts; if the user has 3 saves, it says "3", not "003" or "3+".

---

## 13. Accessibility Floor

Non-negotiable in every screen:

- **Touch target minimum 44 × 44pt** for all interactive elements.
- **Color-contrast pairs validated in §2.4.** Any new color pair added to the app runs through a WCAG check before merging.
- **Dynamic type support.** All Inter text respects the OS text-size setting up to +2 steps. Archivo Black display copy scales but caps at +1 step (visual density preserved).
- **VoiceOver / TalkBack labels** on every icon-only button, chip, and toggle. Never rely on a color alone to communicate state.
- **Motion reduce respected.** If the OS `Reduce Motion` flag is on, all `motion-sheet` / `motion-hero` fall back to `motion-swap` (fade + slide 8pt), and the ✓ pen-tracing animation is replaced with a static ✓.
- **Voice search language:** on-device speech-to-text via `expo-speech-recognition`. Language auto-follows OS locale.

---

## 14. Handoff Deliverables (what design produces)

- Figma file: `SpillTheReel-DS-v1.fig` with pages: Foundations, Components, Screens, Illustrations, Icons, Prototypes.
- Tokens exported via Figma Tokens → `packages/shared-tokens/tokens.json` (imported into RN with `expo-tokens` or Tamagui).
- Illustration set exported as individual SVGs → `apps/mobile/assets/illustrations/`.
- Icon set exported as an `@svgr/webpack`-consumable folder → `apps/mobile/assets/icons/`.
- A single `README-design.md` in `apps/mobile/` linking back to this file and calling out the current sprint's design PRs.

---

## 15. Design → Engineering Contract

- Every screen delivered must reference the archetype (§8) and use only tokens (§10). Rogue values fail code review.
- Every new component gets a Storybook (RN Storybook) story with all states before it merges into a screen.
- Illustrations ship as SVGs, not PNGs. If they must be PNGs, they must be @3x with alpha and < 40 KB.
- Motion specs come with the token name from §7.2, not raw ms values.
- Any deviation from these rules requires an entry in the "Decision Log" in [buildphase.md](./buildphase.md).

---

## 16. Logo & App Icon

### 16.1 Concept — "The Reel Drip"

The mark is a **play triangle melting into a droplet**, accompanied by a small satellite drop — a single-shape composition that literalizes the app's name and identity in under 100ms of viewing.

Rationale:
- **Play triangle** = universal short-form video shorthand. Every gen-z user has tapped ten thousand of these. Instant "reels" recognition, no explanation.
- **The melt into a droplet** literalizes "spill." The name is the shape. When you say "SpillTheReel" the logo answers.
- **The satellite drop** breaks symmetry, adds motion, and gives the mark character — it feels captured mid-action, not static.
- **Melty/liquid aesthetic** is a peak-current gen-z visual signal (Y2K liquid revival, streetwear graphic energy, Brat-adjacent). The mark reads as *made now,* not as a stock SaaS icon.
- **Heavy 9pt ink outline** + flat coral fill matches the app's signature outline treatment. The logo lives natively inside the interface.
- **Survives at 16px favicon** — the drip may blur but the play triangle stays legible. Icon reads at every size.

### 16.2 Logo variants

Three variants ship in v1:

1. **Mark-only** — the reel-brain shape alone. Used inside the app (chat header, splash, tab-bar-adjacent).
2. **Horizontal lockup** — mark + wordmark side-by-side. Used in marketing site header, App Store listing, footer signatures.
3. **Stacked lockup** — mark above wordmark, centered. Used in onboarding hero, launch screen, print / merch.

### 16.3 Wordmark specifications

- Wordmark: `SPILLTHEREEL` set in **Archivo Black** all-caps, letter-spacing `-0.02em` (slightly tighter than body display).
- No custom letterforms in v1 — we use Archivo Black straight. Custom wordmark drawing is a v1.5 investment.
- Wordmark color follows surface: `ink` on light surfaces, `cream` on ink surfaces.
- Optical alignment: baseline of the wordmark sits at ~60% of the mark's height in the horizontal lockup (mark rises taller than the caps).

### 16.4 Color variants of the mark

The mark ships in four color combinations. Each is a fixed asset — never recolor at runtime.

| Variant | Fill | Outline | Use |
|---|---|---|---|
| **Primary** | `coral` | `ink` 9pt | Onboarding, marketing hero, App Store icon, splash on sage |
| **Chat** | `peri` | `cream` 9pt | AI chat / recall screens on ink background |
| **Reversed** | `cream` | `ink` 9pt | On coral / mustard / peri surfaces where coral would compete |
| **Monochrome** | `ink` | `ink` 9pt | Favicon, print single-color contexts, embroidery, App Store metadata |

### 16.5 Construction rules

- **Play triangle:** flat left edge (vertical), apex pointing right. Left edge from `(60, 50)` to `(60, 175)`, apex at `(190, 112)` in a 260×280 viewBox. Not equilateral — slightly elongated for a "reel" feel.
- **Melt transition:** the bottom edge of the triangle (from apex back to bottom-left corner) is broken at ~30% back from the apex, where the drip descends. The drip re-joins the bottom edge at ~40% before the bottom-left corner. This asymmetric attachment gives the mark its "captured mid-motion" energy.
- **Drip:** ~55pt wide at max, ~75pt tall (drop of roughly 65pt below the triangle's original bottom edge). Bezier curves are unequal on left vs right side of the drip — right side has a slightly longer descent — reinforcing the sense that gravity is real and the mark is *falling*.
- **Satellite drop:** small circle, radius 17pt (in the same viewBox), placed at `(212, 232)` — down and right of the main mark. Same fill + outline weight as the main mark.
- **Outline weight:** 9pt at 260×280 viewBox. Scales proportionally. Never below 4pt equivalent at final render size.
- **Stroke joins/caps:** `stroke-linejoin: round`, `stroke-linecap: round`. Rounded corners give the mark its friendly, poster-drawn feel — sharp corners would make it feel harsh.
- **No inner details.** No highlights, no gradients, no film-sprocket dots. The shape does all the storytelling. Restraint is the point.

### 16.6 Sizes & clear-space

- **Minimum sizes:**
  - Mark alone: 20pt tall (below this, drop the satellite drop and use just the melting play triangle).
  - Mark at 12pt or below (favicon range): use Monochrome variant with satellite dropped.
  - Horizontal lockup: wordmark height ≥ 14pt.
  - Stacked lockup: mark ≥ 64pt.
- **Clear space:** minimum padding around the mark equals **the diameter of the satellite drop** on all sides. Never let another element cross the mark's outline. The satellite drop counts as part of the mark's silhouette for clear-space math.

### 16.7 App icon (iOS + Android)

- **1024 × 1024 master.** Blob fills ~72% of the icon area, centered slightly high (56% from bottom) so the filmstrip sits on the icon's horizontal centerline.
- **Background:** solid `sage`. No gradient. No inner shadow. Corner mask handled by the OS on iOS; Android adaptive icon uses `sage` background layer + mark as foreground layer.
- **iOS shape:** relies on OS squircle mask.
- **Android adaptive icon:** foreground = mark scaled to fit the 66×66 keyline safe zone; background = solid `sage`.
- **Monochrome (Android 13+):** the Monochrome variant, `ink` mark on transparent.

### 16.8 Splash / launch screen

- Sage background, mark centered, stacked-lockup wordmark below.
- No spinner. No loading text.
- Displays for ≤ 800ms; if the app takes longer, the splash cross-fades to a subtle animated version where the sprocket holes pulse once.

### 16.9 Anti-patterns for the logo

- ❌ **No perfect / symmetric drip.** The asymmetric fall is the character; a centered symmetric drip reads as a Pinterest pin.
- ❌ **No filmstrip sprockets inside the play triangle.** The play triangle already means "reel"; adding sprockets over-explains and clutters.
- ❌ **No shading, gloss, or 3D bevel.** Flat fills only. The 2020s made "flat + heavy outline" the language; we stay there.
- ❌ **No gradient inside the mark.** Solid coral / peri / cream / ink only.
- ❌ **No stretched proportions.** Uniform scale. Never squash horizontally or vertically to fit a container.
- ❌ **No inner highlights or "gloss dot."** Would date the mark to 2007 immediately.
- ❌ **No rotation** in-app. The mark is not a loading spinner. The mark's melt already implies motion; a rotating mark eats that meaning.
- ❌ **No satellite drop repeated or multiplied.** Exactly one satellite. Three drops reads as sweat/rain, not "spill."
- ❌ **No wordmark alone** (without mark) at small sizes — always pair with the mark below 48pt height.

### 16.10 Reference SVG (Primary variant — ready to hand off)

Files already produced in `assets/logo/`:

- `reel-drip-primary.svg` — coral fill on ink outline (default)
- `reel-drip-chat.svg` — peri fill on cream outline (dark-mode variant)
- `reel-drip-monochrome.svg` — ink fill (favicon / print / single-color)
- `reel-drip-lockup-horizontal.svg` — mark + SPILLTHEREEL wordmark

The primary variant is the shape reference; a designer refines the two bezier curves on the drip for perfect visual balance. The path is:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 280" width="260" height="280">
  <path d="M 60 50 L 190 112 L 155 129 C 172 155, 172 198, 142 220 C 122 234, 100 223, 94 200 C 90 175, 102 158, 110 151 L 60 175 Z"
        fill="#E85C3F" stroke="#15170F" stroke-width="9"
        stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="212" cy="232" r="17" fill="#E85C3F" stroke="#15170F" stroke-width="9"/>
</svg>
```

**Legacy note.** The earlier "reel-brain" (blob + filmstrip band) concept has been deprecated. Its files in `assets/logo/reel-brain-*.svg` remain in the repo as historical reference only; do not use them anywhere in-product or in marketing. All new work uses `reel-drip-*`.

Optional v1.5 flourishes a designer may explore:
- A subtle risograph misprint offset (fill offset 3–4pt from outline) on marketing surfaces only.
- Custom wordmark (§16.11).
- An animated variant where the satellite drop bounces once on app launch (300ms spring, ends static).

### 16.11 Wordmark exploration prompt (for designer / agent)

> Explore three custom-drawn wordmarks for "SPILLTHEREEL" in the spirit of Archivo Black but with two custom touches:
> 1. The double L in "SPILL" ligated so the second L's ascender leans slightly right, suggesting a drip.
> 2. The middle "TH" of "THEREEL" flush-fitted with a soft dot at the base of the T (a "spilled dot").
> All caps, letter-spacing tight (~-0.03em), single line. Deliver in `ink` on `cream`. Three variants ranging from subtle to expressive. Any accepted variant becomes the v1.5 wordmark; v1 ships with straight Archivo Black.

### 16.12 Logo assets checklist

| Asset | Format | Size |
|---|---|---|
| Mark — Primary | SVG | vector |
| Mark — Chat | SVG | vector |
| Mark — Reversed | SVG | vector |
| Mark — Monochrome | SVG | vector |
| Horizontal lockup | SVG | vector |
| Stacked lockup | SVG | vector |
| iOS app icon | PNG | 1024×1024 |
| Android adaptive foreground | SVG + PNG | 432×432 safe zone |
| Android adaptive background | color | `#C7D3C3` |
| Android monochrome | SVG | 432×432 |
| Favicon | PNG | 32×32 + 16×16 |
| Social share (og:image) | PNG | 1200×630 |
| App Store screenshot marketing icon | PNG | 512×512 |

---

## 17. Open Questions

- **Dark mode-wide toggle** — currently only the Recall archetype is dark. Do we ever want to let power users flip the whole app to ink-background? v2 consideration.
- **App icon** — the reel-brain mark on sage vs on ink. Test both in the App Store screenshot suite before submit.
- **Sound design** — save confirmation sound? Recommend NO for v1 (annoyance risk on repeat saves). Revisit at v1.5.
- **Illustration for Sports / Politics / News categories** — not in canonical taxonomy yet; add during design phase if data shows demand.
