import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quart import Quart
from web.web_api import BigBananaWebApi


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    plugin_dir = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(plugin_dir.parent))
    entry = importlib.import_module(f"{plugin_dir.name}.main")
    instance = entry.BigBanana.__new__(entry.BigBanana)
    instance.data_dir = tmp_path
    instance.context = SimpleNamespace()
    instance.avatar_map = {}
    return instance


def test_legacy_rules_and_description_only_rules_survive_save_and_reload(plugin):
    legacy = {
        "123": " first.png ",
        "456": [" second.png ", "", None, "third.png"],
        "789": {"description": "娇小"},
        "empty": [],
    }
    plugin.update_avatar_substitutions(legacy, save=False)
    expected = {
        "123": {"images": ["first.png"], "description": ""},
        "456": {"images": ["second.png", "third.png"], "description": ""},
        "789": {"images": [], "description": "娇小"},
    }
    assert plugin.avatar_map == expected
    path = plugin.data_dir / "avatar_substitutions.json"
    assert not path.exists()
    plugin.update_avatar_substitutions(plugin.avatar_map)
    saved = json.loads(path.read_text())
    assert saved == expected
    plugin.avatar_map = {}
    plugin.update_avatar_substitutions(saved, save=False)
    assert plugin.avatar_map == expected


@pytest.mark.parametrize("description", ["字" * 100, "😀" * 100])
def test_accepts_one_hundred_unicode_characters(plugin, description):
    plugin.update_avatar_substitutions({"123": {"description": description}})
    assert plugin.avatar_map["123"] == {"images": [], "description": description}


@pytest.mark.parametrize("description", ["字" * 101, 123, None])
def test_invalid_description_does_not_overwrite_saved_rules(plugin, description):
    plugin.update_avatar_substitutions({"123": {"description": "原描述"}})
    path = plugin.data_dir / "avatar_substitutions.json"
    saved = path.read_bytes()
    with pytest.raises(ValueError):
        plugin.update_avatar_substitutions({"123": {"description": description}})
    assert path.read_bytes() == saved
    assert plugin.avatar_map["123"]["description"] == "原描述"


@pytest.mark.parametrize("command", ["/大香蕉人设替换", "/人设替换"])
def test_command_updates_only_the_sender_and_preserves_images(plugin, command):
    plugin.update_avatar_substitutions(
        {"123": ["first.png", "second.png"], "456": "other.png"}
    )
    description = '娇小  可爱\nheight 150cm <tag> "quoted"'
    event = SimpleNamespace(
        message_str=f"{command} {description}",
        get_sender_id=lambda: "123",
        plain_result=lambda text: text,
        stop_event=Mock(),
    )

    async def run():
        return [result async for result in plugin.set_persona_description(event)]

    results = asyncio.run(run())
    assert results == [f"✅ 已更新你的人设额外描述：{description}"]
    assert plugin.avatar_map == {
        "123": {"images": ["first.png", "second.png"], "description": description},
        "456": {"images": ["other.png"], "description": ""},
    }
    event.stop_event.assert_called_once()
    assert (
        json.loads((plugin.data_dir / "avatar_substitutions.json").read_text())
        == plugin.avatar_map
    )


@pytest.mark.parametrize("description", ["娇小", "字" * 100, "", "字" * 101])
def test_command_creates_description_only_rule_or_rejects_invalid_input(
    plugin, description
):
    event = SimpleNamespace(
        message_str=f"人设替换 {description}",
        get_sender_id=lambda: "123",
        plain_result=lambda text: text,
        stop_event=Mock(),
    )

    async def run():
        return [result async for result in plugin.set_persona_description(event)]

    results = asyncio.run(run())
    if 0 < len(description) <= 100:
        assert plugin.avatar_map["123"] == {"images": [], "description": description}
        assert "已更新" in results[0]
    else:
        assert plugin.avatar_map == {}
        assert "100" in results[0]
        assert not (plugin.data_dir / "avatar_substitutions.json").exists()


def test_command_is_registered_for_everyone_with_both_names(plugin):
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.permission import PermissionTypeFilter
    from astrbot.core.star.star_handler import star_handlers_registry

    handler = next(
        item
        for item in star_handlers_registry
        if item.handler is type(plugin).set_persona_description
    )
    command_filter = next(
        item for item in handler.event_filters if isinstance(item, CommandFilter)
    )
    assert set(command_filter.get_complete_command_names()) == {
        "大香蕉人设替换",
        "人设替换",
    }
    assert not any(
        isinstance(item, PermissionTypeFilter) for item in handler.event_filters
    )
    assert handler.extras_configs["priority"] > 5


def test_failed_save_preserves_active_rules_and_previous_file(plugin, monkeypatch):
    plugin.update_avatar_substitutions({"123": {"description": "原描述"}})
    path = plugin.data_dir / "avatar_substitutions.json"
    saved = path.read_bytes()
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk error")))
    with pytest.raises(OSError):
        plugin.update_avatar_substitutions({"123": {"description": "新描述"}})
    assert path.read_bytes() == saved
    assert plugin.avatar_map["123"]["description"] == "原描述"


def test_web_api_round_trips_persona_rules_and_rejects_overlong_description(plugin):
    api = BigBananaWebApi(plugin)
    app = Quart(__name__)
    app.add_url_rule("/substitutions", "get", api.api_substitutions_get)
    app.add_url_rule(
        "/substitutions", "set", api.api_substitutions_set, methods=["POST"]
    )
    body = {
        "123": {"images": [], "description": "娇小"},
        "456": {"images": ["tall.png"], "description": "高大"},
    }

    async def run():
        client = app.test_client()
        response = await client.post("/substitutions", json=body)
        assert (await response.get_json())["status"] == "ok"
        response = await client.get("/substitutions")
        assert (await response.get_json())["data"] == body
        response = await client.post(
            "/substitutions", json={"123": {"description": "字" * 101}}
        )
        assert (await response.get_json())["status"] == "error"
        response = await client.get("/substitutions")
        assert (await response.get_json())["data"] == body

    asyncio.run(run())
    assert (
        json.loads((plugin.data_dir / "avatar_substitutions.json").read_text()) == body
    )
