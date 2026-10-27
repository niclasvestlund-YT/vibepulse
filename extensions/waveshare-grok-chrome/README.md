# VibePulse — Waveshare Grok 0.4.0

Single-bot touch presentation for the real Grok Bot website. Open your Grok Bot page and activate BOT. No bot carousel, swipe navigation or camera integration. The layout stays attached to the bot page where it was enabled.

## Update

In chrome://extensions reload VibePulse and confirm version 0.4.0. Then reload the Grok tab once (with no active call) and click BOT. F11 is optional; full screen uses the Waveshare display most effectively. The extension has content scripts only on https://grok.com/bot/* and uses activeTab + scripting for its toolbar toggle.

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

Session storage contains only presentation on/off plus the selected bot page path, local to this site's tab. These values are page-readable, not secrets, and may survive browser session restoration. No account tokens, transcript, audio or usage values are stored or transmitted. No extension network calls or internal APIs. Grok usage is labelled Not connected; no quota is invented.

## Validation

Local fixture tests passed at 1024x576 and 1024x393: delayed call startup, native mic/end handlers, interruption display, native Resume, return to idle, and all four live controls inside the viewport and hit-testable. No carousel remains. JS syntax checks pass. Fixture simulations do not prove the real service cannot fail.

The original animated avatar was previously verified during a real user-started Grok call in the custom layout. Version 0.4.0 still needs a real Grok call and physical Waveshare acceptance after reloading the installed extension.

## Panel cards (0.3.0)

Below the bot status, a text-only strip shows main CI, `lac-pc` runner state,
open PR count and the deploy lock from the local tokenserver's `/api/panel`.
It polls every 20 seconds, with a 15-second service-worker cache and a
2.5-second timeout per port (8737, then 8741). Unreachable bridges show
**Bridge offline**. During calls the strip becomes a compact single line;
status dots distinguish ok, warning, failure and information cards.

The existing `http://127.0.0.1/*` host permission is unchanged. Feed values
are rendered with `textContent`; the page never fetches GitHub or credentials.
Configure `VIBEPULSE_PANEL_REPO` on the tokenserver (default
`niclasvestlund-YT/personal-agent-guide`), and optionally `GIST_ID` for a
`panel.json` inbox. Its format is:

```json
{"cards":[{"id":"note","title":"Review","body":"Ready for review","status":"info","updated_at":"2026-10-28T12:00:00Z"}]}
```

See the tokenserver README for strict validation and size limits. Reload the
extension after installing this version. Browser layout and physical USB
screen acceptance still require manual verification.

## Card strip

Space Grotesk pills enter with a gentle 8px slide and fade over 340ms,
staggered by 70ms. Cards retain their identity across polls; only new cards
enter, removed cards fade out over 200ms, and status changes give a single
900ms glow with a status-colored dot. Bridge offline stays muted. During
calls, smaller pills stay in one row with delays capped at 40ms. Reduced
motion uses simple opacity fades without movement or status pulses.

## Call view (0.4.0)

The two original waveform canvases form a centered flex row inside the call
pill, with a small gap and fades at the outer edges only. Each canvas keeps its
intrinsic aspect ratio, read once from its width/height metadata; no pixels or
audio are read, copied or synthesized. Native nodes stay in their original DOM.

`html[data-vp-state]` exposes Grok’s raw `idle`, `listening`, `thinking`,
`speaking` or `connecting` state (unknown states fall back to idle). Idle art
breathes slowly beneath a subtle rotating highlight. Listening adds two calm,
cool rings; thinking uses an orbiting arc and dims the native waves to 45%.
Speaking adds a small avatar bob and faster warm rings with full-opacity waves.
Frame layers crossfade over 320ms; Grok’s own SVG animation stays intact.
These effects illustrate call state, never audio amplitude.

Touching the pill or native mic/end/start controls gives a 180ms spring press
and a 450ms ripple at the touch point. Passive pointer listeners leave native
click handling intact. Hover effects apply only to hover-capable devices.
Reduced motion disables breathing, bobbing, rings and ripples, retaining opacity
crossfades. Animations use only transform and opacity; no animation frame loop,
animated filters, new network fetches, internal APIs or permissions were added.

## Font

Bundled Space Grotesk (400, 500 and 700) loads locally through extension URLs
with `font-display: swap` and system-ui/Arial fallbacks, including the opening
overlay and native control labels. Bot names use medium weight and slightly
tighter tracking; small labels have medium weight and wider tracking. All text
is at least 12px; card and usage numbers use tabular figures.

Space Grotesk © 2020 The Space Grotesk Project Authors, licensed under the
[SIL Open Font License 1.1](fonts/OFL.txt). Font files and the license are bundled
in `fonts/`; no remote font service is used.
