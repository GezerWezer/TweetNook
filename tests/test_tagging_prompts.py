from tweetnook.tagging_prompts import build_media_system_prompt, build_text_system_prompt


def test_optional_prompt_fragments_are_omitted_entirely_when_empty() -> None:
    for prompt in (build_media_system_prompt(), build_text_system_prompt()):
        assert "**Existing Tags:**" not in prompt
        assert "**Tagging Context:**" not in prompt
        assert "Tagging context:" not in prompt
        assert "**Additional User Instructions:**" not in prompt
        assert "**Search and Verification:**" not in prompt
        assert "**Tweet Isolation:**" not in prompt
        assert "{tagging_context}" not in prompt
        assert "{additional_instructions}" not in prompt


def test_existing_tags_are_direct_non_exhaustive_context() -> None:
    prompt = build_text_system_prompt(existing_tags=["Deadlock", "Ivy (Deadlock)"])
    assert "**Existing Tags:**" in prompt
    assert "Existing tags:\n- Deadlock\n- Ivy (Deadlock)" in prompt
    assert "not exhaustive" in prompt
    assert "Create a new specific tag" in prompt


def test_context_is_weak_non_anchoring_and_only_present_when_configured() -> None:
    for prompt in (
        build_media_system_prompt(tagging_context=["Deadlock", "Hayao Miyazaki"]),
        build_text_system_prompt(tagging_context=["Deadlock", "Hayao Miyazaki"]),
    ):
        assert "**Tagging Context:**" in prompt
        assert "Tagging context:\n- Deadlock\n- Hayao Miyazaki" in prompt
        assert "weak, non-exhaustive hints" in prompt
        assert "freely identify subjects that are not listed" in prompt


def test_additional_instructions_are_lower_priority_and_only_present_when_nonempty() -> None:
    prompt = build_media_system_prompt(
        additional_instructions="Prefer original-language film titles."
    )
    assert "**Additional User Instructions:**" in prompt
    assert "Prefer original-language film titles." in prompt
    assert "must not override factual evidence" in prompt
    assert "required output format" in prompt
    assert "tag limit" in prompt
    assert "Additional User Instructions" not in build_media_system_prompt(
        additional_instructions=" \n "
    )


def test_paid_search_and_free_batch_isolation_are_independent_fragments() -> None:
    paid = build_text_system_prompt(google_search=True)
    free_batch = build_text_system_prompt(tweet_isolation=True)
    assert "**Search and Verification:**" in paid
    assert "**Tweet Isolation:**" not in paid
    assert "**Tweet Isolation:**" in free_batch
    assert "**Search and Verification:**" not in free_batch


def test_media_and_text_keep_distinct_core_rules_and_finishers() -> None:
    media = build_media_system_prompt()
    text = build_text_system_prompt()
    assert "**Description:**" in media
    assert "thoroughly\ntranscribe" in media
    assert "Do not\nrepeat, quote, or summarize the tweet's own text" in media
    assert "visibly present within the media" in media
    assert "Do not create descriptions or summaries" in text
    assert "**Description:**" not in text
    assert "descriptions and tags" in media
    assert "tags are clear" in text
