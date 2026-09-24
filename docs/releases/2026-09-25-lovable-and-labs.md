VibePulse v1.3.0 adds optional Lovable credits and more control over the pages on your desk display.

## Lovable credits, with an experimental browser source

Enable Lovable in Labs to show credits left, plan and freshness. The optional local Chrome extension reads explicitly labelled values from Lovable's billing page while a project is open. Credentials stay in the browser; project source and chats are not read. Browser readings become CACHED after three minutes without an update and retain their age across host restarts.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.3.0/docs/img/vibepulse-lovable.png" width="480" height="480" alt="Native LVGL Lovable page with illustrative fixture data"></p>

This is a native simulator capture with illustrative data. The browser source does not supply the reset countdown shown here. Dates and daily chat percentages are not guessed into reset or build-credit values.

Lovable's MCP documentation describes a credit balance, but the authenticated schema and response checked on 24 September omitted it. The official OAuth monitor remains available; `--lovable-source browser|auto|mcp` selects the source without changing the display contract. Browser mode needs no MCP login.

**[Install, verify or remove the Lovable integration](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.3.0/docs/lovable-pulse.md).** The guide includes exact workspace selection, custom ports, service startup, privacy and future API migration.

## Pick your pages and restart from Labs

Claude Code and Codex quota pages now have independent visibility switches. RESTART NOW applies saved choices from the last Labs page. Existing choices migrate, and disabling every page leaves a message directing you back to Settings. Agent activity monitoring remains separate.

<p align="center"><img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.3.0/docs/img/vibepulse-labs-providers.png" width="480" height="480" alt="Native LVGL Labs provider visibility controls"></p>

On the round 1.75, hold BOOT for three seconds to open Settings; RESET is the hardware reset. Follow the actual board's USB guide and select its profile before building. Local companion checkouts can change the app registry, so inspect those build inputs too.

## Reliability fixes

- Daily credit pools and their reset dates cannot become the primary balance or period.
- MCP event streams accept LF, CRLF and CR record boundaries.
- Windows task installation/runner retain explicit Lovable source options.
- The display changes a retained LIVE reading to CACHED when host polling stops.
- Disabled quota tiles are skipped safely, and carousel navigation remains bounded with all pages off.

## Verification and limits

The preceding implementation passed the complete local host gate, including 256 Labs combinations through shared LVGL and 961 tokenserver tests, plus an ESP-IDF build for the round 1.75. Final release changes are gated on the required GitHub checks for the merged revision.

A real one-time browser reading reached the round display and was photographed. Automatic refresh from the installed Chrome extension and Windows browser operation remain unverified. The browser integration is experimental and may need adjustment when Lovable changes its page. Latest Labs touch acceptance and board-specific OTA/rotation limits remain separate from simulator or CI results. A source release does not flash a connected display.

## Upgrade

Fetch the release source and follow your board's guide. Preserve your existing `secrets.h` and host-service options. Restart tokenserver after updating its code; new firmware features require an explicitly selected board build and an authorized installation.

No firmware binary is attached: locally built `torget.bin` contains private Wi-Fi/device configuration.

[Full changelog](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.3.0/CHANGELOG.md) · [Compare v1.2.0...v1.3.0](https://github.com/niclasvestlund-YT/vibepulse/compare/v1.2.0...v1.3.0)
