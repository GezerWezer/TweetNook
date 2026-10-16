#!/usr/bin/env python3
"""Tag only liked demo posts with Gemini's asynchronous, Search-enabled Batch API.

The API key is read from GEMINI_API_KEY in memory. Provider payloads and uploaded
file references stay in ignored data/demo-content/tagging. This does not mutate
the public fixture, the private archive, or the user's saved configuration.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from tweetnook.tagging import MediaTagResult, TextTagResult
from tweetnook.tagging_prompts import build_media_system_prompt, build_text_system_prompt

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data/demo-content"
WORK = PRIVATE / "tagging"
MODEL = "gemini-3.6-flash"
API = "https://generativelanguage.googleapis.com/v1beta/"


def now() -> str:
    return datetime.now(UTC).isoformat()


def save(name: str, value: object) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def load(name: str) -> dict:
    return json.loads((WORK / name).read_text())


def api(path: str, body=None, *, method: str | None = None, headers=None, raw=False):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY must be supplied in the environment")
    request_headers = {"x-goog-api-key": key, **(headers or {})}
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        path if path.startswith("https://") else API + path,
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = response.read()
            return (dict(response.headers), result) if raw else json.loads(result or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace").replace(key, "[REDACTED]")
        raise RuntimeError(f"Provider HTTP {error.code}: {detail}") from None


def prepare() -> None:
    if (WORK / "job.json").exists():
        raise RuntimeError("Existing batch found; preserve its inputs before preparing another run")
    fixture = json.loads((ROOT / "demo/data.json").read_text())
    posts = {row["tweet_id"]: row for row in fixture["tweets"]}
    authors = {row["id"]: row for row in fixture["authors"]}
    liked = [row for row in posts.values() if "like" in row.get("collections", [])]
    existing = [
        tag
        for tag, _ in Counter(
            tag for row in posts.values() for tag in row.get("tags", [])
        ).most_common(50)
    ]
    requests = []
    with sqlite3.connect(PRIVATE / "archive.db") as connection:
        connection.row_factory = sqlite3.Row
        for post in liked:
            quoted = posts.get(post.get("quoted_tweet_id"))
            content_ids = [post["tweet_id"]] + ([quoted["tweet_id"]] if quoted else [])
            placeholders = ",".join("?" for _ in content_ids)
            media = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT tweet_id,media_type,local_path FROM archive
                    WHERE record_type='media' AND tweet_id IN ({placeholders})
                    AND COALESCE(source,'')!='link_card' ORDER BY tweet_id,local_path""",
                    content_ids,
                )
                if row["local_path"]
            ]
            author = authors[post["author_id"]]
            kind = "Quote Tweet" if quoted else "Reply" if post.get("reply_to_id") else "Standalone"
            text = (
                f"Type: {kind}\n"
                f"Author: {author['display_name']} (@{author['username']})\n"
                f"Text: {json.dumps(post['text'])}\n"
            )
            if quoted:
                qa = authors[quoted["author_id"]]
                text += (
                    "Quoted Tweet Context:\n"
                    f"Author: {qa['display_name']} (@{qa['username']})\n"
                    f"Text: {json.dumps(quoted['text'])}\n"
                )
            supplemental = []
            for source in [post] + ([quoted] if quoted else []):
                if article := source.get("article"):
                    text += (
                        "Article Content:\n"
                        f"Title: {article.get('title', '')}\n"
                        f"Body: {article.get('content_text', '')}\n"
                    )
                    supplemental.append(source["tweet_id"])
            builder = build_media_system_prompt if media else build_text_system_prompt
            schema = MediaTagResult if media else TextTagResult
            prompt = builder(existing_tags=existing, google_search=True)
            for item in media:
                path = PRIVATE / item["local_path"]
                if not path.is_file():
                    raise RuntimeError(f"Missing local media for {post['tweet_id']}")
                item["mime_type"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            requests.append(
                {
                    "tweet_id": post["tweet_id"],
                    "content_type": "media" if media else "text",
                    "text": text,
                    "system_prompt": prompt,
                    "schema": schema.model_json_schema(),
                    "media": media,
                    "supplemental_article_context_ids": supplemental,
                }
            )
    assert len(requests) == len(liked)
    assert all("like" in posts[row["tweet_id"]]["collections"] for row in requests)
    save(
        "manifest.json",
        {
            "model": MODEL,
            "created_at": now(),
            "google_search": True,
            "provider_batch": True,
            "prompt_source": "tweetnook.tagging_prompts",
            "existing_tags": existing,
            "requests": requests,
        },
    )
    print(
        json.dumps(
            {
                "prepared_likes": len(requests),
                "media_inputs": sum(len(row["media"]) for row in requests),
                "article_context_posts": sum(
                    len(row["supplemental_article_context_ids"]) for row in requests
                ),
            }
        ),
        flush=True,
    )


def upload(path: Path, mime: str) -> dict:
    headers, _ = api(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        {"file": {"display_name": path.name}},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size),
            "X-Goog-Upload-Header-Content-Type": mime,
        },
        raw=True,
    )
    url = next(value for name, value in headers.items() if name.lower() == "x-goog-upload-url")
    result = api(
        url,
        path.read_bytes(),
        headers={
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
            "Content-Type": mime,
        },
    )["file"]
    for _ in range(120):
        if result.get("state") == "ACTIVE":
            return result
        if result.get("state") == "FAILED":
            raise RuntimeError("Gemini file processing failed")
        time.sleep(5)
        result = api(result["name"])
    raise RuntimeError("Gemini file processing timeout; no batch submitted")


