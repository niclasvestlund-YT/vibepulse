# VibePulse — Waveshare Grok 0.3.0

Single-bot touch presentation for the real Grok Bot website. Open your Grok Bot page and activate BOT. No bot carousel, swipe navigation or camera integration. The layout stays attached to the bot page where it was enabled.

## Update

In chrome://extensions reload VibePulse and confirm version 0.3.0. Then reload the Grok tab once (with no active call) and click BOT. F11 is optional; full screen uses the Waveshare display most effectively. The extension has content scripts only on https://grok.com/bot/* and uses activeTab + scripting for its toolbar toggle.

## Design

Larger warm-white avatar, softer light and shadows, quiet background, one Call Grok action. The font family is read from the actual Grok page (currently universalSans with its original fallback fonts). During calls the original Grok SVG animation and audio waveform canvases remain mounted. There is no synthetic speech animation and no extension speech/audio processing; Swedish and English are handled by Grok itself. The interface text stays English.

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

The original animated avatar was previously verified during a real user-started Grok call in the custom layout. The polished 0.3.0 release still needs the user to reload the installed extension and check on Waveshare.
