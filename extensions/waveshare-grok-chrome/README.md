# VibePulse — Waveshare Grok 0.4.1

Single-bot touch presentation for the real Grok Bot website. Open your Grok Bot page and activate BOT. No bot carousel, swipe navigation or camera integration. The layout stays attached to the bot page where it was enabled.

## Update

In chrome://extensions reload VibePulse and confirm version 0.4.1. Then reload the Grok tab once (with no active call) and click BOT. F11 is optional; full screen uses the Waveshare display most effectively. The extension has content scripts only on https://grok.com/bot/* and uses activeTab + scripting for its toolbar toggle.

## Design

Larger warm-white avatar, softer light and shadows, quiet background, one Call Grok action. During calls the original Grok SVG animation and audio waveform canvases remain mounted. Decorative motion follows only Grok’s textual call state; there is no extension speech/audio processing; Swedish and English are handled by Grok itself. The interface text stays English.

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

The original animated avatar was previously verified during a real user-started Grok call in the custom layout. Version 0.4.1 still needs a real Grok call and physical Waveshare acceptance after reloading the installed extension.

## Panel cards

The panel card strip is disabled in 0.4.1: no `#vp-panel` is created, no
`/api/panel` request is made, and no bridge-offline card is shown. The optional
renderer and service-worker handler remain gated by `SHOW_PANEL = false` in
`display.js` and `background.js` for later re-enabling. Local usage polling is
unchanged and still uses the existing localhost host permission.

## Call view (0.4.1)

The two original waveform canvases are stacked in one box, centered horizontally
and vertically in the call pill area left of the mic. Native DOM order is assumed
to be bot/output first (`wave-0`), user/mic second (`wave-1`); confirm this mapping
in a live call after native markup changes. Speaking selects the bot wave;
listening selects the user wave. Idle/thinking show the bot wave at 45% opacity.
Selection crossfades over 200ms. Canvas width/height metadata supplies
`--vp-wave-ar`; contained canvases retain their aspect ratio without CSS filters
or scale transforms. Both native nodes stay mounted and keep drawing themselves;
no canvas contexts, pixels or audio are accessed.

A small status card under the title shows “Grok Bot · Speaking” or “Listening”.
It fades/slides in on those states, holds through idle/thinking for four seconds,
then fades out over 300ms. Reduced motion keeps the opacity fade without sliding.
Text is assigned only through `textContent`.

`html[data-vp-state]` exposes Grok’s raw `idle`, `listening`, `thinking`,
`speaking` or `connecting` state (unknown states fall back to idle). Idle art
breathes slowly beneath a subtle rotating highlight. Listening adds two calm,
cool rings; thinking uses an orbiting arc and dims the bot wave to 45%.
Speaking adds a small avatar bob and faster warm rings with the full-opacity bot wave.
Frame layers crossfade over 320ms; Grok’s own SVG animation stays intact.
These effects illustrate call state, never audio amplitude.

Touching the pill or native mic/end/start controls gives a 180ms spring press
and a 450ms ripple at the touch point. Passive pointer listeners leave native
click handling intact. Hover effects apply only to hover-capable devices.
Reduced motion disables breathing, bobbing, rings and ripples, retaining opacity
crossfades. Animations use only transform and opacity; no animation frame loop,
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
