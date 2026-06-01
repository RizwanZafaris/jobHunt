"""tests/test_persona_apify_parse.py — Apify rag-web-browser nested parse.

Regression guard for the bug where ``gather_research`` parsed the Apify
``rag-web-browser`` dataset items by reading DOTTED strings as flat dict
keys::

    url=item.get("metadata.url")        # ALWAYS None
    title=item.get("metadata.title")    # ALWAYS None

The actor actually returns NESTED objects::

    {"markdown": "...",
     "metadata": {"url": "...", "title": "..."},
     "searchResult": {"url": "...", "title": "..."}}

so the dotted-key lookups silently stripped every source's provenance
url/title (markdown still came through, so source *count* was unaffected,
but citations and the news digest were degraded).

This test exercises the REAL parsing path inside ``gather_research`` by
stubbing the network layer (``_fetch_apify``) and settings, then asserts
the url/title are extracted from the nested dicts (not empty). It also
covers the ``searchResult`` fallback and the all-missing case.
"""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def mod(monkeypatch):
    """Import the module under test with env stubbed (mirrors
    tests/test_admin_rebuild_personas.py's env-stub pattern)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setenv("APIFY_TOKEN", "test-apify-token")
    import config.settings as _cs
    _cs._settings = None
    import agents.persona_deep_research as _m
    # Make sure a token is present regardless of how Settings loads env,
    # so gather_research does not short-circuit to [].
    monkeypatch.setattr(
        _m, "get_settings",
        lambda: SimpleNamespace(apify_token="test-apify-token"),
    )
    return _m


def _run_gather(mod, items):
    """Drive the real gather_research with _fetch_apify stubbed to return
    `items` for the single news-only query."""
    async def _fake_fetch(token, query, max_results=3):
        return items

    # Patch the network call; the parsing logic under test stays real.
    import agents.persona_deep_research as _m
    _orig = _m._fetch_apify
    _m._fetch_apify = _fake_fetch
    try:
        return asyncio.run(
            mod.gather_research(
                "Adyen",
                max_per_query=3,
                queries=mod.build_news_only_queries("Adyen"),
            )
        )
    finally:
        _m._fetch_apify = _orig


def test_nested_metadata_url_and_title_extracted(mod):
    """The representative nested Apify item must yield a populated
    url AND title — the core regression."""
    item = {
        "markdown": "# Adyen raises a round\nSome body text.",
        "metadata": {
            "url": "https://example.com/adyen-news",
            "title": "Adyen raises a round",
        },
        "searchResult": {
            "url": "https://search.example.com/adyen",
            "title": "Adyen — search snippet title",
        },
    }
    sources = _run_gather(mod, [item])
    assert len(sources) == 1
    src = sources[0]
    # metadata.* must win and must NOT be empty (the bug made both "").
    assert src.url == "https://example.com/adyen-news"
    assert src.title == "Adyen raises a round"
    assert src.url != ""
    assert src.title != ""


def test_falls_back_to_searchresult_when_metadata_missing(mod):
    """When metadata has no url/title, searchResult.* is used."""
    item = {
        "markdown": "body",
        "metadata": {},  # present but empty
        "searchResult": {
            "url": "https://search.example.com/x",
            "title": "Search title",
        },
    }
    sources = _run_gather(mod, [item])
    assert len(sources) == 1
    assert sources[0].url == "https://search.example.com/x"
    assert sources[0].title == "Search title"


def test_missing_both_yields_empty_strings_not_error(mod):
    """No metadata/searchResult at all → empty url/title, but the source
    is still collected from markdown (no crash)."""
    item = {"markdown": "body only"}
    sources = _run_gather(mod, [item])
    assert len(sources) == 1
    assert sources[0].url == ""
    assert sources[0].title == ""
    assert sources[0].content  # markdown still captured


def test_source_file_has_no_dotted_key_antipattern():
    """Belt-and-suspenders: the buggy dotted-key lookups must be gone and
    the corrected nested access must be present in the source."""
    import inspect
    import agents.persona_deep_research as _m

    src = inspect.getsource(_m)
    assert 'item.get("metadata.url")' not in src
    assert 'item.get("metadata.title")' not in src
    assert 'item.get("searchResult.url")' not in src
    assert 'item.get("searchResult.title")' not in src
    # corrected nested access present (url + title)
    assert '(item.get("metadata") or {}).get("url")' in src
    assert '(item.get("metadata") or {}).get("title")' in src
