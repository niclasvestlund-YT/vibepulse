## Outcome

<!-- What changes for the user? -->

## Root cause / rationale

<!-- Why was this needed? -->

## Verification

- [ ] Relevant focused tests pass.
- [ ] New tests are registered in the host runner and relevant CI steps;
      optional-feature changes cover enabled and disabled paths where relevant.
- [ ] `./test/run.sh` passes, or the exact omission and reason are stated.
- [ ] No secret, credential, raw session content, production payload, or
      `torget.bin` is included.
- [ ] Documentation and changelog match the behavior.

### Platform claims

- [ ] This does not change a platform support claim; or
- [ ] the support matrix and release validation evidence were updated.
- [ ] A green CI runner is described only as automated evidence, not as a
      real-host or physical-panel pass.
- [ ] Windows-affecting changes follow `docs/windows-validation.md`.
- [ ] Linux is not called supported before issue #2 and every real-host/panel
      gate pass.

### Hardware / UI

- [ ] No hardware or UI behavior changed; or
- [ ] exact 480×480 simulator evidence is attached.
- [ ] A physical flash was separately and explicitly authorized.
- [ ] Silence, timeout, missing panel, **LEAVE IT**, and computer fallback were
      not treated as approval.

## Not tested / remaining risk

<!-- Be explicit. “None” is acceptable only after checking. -->

<!-- A documented local omission does not waive required GitHub checks.
     See CONTRIBUTING.md for the review and merge process. -->