def submit() -> None:
    if (WORK / "job.json").exists():
        raise RuntimeError("Existing job metadata found; refusing a duplicate billable submission")
    manifest = load("manifest.json")
    model = api("models/" + MODEL)
    if "batchGenerateContent" not in model.get("supportedGenerationMethods", []):
        raise RuntimeError("Requested model does not advertise Batch API")
    uploaded = load("uploaded.json") if (WORK / "uploaded.json").exists() else {}
    requests = []
    for item in manifest["requests"]:
        parts = [{"text": item["text"]}]
        for media in item["media"]:
            local = media["local_path"]
            if local not in uploaded:
                uploaded[local] = upload(PRIVATE / local, media["mime_type"])
                save("uploaded.json", uploaded)
            remote = uploaded[local]
            label = (
                "Direct Tweet Media"
                if media["tweet_id"] == item["tweet_id"]
                else "Quoted Tweet Media"
            )
            parts.extend(
                [
                    {"text": f"\n{label}:\n"},
                    {"fileData": {"fileUri": remote["uri"], "mimeType": remote["mimeType"]}},
                ]
            )
        requests.append(
            {
                "metadata": {"key": item["tweet_id"]},
                "request": {
                    "contents": [{"role": "user", "parts": parts}],
                    "systemInstruction": {"parts": [{"text": item["system_prompt"]}]},
                    "tools": [{"googleSearch": {}}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseSchema": item["schema"],
                        "maxOutputTokens": 8192,
                        "thinkingConfig": {"thinkingLevel": "LOW"},
                    },
                },
            }
        )
        print(f"Prepared provider item {item['tweet_id']} ({len(item['media'])} media)", flush=True)
    body = {
        "batch": {
            "display_name": "tweetnook-demo-liked-tags",
            "input_config": {"requests": {"requests": requests}},
        }
    }
    save("batch-request.json", body)
    job = api(f"models/{MODEL}:batchGenerateContent", body)
    save("job.json", job)
    print(
        json.dumps(
            {
                "name": job.get("name"),
                "state": job.get("metadata", {}).get("state"),
                "done": job.get("done", False),
            }
        ),
        flush=True,
    )


