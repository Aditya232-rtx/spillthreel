# SpillTheReel native design execution brief

Prepared 2026-10-18 from the current mobile source and rendered review described
in [PROJECT_REVIEW_2026-10-16.md](PROJECT_REVIEW_2026-10-16.md). Execute through
T12–T19 in [LUNA_IMPLEMENTATION_PLAN.md](LUNA_IMPLEMENTATION_PLAN.md).

## Direction

Make a personal library that feels warm, capable, and easy to return to. Preserve
the cream/ink palette, coral accent, expressive headline face, and restrained
sticker details. Let saved content and understandable actions carry the screen.
The desired impression is a carefully made native utility, with personality in
its typography and a few brand details. Do not turn this into a marketing website.

Primary user jobs, in order:

1. Save something with confidence, including when processing takes time.
2. Find a remembered item through natural language or browsing.
3. Understand import progress and recover from failures without repeating work.
4. Open the original, organize a library, and control account data.

Use the installed [Impeccable skill](../.agents/skills/impeccable/SKILL.md) in its
native **Operate** mode. Run its context step once per execution session; read
the relevant native playbook and craft floor before UI edits. The engine already
ran during review, but PRODUCT.md/DESIGN.md were absent. This brief and the product
decisions in the plan supply the actual context; do not invent research findings.
No GSAP, DOM recipes, large decorative hero, or web detector score is needed here.

## Information architecture proposal

Use four labeled destinations: **Home, Library, Ask, Account**. Put Categories
and Collections inside Library; put Settings inside Account. This is a proposed
implementation direction, not a newly confirmed owner requirement. Keep existing
deep links working through route redirects/aliases while changing navigation.

Home offers a compact greeting, a prominent search/ask entry, recent real saves,
and one contextual action: add a link, import history, or resume an active import.
Library is the complete list with useful filters and collection/category views.
Ask retains its conversational recall behavior and can retain the dark surface.
Account holds identity, actual allowance usage, preferences and privacy actions.

The global tab bar should normally remain available on top-level screens. Detail,
auth, and focused import steps use a back affordance and appropriate native
navigation. Choose whether Ask hides tabs during keyboard entry based on actual
device behavior; preserve a visible way back and unsent text. Do not refactor
route groups before the destination mapping and regression cases are written.

## Visual system

| Layer | Specification |
|---|---|
| Primitive colors | Retain `#15170F` ink, `#F3EFE2` cream, `#E85C3F` coral, `#E8AC3D` gold, `#6A67E0` violet, sage and the current dark Ask surfaces |
| Semantic colors | Define canvas, surface, raised surface, text primary/secondary/disabled, border, accent action, on-accent, selected, success, warning, danger, and focus; define Ask equivalents explicitly |
| Typography | Keep Oswald for short display headings; use a legible native system sans for body/forms first. A bundled Inter alternative requires a deliberate asset/license/performance decision, not a dependency spree. Keep MyLove for the brand mark only |
| Type scale | Start with body 16/24, supporting text 14/20, labels 14/18, titles 24–32 with proportionate leading; small metadata may be 12–13 when nonessential. Support OS font scaling and wrapping |
| Spacing | Use the existing 4-based scale, mostly 16–24 screen margins and 8–16 gaps. Resolve safe areas through insets, never by assuming a device's status-bar height |
| Shape | Use a small consistent radius family. Hard offset shadows may identify primary branded actions; avoid outlining and shadowing every row/card |
| Icons | One consistent icon family/style. Pair navigation icons with visible text; add meaningful accessibility labels for icon-only actions |
| Media | Real thumbnails with stable aspect ratio, loading placeholder and failure fallback. Show source/creator as supporting information; never use a colored box as if it were saved content |
| Motion | Short state transitions and useful feedback using native/installed animation primitives. Respect reduced motion; avoid repeating entrance animations during list refresh |

Contrast measurements for opaque current colors: white on coral is approximately
**3.48:1**, while ink on coral is **5.20:1**; white on violet is approximately
**4.54:1**, and ink on cream **15.72:1**. The older design-system rule that white
on coral is AA for ordinary text is incorrect. Use ink for ordinary coral-button
labels, or define a darker semantic action color for white labels. Recheck actual
opacity, font weight, disabled states and final backgrounds; a palette calculation
does not certify a complete screen. Target 4.5:1 ordinary text and 3:1 large text
and essential control boundaries as the implementation accessibility floor.

## Shared components and state contracts

| Component | Required behavior/states |
|---|---|
| Screen scaffold | Insets, scroll handling, consistent header, keyboard avoidance, accessible background/text pairing |
| Button | Primary/secondary/destructive; pressed, disabled, busy; stable width; accessible label/role; no duplicate submit |
| Text field | Persistent label, help/error, password reveal, focus, autocomplete/keyboard props; error announced and associated |
| Save card | Image, title, creator/source, processing state, open detail; accessibility reads meaningful text once |
| Processing badge | Queued / Processing / Ready / Needs attention / Unavailable; never expose internal state names as raw copy |
| Async region | Initial loading, empty, populated, refresh, stale/offline, retryable error; no generic full-screen spinner for every state |
| Filter chip | Selected state and count only when real; clear-all; usable with VoiceOver/TalkBack |
| Confirmation sheet | Clear consequence, cancel, destructive action, busy/error; focus returns to trigger |
| Import progress | Accepted, waiting, working, ready, unavailable/failed, skipped duplicates; absolute counts; no fictitious percentages |
| Allowance summary | One-time policy, total and bucket remaining; refresh after admission; distinguish quota limit from temporary capacity pause |

