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

The packages form four layers: the CLI and web interfaces, the feature packages (sync, import, enrichment, tagging, jobs and the X client), storage, and core. A module imports only from its own layer or a lower one, which CI checks; the [module reference](module-reference.md#layers) lists each package's layer.

## Browser UI

The page has no JavaScript build step. `web/page.py` assembles it from the `index.html` shell, replacing each `<!-- include NAME.html -->` line with the fragment of that name, so the result is what a single file holding the same markup would be. When it serves the page, the server adds a content hash to every `/static/` reference, so browsers can cache scripts and stylesheets indefinitely and still pick up a changed file.

Scripts are classic scripts that share globals and load in the order the page lists them. `tweetApp()` in `app.js` is the page's Alpine component. It holds the state several features share and merges in one method factory per feature (`statsMethods()`, `feedMethods()`, and so on, from `features/`, `feed/` and `media/`). The factories are merged with property descriptors, so their getters stay getters, and in their methods `this` is the app.

Dialogs that own their state are their own components, mounted with `x-data` on their fragment's root: `statsDialog()`, `tagDialog()` and `setupDialog()`, the Tags and Activity logs settings pages (`nookTagsPage()`, `nookLogsPage()`) and the Jobs sync panel (`nookSyncPanel()`). They are global factory functions used in `x-data`, not `Alpine.data` registrations. They nest inside `tweetApp()`, so they read the app's state through Alpine's scope, and a write goes to whichever component declares the name; a name the dialog doesn't declare is written to the app. The app can't call into a nested component, so it signals one through state the dialog watches (`showStatsModal`) or a window event (`open-tag-dialog`, `open-lightbox`).

The tag editor captures the post ID and submitted values before saving or deleting. A successful response updates that post's loaded copies. Dialog generations keep late responses from changing a dismissed or reopened editor's content, errors or pending state.

Settings, Configuration, Schedule and automated tagging stay in the app because other features read and write their state: Setup reuses the schedule form and the Settings tab, and the automated-tagging page and the spend-history dialog share spend data. There is no `Alpine.store`: every component nests inside `tweetApp()`, so shared state is reachable through scope. A store becomes worth adding when two separate component trees need the same state.

Feed cards are rendered outside Alpine as HTML strings (`feed/render/`) and handled by delegated listeners. They are written while Alpine's observer is paused, so Alpine doesn't walk and bind every card. Cards far outside the viewport are replaced by empty placeholders of the same height and rendered again from `tweets` when the reader scrolls back (`feed/eviction.js`), so the list stays light on long scrolls. A card's DOM can therefore be thrown away at any time: state that has to survive belongs in the app (as `expandedTweets` does) or in what eviction saves and reapplies (carousel and video positions). Video playback (`media/video.js`, `media/player.js`) doesn't depend on Alpine: the app passes it the playback settings and the open-dialog flags that pause it.

Stylesheets are linked one per feature. Later ones override earlier ones, so a rule's file and the link order decide the cascade; `styles.css`, linked first after Tailwind's build, holds the theme variables, base elements and tweet cards.

## Stored and derived data

Collection memberships answer why a post belongs in the archive. Tweet objects hold the best available content, while relation rows connect replies, quotes, and reposts. Raw captures preserve observations. Normalized fields, full-text indexes, like order, and cached statistics support fast reads.

Source precedence is authenticated X, then FxTwitter, then official-archive content. A later sparse payload must not overwrite richer content; [field ownership](storage.md#field-ownership) gives the rule for each family. Unavailability is tracked independently from the saved content so a disappearance can be recorded without losing the prior capture.

Derived data should be rebuildable from saved evidence where the model supports it. A `rehydrate` rebuild and a live refresh have different purposes: the first reapplies extraction locally; the second obtains a new observation.

## Concurrency

Archive-changing work uses a process lock. The web supervisor owns at most one active worker and can queue supported jobs. The scheduler submits through the supervisor rather than starting an unrelated write path. Reads continue while jobs work through batches. Storage serializes write transactions on its shared connection, nests its own helpers through savepoints and rejects foreign transactions without changing them. Features request named facts and mutations; SQL, record keys, import row construction and batch completion stay in storage. Statistics gather requested database facts on one private read snapshot and release it before filesystem work.

Each worker publishes pipeline state and a durable run log. A successful collection pass remains recorded even if a later optional follow-up fails. Shutdown asks workers to interrupt cooperatively, gives them time to finish, and closes archive readers afterward.

## Performance

Normal feeds use indexed pagination and lightweight presentation payloads. Large count/statistics work can be requested separately. Search facts and indexes avoid repeatedly interpreting raw JSON for common filters. Warmup and cache refresh run outside the first-feed critical path.

See [storage](storage.md) and [search](search-and-web-api.md) for query behavior, and [testing](testing.md) for validation.

## Locks and security

`command.lock` covers a reported command lifecycle; `sync.lock` covers archive writer/schema/maintenance work. They are POSIX file locks with different scopes. A timeline page's raw capture, normalized graph, memberships and cursor form an atomic persistence unit. The complete multi-page job is not a single transaction.

Web tag/config edits and inline tagging previews can run alongside long jobs because they don't all acquire the process lock. Provider-budget decisions can also overlap. Multiple web processes sharing one data directory aren't a supported scheduler arrangement.

### File publication

`core/fileio.py` owns same-directory temporary files and atomic replacement. Streaming writers use `owned_temp` and `publish_file`; byte, text and JSON publishers use the atomic-write helpers. Cleanup covers interrupts, failed close and failed replacement without hiding the initiating error. Status snapshots and derived-cache callers retain their best-effort error policies.

## Trust and security

TweetNook assumes a trusted local administrator and trusted archive inputs. The built-in server serves HTTP and uses a shared password, with no per-user permissions. CSRF and origin checks are incomplete. Stored URLs can contact arbitrary hosts, and remote fetches have no general response-size budget. Use a trusted network, or put remote access behind a VPN or HTTPS reverse proxy.

Filesystem checks depend on the operation. `/media` checks resolved paths for traversal and symlink escapes. Imports, downloads and tagging inputs do not all enforce that containment; extracted imports need trusted, symlink-free files.

Static assets and default FastAPI schema endpoints are public. Archive routes require the configured password when one is set. API secret masking and log-argument filtering cover selected fields; configuration, captures, logs and AI-usage records can still contain private data. Configuration and owned temporary files are private (`0600`); other file permissions outside installer-managed directories follow the process umask. Keep real account data and credentials out of published fixtures.

There is no active vector/embedding search, browser-profile login adapter, standalone HTML export or JSON restore importer. Legacy compatibility names do not enable these features.
