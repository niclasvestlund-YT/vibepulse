# Adding a Vibe Labs feature

Add a catalogue entry before advertising an optional feature as available.
Keep the feature's real implementation and verification status visible.

Each entry states:

| Field | What to record |
|---|---|
| Purpose | The one useful thing the user sees or does |
| Status | Idea, concept, implemented with evidence limits, or verified on named hardware/revision |
| Default | What a fresh install does today; intended default separately when different |
| Enable and disable | Existing setup path, dependencies and whether rebuild/restart is required |
| Data | Source, freshness, missing-data behaviour, network and privacy needs |
| Display | Passive page or interrupting event; dismissal and priority |
| Evidence | Relevant tests, native captures and exact physical review, when available |

An experiment is not enabled by installing VibePulse. New optional features
should start off; preserve existing user choices. A page and a popup are
separate choices. Optional failure must not block quota updates or questions.

Keep existing code in place. Introduce `components/labs_<feature>` or
`tools/labs/<feature>` only when the feature needs its own module; reuse the
app's existing interfaces and source ownership. Current display choices live
in `components/app_tokens/labs_features.*` and the five-choice SETTINGS binding;
extend the versioned NVS migration and all-mask tests when adding a choice.
Do not build a package loader
or framework ahead of a concrete requirement.

When implementation changes defaults or activation, update this catalogue,
the feature guide and the setup/README instructions in the same change. The
feature must have a working disable path before it can be described as a
user-selectable add-on. Follow repository PR, test and physical-review rules.

Return to the [Labs catalogue](README.md).
