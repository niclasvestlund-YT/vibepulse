# Vibe Labs

Optional ways to make VibePulse your own. Start with Claude/Codex usage;
add the displays and integrations you want when you need them.

**This is a feature catalogue, not a package installer.** Available features
use the setup paths linked below. The on-screen FEATURES selector and a
leaner first-install profile are still planned. Labs is optional; each entry
has its own delivery and verification status.

## What you get when you install today

A fresh checkout builds one app: VibePulse. Its page rotation includes three
quota pages, burn rate, two Max Tracker pages, and the Value page. Missing
provider data stays unavailable; it is never turned into a made-up zero.
Value needs priced usage, and its comparison also needs a plan cost.

Local agent-status and Needs You display support are present; useful activity
depends on the provider and setup. Answering on the panel is a separate
opt-in. Cloud relays, GitHub pages/popups and sound are off by default.
SETTINGS currently offers UPDATE, WIFI and ABOUT.

Use the [setup guide](../agent-setup.md) for the installation and the
[independent switches](../../README.md#independent-switches) for interaction
and cloud choices. You do not need GitHub configuration, plan costs, a relay
account or any Labs experiment to start using quotas.

## The simpler installation we are working toward

The intended base is Claude/Codex quota pages with reset information, local
activity when available, and device settings. Additional analytics, fun
displays and integrations belong in Labs and start off for a fresh install.
Existing users keep their choices when this profile is implemented.

| Feature | Available today | Fresh install today | Intended Labs choice |
|---|---|---|---|
| Burn rate | Built; uses quota history | Page included | Optional analytics |
| Max Tracker | Built; needs usage history and plan identity for relevant labels | Two pages included | Optional analytics |
| API-equivalent value | Built; needs model prices, plus plan cost for the multiple | Page included; can show missing-price or missing-plan states | Optional analytics |
| GitHub Stars / forks | Built | Off | Repository page |
| New-star popup | Built, independent of the GitHub page | Off | Event popup |
| Reset clock / countdown | Concept | Not available as a standalone view | Ambient display |
| Coding quotes | Idea; initial copy drafted | Not available | Ambient display |
| Reset celebration | Idea | Not available | Event popup |

**Included today is not the intended default.** Burn rate, Max Tracker and
Value do not yet have user-facing off switches. Creating this catalogue does
not hide those pages, install an experiment, or change an existing device.

## Choose an available add-on

### GitHub Stars and new-star moments

Follow [GitHub project pulse](../../README.md#optional-github-project-pulse) to set a
public repository on the computer service. The firmware switches
`TK_GITHUB_SCREEN_ENABLED` and `TK_GITHUB_NOTIFICATIONS_ENABLED` are separate
and default to `0`. They currently require a firmware build; they are not
runtime menu switches. Turning the page off does not turn the popup on.

The [page and popup examples](../../README.md#optional-github-project-pulse) distinguish
live, cached and missing data. Sound is a separate default-off experiment;
its physical speaker/backend gate is not complete.

### API-equivalent value

This estimates what recorded tokens would cost at list API prices, then
compares that estimate with the configured subscription cost. It is **not an
API invoice, actual spend, or a measured saving**.

Follow [Value setup and price maintenance](../value-multiple.md). The page is
already included today. `UNPRICED` / `SOME MODELS ARE NOT PRICED` means more
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
checklist. Next delivery steps are the runtime feature switches, measurements
and first-install defaults described in the
[onboarding design](../superpowers/specs/2026-09-04-vibepulse-onboarding-core-and-settings-design.md).
