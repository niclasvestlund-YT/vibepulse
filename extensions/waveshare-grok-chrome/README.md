# VibePulse — Waveshare Grok 0.5.1

Single-bot touch presentation for the real Grok Bot website. Open your Grok Bot page and activate BOT. No bot carousel, swipe navigation or camera integration. The layout stays attached to the bot page where it was enabled.

## Update

In chrome://extensions reload VibePulse and confirm version 0.5.1. Then reload the Grok tab once (with no active call) and click BOT. F11 is optional; full screen uses the Waveshare display most effectively. The extension has content scripts only on https://grok.com/bot/* and uses activeTab + scripting for its toolbar toggle.

## Design

Charcoal call view (#141416), dark surfaces (#1c1d21), hairline borders, near-white text and a dark microphone control; hang-up stays red. The main avatar stands alone, with one Call Grok action and no constellation. During calls the original Grok SVG animation and audio waveform canvases remain mounted. Decorative motion follows only Grok’s textual call state; there is no extension speech/audio processing; Swedish and English are handled by Grok itself. The interface text stays English.

## State handling

- Idle: original start button, styled as Call Grok.
- Incremental call startup: Opening voice chat; preserve the layout and any available native mic/end controls, with a bounded 15-second fallback.
- Call: original animated figure, waveform, microphone, end button, settings and transcript.
- Native call failure: The call was interrupted and the native Resume button when Grok supplies it. No automatic retry, new call or fabricated connection state.
- Original view / Escape: restore the full page without ending the call. Settings/transcript deliberately restore the native page.
- Unknown markup or visible role-based dialogs: restore the original view. The extension cannot prevent service/network call failures.

## Privacy

Session storage contains only presentation on/off plus the selected bot page path, local to this site's tab. These values are page-readable, not secrets, and may survive browser session restoration. No account tokens, transcript, audio or usage values are stored or transmitted. Only the extension service worker polls the local usage bridge; no internal Grok APIs are used. Grok usage is labelled Not connected; no quota is invented.

## Validation

Local fixture tests passed at 1024x576 and 1024x393: delayed call startup, native mic/end handlers, interruption display, native Resume, return to idle, and all four live controls inside the viewport and hit-testable. No carousel remains. JS syntax checks pass. Fixture simulations do not prove the real service cannot fail.

The original animated avatar was previously verified during a real user-started Grok call in the custom layout. Version 0.5.1 still needs a real Grok call and physical Waveshare acceptance after reloading the installed extension.

## Panel cards

The panel card strip remains disabled in 0.5.1: no `#vp-panel` is created, no
`/api/panel` request is made, and no bridge-offline card is shown. The optional
renderer and service-worker handler remain gated by `SHOW_PANEL = false` in
`display.js` and `background.js` for later re-enabling. Local usage polling is
unchanged and still uses the existing localhost host permission.

## Call view (0.5.1)

The two original waveform canvases share a slot left of the mic. Native DOM
order is assumed to be bot/output first (`wave-0`), user/mic second (`wave-1`);
confirm this mapping in a live call after native markup changes.
`html[data-vp-state]` selects the bot wave when speaking and the user wave when
listening. The active wave fills the slot width; the inactive wave remains below,
centered at 35% width and 25% opacity. Idle/thinking/connecting keep both small
and dim, with the bot above. Only opacity transitions over 280ms ease-out; widths change without animated resampling.
Canvas width/height metadata supplies `--vp-wave-ar` to preserve aspect ratio.
Canvas-only `invert(1) grayscale(1) brightness(1.6) contrast(1.2)` targets
near-white bars, with a light blue glow on the active wave. The slot uses equal
11px insets and flex centering; branch transforms, filters and opacity are reset.
Canvas CSS width/height follow the intrinsic aspect ratio with `image-rendering:auto`. No blur or waveform scale transforms
are used. Both native nodes stay mounted and draw themselves; no canvas
contexts, pixels or audio are accessed.

The status line remains the primary call-state label (22px, #b4b4bc). The
chip shows only “Grok Bot · Speaking” while speaking; listening has no chip.
It stays mounted above the native layers at a stable z-index and fades only
through opacity over 280ms ease-out, including when hidden. There is no grace
period or scale pop.

`html[data-vp-state]` exposes Grok’s textual state. Speaking fades in a very
subtle soft glow ring without moving or scaling the avatar. Other decorative
idle/listening/thinking rings remain; state layers crossfade over 280ms.
Grok’s own SVG animation stays intact. These effects illustrate call state,
never audio amplitude.

Touching the pill or native mic/end/start controls gives a 180ms spring press
and a 450ms ripple at the touch point. Passive pointer listeners leave native
click handling intact. Hover effects apply only to hover-capable devices.
Reduced motion disables breathing, animated rings and ripples, retaining opacity
crossfades. Secondary text is #b4b4bc, titles are white, borders are crisp 1px
hairlines at 12% white, avatar shadows are removed, and Geist uses antialiasing. No animation frame loop,
animated filters, new network fetches, internal APIs or permissions were added.

## Font

Bundled Geist (400, 500 and 700) loads locally through extension URLs
with `font-display: swap` and a system-ui fallback, including the opening
overlay and native control labels. Bot names use medium weight and slightly
tighter tracking; small labels have medium weight and wider tracking. All text
is at least 12px; card and usage numbers use tabular figures.

Geist by Vercel, licensed under the
[SIL Open Font License 1.1](fonts/OFL.txt). Font files and the license are bundled
in `fonts/`; no remote font service is used.

### 0.5.1 dark-wave investigation

The old canvas rule already applied an important invert filter; ancestor
`filter:none!important` does not override a child filter (filters are not
inherited). Its 1.05 brightness multiplier offered little lift to inverted
midtones. Native branch opacity/transforms and layout constraints were not fully
reset, and the 250ms width transition resampled the canvas during state changes.
This update strengthens only the canvas filter, normalizes branch presentation,
and removes width animation. Exact native pixel colors and live rendering still
need verification in a real call; no canvas context or pixels were inspected.
