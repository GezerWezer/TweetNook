# Architecture

The CLI and web server use the same archive operations. The web app starts workers for long-running commands, while ordinary read requests query the local archive. Saved source responses let the application revisit extraction and presentation without fetching everything again.

## Data flow

1. Configuration resolves paths, credentials, and feature settings.
2. The client builds timeline or detail requests and handles pacing, authentication, query IDs, and remote errors.
3. Extractors turn responses into tweet objects, membership observations, relations, media, URLs, and Articles.
4. Storage merges those records and keeps raw captures with their provenance.
5. Follow-up jobs fill conversations, media, previews, and optional tags.
6. Search and web presentation read the resulting local records.

An official archive enters at a separate import boundary and joins the same storage model. FxTwitter supplies public detail lookups with a lower source rank than an authenticated X response. Neither an import nor a restored quote should silently create collection membership.

## Boundaries

The [module reference](module-reference.md) maps operations to their owning modules. CLI and web handlers validate inputs and present results; archive operations share the transport, extraction and storage layers.

## Stored and derived data

Collection memberships answer why a post belongs in the archive. Tweet objects hold the best available content, while relation rows connect replies, quotes, and reposts. Raw captures preserve observations. Normalized fields, full-text indexes, like order, and cached statistics support fast reads.

Source precedence is authenticated X, then FxTwitter, then official-archive content. A later sparse payload must not overwrite richer content. Unavailability is tracked independently from the saved content so a disappearance can be recorded without losing the prior capture.

Derived data should be rebuildable from saved evidence where the model supports it. A `rehydrate` rebuild and a live refresh have different purposes: the first reapplies extraction locally; the second obtains a new observation.

## Concurrency

Archive-changing work uses a process lock. The web supervisor owns at most one active worker and can queue supported jobs. The scheduler submits through the supervisor rather than starting an unrelated write path. Reads continue while jobs work through batches.

Each worker publishes pipeline state and a durable run log. A successful collection pass remains recorded even if a later optional follow-up fails. Shutdown asks workers to interrupt cooperatively, gives them time to finish, and closes archive readers afterward.

## Performance

Normal feeds use indexed pagination and lightweight presentation payloads. Large count/statistics work can be requested separately. Search facts and indexes avoid repeatedly interpreting raw JSON for common filters. Warmup and cache refresh run outside the first-feed critical path.

See [storage](storage.md) and [search](search-and-web-api.md) for query behavior, and [testing](testing.md) for validation.

## Locks and security

`command.lock` covers a reported command lifecycle; `sync.lock` covers archive writer/schema/maintenance work. They are POSIX file locks with different scopes. A timeline page's raw capture, normalized graph, memberships and cursor form an atomic persistence unit. The complete multi-page job is not a single transaction.

Web tag/config edits and inline tagging previews can run alongside long jobs because they don't all acquire the process lock. Provider-budget decisions can also overlap. Multiple web processes sharing one data directory aren't a supported scheduler arrangement.

### Trust and security

TweetNook assumes a trusted local administrator and trusted archive inputs. The built-in server serves HTTP and uses a shared password, with no per-user permissions. CSRF and origin checks are incomplete. Stored URLs can contact arbitrary hosts, and remote fetches have no general response-size budget. Use a trusted network, or put remote access behind a VPN or HTTPS reverse proxy.

Filesystem checks depend on the operation. `/media` checks resolved paths for traversal and symlink escapes. Imports, downloads and tagging inputs do not all enforce that containment; extracted imports need trusted, symlink-free files.

Static assets and default FastAPI schema endpoints are public. Archive routes require the configured password when one is set. API secret masking and log-argument filtering cover selected fields; configuration, captures, logs and AI-usage records can still contain private data. File permissions outside installer-managed directories follow the process umask. Keep real account data and credentials out of published fixtures.

There is no active vector/embedding search, browser-profile login adapter, standalone HTML export or JSON restore importer. Legacy compatibility names do not enable these features.
