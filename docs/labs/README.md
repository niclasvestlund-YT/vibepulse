# Vibe Labs

Optional ways to make VibePulse your own. Start with Claude/Codex usage;
add the displays and integrations you want when you need them.

**LABS is an optional-feature menu, not a package installer.** This checkout
implements the selector described below. Its new menu layout still needs
physical panel review before release; simulator evidence does not authorize a
firmware installation.

## What you get when you install

A fresh installation from `secrets.h.example` starts with three quota pages
and their reset information, local activity when the provider supplies it,
and SETTINGS. Missing data stays unavailable; it is never a made-up zero.
Answering on the panel, cloud relays and sound remain separate opt-ins.

Use the [setup guide](../agent-setup.md) for installation. You do not need
GitHub configuration, a plan cost, a relay account or an experiment to start
using quotas.

## Add features on the display

Hold KEY3 for three seconds, then tap **LABS**. Tap a row to switch it ON or
OFF. **MORE** opens the GitHub choices; **BACK** returns to analytics and
**SETTINGS** returns to the main menu. KEY3 closes any of these pages.

Choices are saved immediately. **RESTART TO APPLY** means the saved choices
differ from the currently running pages: power-cycle the display when ready.
Disabled pages are not allocated at boot, and disabled Max Tracker/GitHub
polling tasks are not started. The menu does not enable a cloud service,
install a provider plugin, or configure anything on the computer.

| LABS choice | What it adds | Set up first on the computer | New install |
|---|---|---|---|
| BURN RATE | Pace/forecast page | Quota history; learning/missing states are explicit | Off |
| MAX TRACKER | Claude and Codex history pages | Usage history; plan identity for relevant labels | Off |
| API VALUE | API-equivalent value page | Current model prices; plan cost for a multiple | Off |
| GITHUB PAGE | Repository stars and forks | One public repository in tokenserver | Off |
| STAR POPUP | New-star moments, independent of the page | The same repository feed | Off |

The ON label is a saved display preference, not a claim that its data source
is ready. Configure the computer first using the linked guides. Missing data
continues to show dashes or the existing setup/pricing state.

### Existing installations and saved choices

Old `secrets.h` files lack `TK_LABS_ANALYTICS_DEFAULT`, so the firmware keeps
burn rate, Max Tracker and Value enabled on their first upgrade. The fresh
sample explicitly sets this default to `0`. The existing GitHub macros seed
their two independent choices. Copying a new sample over an old configuration
is a new seed, not a migration; retain your existing `secrets.h` when upgrading.

After the first successful save, the versioned `vp_labs` NVS record overrides
all these defaults in both directions and survives firmware updates. Full NVS
erasure also erases Labs choices. **COULD NOT SAVE** means the requested change
was not saved; the displayed choices remain at their last successful values.
Read errors or an unknown record version preserve the record and use build
defaults for that boot; they never erase Wi-Fi or device-key settings.

## Choose an available add-on

### GitHub Stars and new-star moments

Follow [GitHub project pulse](../../README.md#optional-github-project-pulse) to set a
public repository on the computer service. Then enable GITHUB PAGE and/or
STAR POPUP in SETTINGS → LABS → MORE and restart the panel. Turning the page
off does not turn the popup on. The old firmware macros now seed defaults;
a saved menu choice wins on subsequent boots.

The [page and popup examples](../../README.md#optional-github-project-pulse) distinguish
live, cached and missing data. Sound is a separate default-off experiment;
its physical speaker/backend gate is not complete.

### API-equivalent value

This estimates what recorded tokens would cost at list API prices, then
compares that estimate with the configured subscription cost. It is **not an
API invoice, actual spend, or a measured saving**.

Follow [Value setup and price maintenance](../value-multiple.md). The page is
optional in LABS. `UNPRICED` / `SOME MODELS ARE NOT PRICED` means more
than 2% of the counted tokens lack a known model price; refresh and verify the
price snapshot before trusting a comparison. A missing plan cost is a
different condition. Neither should prompt a board reset or firmware flash.

### Answering from the panel and using it away from home

These are independent opt-ins, not prerequisites for Labs:

- [Claude and Codex interactions](../agent-setup.md): set up the providers
  you use; adding a plugin alone does not enable them.
- [Numbers relay](../relay.md): carry quota and other supported numbers
  between separate networks.
- [Encrypted interaction and live-status relays](../interaction-relay.md):
  separate choices for questions/answers and activity.

Their guides own setup, privacy and platform verification. A Labs label does
not imply physical validation on another machine or panel.

## Ideas to try later

[Display experiments](display-experiments.md) records the reset clock,
countdown, quotes and reset moments. None is a shipped feature or a promised
release. Home Assistant remains an idea in the
[onboarding design](../superpowers/specs/2026-09-04-vibepulse-onboarding-core-and-settings-design.md).

## Where additions belong

This catalogue is the entry point. Existing firmware and host implementations
stay in place. New optional firmware components may use `components/labs_*`
and new optional host modules `tools/labs/` when they are actually needed;
there are no empty runtime packages or new plugin loader to install.

[Adding a Labs feature](adding-a-feature.md) defines the entry and delivery
checklist. The selector's physical memory and touch review is still pending.
The wider onboarding work remains described in the
[onboarding design](../superpowers/specs/2026-09-04-vibepulse-onboarding-core-and-settings-design.md).
