-- Schema created by tweetnook v0.1.0; regenerate with
-- scripts/dump_release_schema.py v0.1.0. Do not edit by hand.
CREATE TABLE archive (row_key TEXT PRIMARY KEY, record_type TEXT, tweet_id TEXT, collection_type TEXT, folder_id TEXT, sort_index TEXT, operation TEXT, cursor_in TEXT, cursor_out TEXT, captured_at TEXT, http_status TEXT, source TEXT, text TEXT, author_id TEXT, author_username TEXT, author_display_name TEXT, created_at TEXT, created_at_ts INTEGER, deleted_at TEXT, conversation_id TEXT, lang TEXT, note_tweet_text TEXT, enrichment_state TEXT, enrichment_checked_at TEXT, enrichment_http_status TEXT, enrichment_reason TEXT, enrichment_detail TEXT, enrichment_retry_count INTEGER, enrichment_next_retry_at TEXT, enrichment_first_unavailable_at TEXT, enrichment_retry_eligible INTEGER, raw_json TEXT, first_seen_at TEXT, last_seen_at TEXT, added_at TEXT, synced_at TEXT, relation_type TEXT, target_tweet_id TEXT, position TEXT, media_key TEXT, media_type TEXT, media_url TEXT, thumbnail_url TEXT, width TEXT, height TEXT, duration_millis TEXT, variants_json TEXT, download_state TEXT, local_path TEXT, provenance_source TEXT, sha256 TEXT, byte_size TEXT, content_type TEXT, thumbnail_local_path TEXT, thumbnail_sha256 TEXT, thumbnail_byte_size TEXT, thumbnail_content_type TEXT, downloaded_at TEXT, download_error TEXT, url_hash TEXT, url TEXT, expanded_url TEXT, final_url TEXT, canonical_url TEXT, display_url TEXT, url_host TEXT, description TEXT, site_name TEXT, unfurl_state TEXT, last_fetched_at TEXT, article_id TEXT, title TEXT, summary_text TEXT, content_text TEXT, published_at TEXT, status TEXT, archive_digest TEXT, archive_generation_date TEXT, import_started_at TEXT, import_completed_at TEXT, warnings_json TEXT, counts_json TEXT, enrichment_followup_status TEXT, enrichment_aborted_reason TEXT, last_head_tweet_id TEXT, backfill_cursor TEXT, backfill_incomplete TEXT, updated_at TEXT, key TEXT, value TEXT);
CREATE VIRTUAL TABLE archive_fts USING fts5(
                author_username, author_display_name, text, note_tweet_text,
                content=''
            );
CREATE TRIGGER archive_ad AFTER DELETE ON archive
        WHEN old.record_type = 'tweet' BEGIN
          INSERT INTO archive_fts(
            archive_fts, rowid, author_username, author_display_name, text, note_tweet_text
          ) VALUES(
            'delete', old.rowid, old.author_username, old.author_display_name,
            old.text, old.note_tweet_text
          );
        END;
CREATE TRIGGER archive_ai AFTER INSERT ON archive
        WHEN new.record_type = 'tweet' BEGIN
          INSERT INTO archive_fts(
            rowid, author_username, author_display_name, text, note_tweet_text
          ) VALUES(
            new.rowid, new.author_username, new.author_display_name,
            new.text, new.note_tweet_text
          );
        END;
CREATE TRIGGER archive_au AFTER UPDATE ON archive BEGIN
          INSERT INTO archive_fts(
            archive_fts, rowid, author_username, author_display_name, text, note_tweet_text
          ) SELECT
            'delete', old.rowid, old.author_username, old.author_display_name,
            old.text, old.note_tweet_text
          WHERE old.record_type = 'tweet';
          INSERT INTO archive_fts(
            rowid, author_username, author_display_name, text, note_tweet_text
          ) SELECT
            new.rowid, new.author_username, new.author_display_name,
            new.text, new.note_tweet_text
          WHERE new.record_type = 'tweet';
        END;
CREATE INDEX idx_archive_tweet_id ON archive(tweet_id);
CREATE INDEX idx_archive_target_tweet_id ON archive(target_tweet_id);
CREATE INDEX idx_archive_sort ON archive(collection_type, created_at_ts DESC, CAST(sort_index AS INTEGER) DESC);
CREATE INDEX idx_archive_record_sort ON archive(record_type, collection_type, created_at_ts DESC, tweet_id DESC);
CREATE INDEX idx_archive_record_page ON archive(record_type, created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC);
CREATE INDEX idx_archive_record_collection_page ON archive(record_type, collection_type, created_at_ts DESC, CAST(sort_index AS INTEGER) DESC, tweet_id DESC);
CREATE INDEX idx_archive_enrichment_due ON archive(record_type, enrichment_state, enrichment_retry_eligible, enrichment_next_retry_at);
CREATE INDEX idx_archive_dead_author ON archive(record_type, enrichment_state, author_id, enrichment_retry_eligible, enrichment_next_retry_at);
CREATE INDEX idx_archive_resurrection_reason_due ON archive(record_type, enrichment_state, enrichment_retry_eligible, enrichment_reason, enrichment_next_retry_at);
CREATE INDEX idx_archive_capture_target ON archive(record_type, cursor_in, captured_at DESC);
CREATE INDEX idx_archive_search_attachment
            ON archive(record_type, tweet_id, media_type)
            WHERE record_type IN ('media', 'url_ref')
        ;
CREATE INDEX idx_archive_media_tag_lookup
            ON archive(tweet_id, raw_json)
            WHERE record_type = 'media_tag'
        ;
PRAGMA user_version = 5;
