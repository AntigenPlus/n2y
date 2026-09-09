"""
Tests of the `Client`'s wrapping and caching behavior.
"""

import logging
from unittest import mock

from pandoc.types import Str

from n2y.emoji import Emoji
from n2y.errors import APIErrorCode, APIResponseError
from n2y.notion import Client
from n2y.notion_mocks import (
    mock_database,
    mock_id,
    mock_page,
    mock_property_value,
    mock_rich_text,
    mock_user,
)
from n2y.user import User


def make_client():
    client = Client("")
    patcher = mock.patch.object(Client, "wrap_notion_user")
    wrap_notion_user = patcher.start()
    wrap_notion_user.return_value = User(client, mock_user())
    return client, patcher


def test_page_with_custom_emoji_icon():
    client, patcher = make_client()
    try:
        page_data = mock_page("Iconic")
        page_data["icon"] = {
            "type": "custom_emoji",
            "custom_emoji": {
                "id": mock_id(),
                "name": "bufo",
                "url": "https://example.com/bufo.png",
            },
        }
        page = client._wrap_notion_page(page_data)
        assert isinstance(page.icon, Emoji)
        assert page.icon.emoji is None
        assert page.icon.name == "bufo"
    finally:
        patcher.stop()


def test_database_with_custom_emoji_icon():
    client, patcher = make_client()
    try:
        database_data = mock_database("Iconic")
        database_data["icon"] = {
            "type": "custom_emoji",
            "custom_emoji": {"id": mock_id(), "name": "bufo", "url": "u"},
        }
        database = client._wrap_notion_database(database_data)
        assert isinstance(database.icon, Emoji)
    finally:
        patcher.stop()


def test_page_cache_ignores_id_hyphenation():
    client, patcher = make_client()
    try:
        page_data = mock_page("Cached")
        hyphenated = page_data["id"]
        unhyphenated = hyphenated.replace("-", "")
        with mock.patch.object(Client, "get_notion_page") as get_notion_page:
            get_notion_page.return_value = page_data
            first = client.get_page(unhyphenated)
            second = client.get_page(hyphenated)
            assert get_notion_page.call_count == 1
        assert first is second
        # a page returned from a database query is the same instance too
        assert client._wrap_notion_page(page_data) is first
    finally:
        patcher.stop()


def test_database_cache_ignores_id_hyphenation():
    client, patcher = make_client()
    try:
        database_data = mock_database("Cached")
        hyphenated = database_data["id"]
        with mock.patch.object(Client, "get_notion_database") as get_notion_database:
            get_notion_database.return_value = database_data
            first = client.get_database(hyphenated.replace("-", ""))
            second = client.get_database(hyphenated)
            assert get_notion_database.call_count == 1
        assert first is second
    finally:
        patcher.stop()


def test_unknown_mention_type_renders_plain_text(caplog):
    client = Client("")
    rich_text = mock_rich_text("@Today")
    rich_text["type"] = "mention"
    rich_text["mention"] = {"type": "template_mention", "template_mention": {}}
    with caplog.at_level(logging.WARNING):
        ast = client.wrap_notion_rich_text_array([rich_text]).to_pandoc()
    assert ast == [Str("@Today")]
    assert "template_mention" in caplog.text


def test_unknown_property_value_type_is_null(caplog):
    client = Client("")
    notion_data = mock_property_value("verification", {"state": "verified"})
    with caplog.at_level(logging.WARNING):
        value = client.wrap_notion_property_value(notion_data, None).to_value()
    assert value is None
    assert "verification" in caplog.text


def test_unknown_property_type_is_wrapped():
    client = Client("")
    notion_data = {
        "id": "abc",
        "type": "verification",
        "name": "Verification",
        "verification": {},
    }
    prop = client.wrap_notion_property(notion_data)
    assert prop.name == "Verification"


def test_wrap_notion_user_without_user_capability(caplog):
    class MockResponse:
        status_code = 403
        headers = {}
        text = ""

    client = Client("")
    user_id = mock_id()
    with mock.patch.object(Client, "get_notion_user") as get_notion_user:
        get_notion_user.side_effect = APIResponseError(
            MockResponse(), "restricted", APIErrorCode.RestrictedResource
        )
        with caplog.at_level(logging.WARNING):
            user = client.wrap_notion_user({"object": "user", "id": user_id})
    assert user.notion_id == user_id
    assert user.name is None
    assert user_id in caplog.text