def status(collect: bool, output: Path | None) -> None:
    saved = load("job.json")
    job = api(saved["name"])
    save("job-status.json", job)
    metadata = job.get("metadata", {})
    state = metadata.get("state")
    print(
        json.dumps(
            {
                "name": job.get("name"),
                "state": state,
                "done": job.get("done", False),
                "createTime": metadata.get("createTime"),
                "endTime": metadata.get("endTime"),
                "batchStats": metadata.get("batchStats"),
                "error": job.get("error"),
            }
        ),
        flush=True,
    )
    if not collect or not job.get("done"):
        return
    if job.get("error"):
        raise RuntimeError("Batch completed with an error; see private job status")
    responses = job.get("response", {}).get("inlinedResponses", [])
    if isinstance(responses, dict):
        responses = responses.get("inlinedResponses", [])
    if not responses:
        # API uses batch metadata.output for inline responses in some versions.
        responses = metadata.get("output", {}).get("inlinedResponses", [])
        if isinstance(responses, dict):
            responses = responses.get("inlinedResponses", [])
    results = {}
    validation_errors = {}
    for row in responses:
        tweet_id = row.get("metadata", {}).get("key")
        response = row.get("response")
        if not response:
            raise RuntimeError(f"Missing successful response for {tweet_id}")
        text = "".join(
            part.get("text", "")
            for candidate in response.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
            if not part.get("thought")
        )
        source = next(
            item for item in load("manifest.json")["requests"] if item["tweet_id"] == tweet_id
        )
        schema = MediaTagResult if source["content_type"] == "media" else TextTagResult
        try:
            parsed_json = json.loads(text)
            # Accept lossless formatting drift without inventing or changing tags.
            if isinstance(parsed_json, list):
                if source["content_type"] == "text" and all(
                    isinstance(tag, str) for tag in parsed_json
                ):
                    parsed_json = {"tags": parsed_json}
                elif len(parsed_json) == 1 and isinstance(parsed_json[0], dict):
                    parsed_json = parsed_json[0]
            parsed = schema.model_validate(parsed_json).model_dump()
            if len({tag.casefold() for tag in parsed["tags"]}) != len(parsed["tags"]):
                raise ValueError("Duplicate generated tags")
        except (ValueError, TypeError) as error:
            validation_errors[tweet_id] = str(error)
            continue
        results[tweet_id] = {
            **parsed,
            "content_type": source["content_type"],
            "model": MODEL,
            "model_version": response.get("modelVersion"),
            "usage": response.get("usageMetadata", {}),
            "search_queries": [
                query
                for candidate in response.get("candidates", [])
                for query in candidate.get("groundingMetadata", {}).get("webSearchQueries", [])
            ],
        }
    expected = {item["tweet_id"] for item in load("manifest.json")["requests"]}
    if set(results) != expected:
        save("partial-results.json", {"results": results, "validation_errors": validation_errors})
        raise RuntimeError(
            f"Expected {len(expected)} liked outputs, got {len(results)}; "
            "inspect private job status"
        )
    result = {
        "model": MODEL,
        "provider_batch": True,
        "google_search_enabled": True,
        "completed_at": now(),
        "results": results,
    }
    save("validated-results.json", result)
    if output:
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "validated_liked_results": len(results),
                "search_queries": sum(len(row["search_queries"]) for row in results.values()),
            }
        ),
        flush=True,
    )


def cleanup() -> None:
    if not (WORK / "validated-results.json").exists():
        raise RuntimeError("Refusing to remove uploaded media before validated results exist")
    for remote in load("uploaded.json").values():
        api(remote["name"], method="DELETE")
    save("cleanup.json", {"completed_at": now(), "files_deleted": len(load("uploaded.json"))})
    print("Provider media files deleted", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["prepare", "submit", "status", "collect", "cleanup"],
        help="Prepare, submit a paid batch, inspect, collect results, or remove uploads.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write sanitized results here during collect; default is private output only.",
    )
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare()
        elif args.action == "submit":
            submit()
        elif args.action == "cleanup":
            cleanup()
        else:
            status(args.action == "collect", args.output)
    except Exception as error:
        key = os.environ.get("GEMINI_API_KEY", "")
        message = str(error).replace(key, "[REDACTED]") if key else str(error)
        print(message, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