Use at least 44pt iOS / 48dp Android hit areas for interactive targets, allowing
layout padding/hitSlop without overlapping targets. Test long labels and large
text rather than hiding problems with clipping or `maxFontSizeMultiplier`.

## Screen-by-screen changes

### Welcome and authentication

Keep the recognizable brand introduction but shorten the path to useful content.
Keep real email/OAuth/reset/name flows. Use readable form text, explicit busy and
validation states, and sensible keyboard actions. Prove cold/warm OAuth callbacks,
password reset links, expired sessions and account switching on a native build.
Never show a successful login/name save based only on a local optimistic flag.

### Home

Remove the hardcoded 342 saves, growth graph and illustrative collection counts.
Show real recent saves and processing summaries. If there are no saves, explain
one next action and offer add-link/import; never populate a new account with fake
history. “See all” navigates to Library. Collection previews open the corresponding
filtered list. A failed recent save offers a real recovery path.

### Library, Categories and Collections

Use a predictable list/grid with a search entry, source filter, and category or
collection chips. Preserve category colors as accents, with text labels. Replace
the repeated dial as the primary navigation; if retained as an optional decorative
interaction, it must not duplicate dozens of accessibility nodes. Use server IDs,
user-scoped cache keys, real counts, stable pagination, pull-to-refresh and an
explicit filter-empty state. Category editing and collection membership must persist.

### Ask

Retain the working search request, cancellation and per-user conversation history.
Improve the readable body face, input reachability, answer spacing, citation
affordances and source-card hierarchy. Suggested prompts must be clearly examples;
do not imply unseen library contents. Distinguish no matches, service failure,
offline and cancelled requests. A citation opens the same item the server bound
to that index. The user can recover typed text after a failed request.

### Item detail

Keep the implemented detail/retry/original link. Order information as title/source,
processing status, useful summary, then expandable transcript and metadata. Expose
organization and deletion once their contracts work. Explain inaccessible originals.
If an action only re-summarizes existing text, label it accordingly; “Analyze video”
must actually invoke media ingestion. Handle deleted items without leaking ownership.

### Import

Use this flow: source → export instructions → select file → validate/preview →
choose within remaining allowance → confirm → track processing → view results.
Do not imply a source supports bulk import until its adapter is implemented.
Keep manual URL saving separately available for supported sources.

Show **“One-time import allowance”**. For free accounts show separate Instagram
and other-app remaining counts; for premium show the shared total. Explain that
uploading another archive does not reset usage. An oversized history is previewed
so the user can select eligible items; never silently truncate or silently admit
more than the server permits. Show duplicates/unsupported entries separately.

Progress comes from persisted import status. “Saved for processing” is different
from “Ready to search.” Show partial success, retriable failures and permanently
unavailable source content. Allow leaving and returning to a running import.
Replacing the timer-based success screen is essential before visual polish.

Replace the inaccurate “original media never leaves Instagram” statement with
plain disclosure of temporary server/provider processing, grounded in the retention
policy actually implemented in T07/T10. Do not promise immediate archive deletion
until the cleanup mechanism and failure paths are verified. “Not now” is clearer
than “Skip forever” for optional onboarding.

### Account and Settings

Use actual profile identity, library totals and allowance status. A platform badge
means “Saved from Instagram,” not “Connected,” unless account linking really exists.
Persist preferences or remove the control until it works. Show export, notification,
and deletion actions only with an honest working state; no toast-only success.
Deletion needs confirmation, pending status, sign-out, and a durable backend job.

## Visual and native acceptance

Capture before/after screens from the same fixtures for empty, populated, loading,
error and long-content variants. Keep synthetic preview data in test fixtures or
a development-only harness; never add an unguarded preview route to shipped builds.
Validate at a small phone size, a typical phone, and a larger phone, plus landscape
where supported. Web previews are useful for composition but do not close native
keyboard, safe-area, share-extension, touch, or screen-reader gates.

On Android, test TalkBack focus order, back navigation, keyboard open/close,
large text, reduced motion, offline recovery and app resume. Repeat the corresponding
iOS checks before an iOS release. Headers/buttons must not overlap notches, system
bars or the home indicator. A long title or an empty image must not break card height
or hide actions. Final QA must use live API contracts with synthetic isolated data,
not only the screenshot fixture states.

The design is accepted when the primary loop is understandable and truthful on
device, every visible control works, typography and state treatment are consistent,
and the interface remains recognizably SpillTheReel. Visual review alone cannot
prove users will love it; test the save/import/find tasks with representative users
before a broad launch and record their failures as follow-up issues.
