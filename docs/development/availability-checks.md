# Availability checks

`check.py` checks public availability through FxTwitter and uses bounded authenticated detail lookups for cases that need the user's account. `resurrection.py` owns the reason-aware X retry policy. The web presentation reads durable availability state alongside the last saved content.

## Public lookups

`client/fxtwitter.py` builds a cookie-free HTTP client against `fxtwitter.base_url`. It normalizes successful responses into the common graph shape, including nested quotes and Community Note information. Preserve the FxTwitter payload as source evidence and assign it the FxTwitter source rank.

An authenticated X capture outranks FxTwitter; FxTwitter can fill an archive placeholder. Availability observations and richer canonical content are separate, so a successful public check needn't replace an already complete X body. Disabling FxTwitter disables `check`; authenticated restoration can then remain part of sync.

## Check scope

The job visits saved memberships and eligible unavailable objects on each run, then a rolling share of context when `check.context_enabled` is true. Saved posts' embedded quotes are processed either way and count toward checked context, so context selection happens after the saved pass. Batch size is approximately the context count times the cadence interval (1/7/31 days), divided by `check.context_days`, bounded by the available context. Deduplicate IDs across scopes.

Context checks are off by default, with a 60-day target cycle when enabled. The authenticated detail budget is 200. `--skip-saves`, `--skip-context`, `--skip-unavailable`, and `--detail-budget` change one invocation. These scope switches apply to public checks. Authenticated restoration runs separately against due unavailable posts; a zero detail budget disables it without disabling public checks.

## Results

Public lookups distinguish available, not found, protected, explicit tombstone, and transport/error outcomes. A generic 404 needs repeated evidence before an available post is marked unavailable. The run also watches for a broad 404 outage; a broken provider response must not mark an entire archive missing.

Protected-account responses can need authenticated confirmation. Transport failures remain failures. Record `suspect`/to-recheck states when evidence is insufficient, and keep detailed counters tied to the final outcome after provisional 404s settle.

## Availability state

`terminal_unavailable` has a reason and retry policy; it doesn't mean every reason is permanent. Available results can turn a sparse object into `done` or a formerly unavailable object into `resurrected`. The graph and memberships remain consistent across those transitions.

`web/availability.py` shows saved text, media, and Articles with a missing marker for every unavailable reason, including author/archive deletion. Load separate media and Article records before deciding whether a saved copy exists. Use a placeholder only when there is no saved content; a relation-only target with no saved copy can be `not_archived`. Don't infer authoritative state from display text when durable object state exists.

For unavailable posts, saved canonical content takes precedence over stale embedded tombstones. Retain an embedded body when the canonical row has no saved body. These rules apply to quoted and reposted targets as well as the focal post.

`is:missing` selects objects in `terminal_unavailable`; `is:resurrected` selects restored objects. Keep those search semantics and web annotations in agreement.

## Authenticated retries

| Reason | Normal retry interval |
| --- | --- |
| `protected_account` | 7 days |
| `suspended_account` | 30 days |
| `account_missing` | 90 days |
| `withheld` | 90 days |
| `not_found` | 180 days |
| `unavailable_unknown` | 7, 30, 90, then 180 days |
| `archive_deleted`, `deleted_by_author` | No automatic restoration retry |

Transport retry delays are 1, 6, 24, then 72 hours. Account probes can prioritize related unavailable posts when evidence suggests an account returned; all work stays within bounded policy. These intervals are local retry choices, not claims about X retention.

## History

Store timestamped checks and observed Community Note changes. A first note check compares against the previously saved note, which lets removed/replaced notes be recorded. History endpoints expose observations, not a reconstructed complete edit history.

See [testing](testing.md) for the availability and history suites.
