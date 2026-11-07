"""main.py 纯逻辑单元测试（不依赖真实 AstrBot）。"""
import asyncio
import json

import pytest

from tests.conftest import FakeAstrBotConfig, FakeEvent, load_main_module


async def _noop_append(sent, origin, text):
    sent.append((origin, text))

main = load_main_module()
Plugin = main.DormPowerCpPlugin


@pytest.fixture
def plugin():
    return Plugin(context=None)


# ---------- _args ----------
def test_args_basic(plugin):
    ev = FakeEvent("/电量绑定 abc123")
    assert plugin._args(ev, "电量绑定") == ["abc123"]


def test_args_no_slash(plugin):
    ev = FakeEvent("电量绑定 abc123")
    assert plugin._args(ev, "电量绑定") == ["abc123"]


def test_args_sticky_no_space(plugin):
    ev = FakeEvent("/电量搜房 3栋3楼335号")
    assert plugin._args(ev, "电量搜房") == ["3栋3楼335号"]


def test_args_chinese_separators(plugin):
    ev = FakeEvent("/电量搜房 3栋，1层，335")
    assert plugin._args(ev, "电量搜房") == ["3栋", "1层", "335"]


def test_args_strip_platform_meta(plugin):
    ev = FakeEvent("/电量绑定 abc [MSG_ID:123]")
    assert plugin._args(ev, "电量绑定") == ["abc"]


def test_args_none_message(plugin):
    ev = FakeEvent(None)
    assert plugin._args(ev, "电量绑定") == []


# ---------- _cn2digit ----------
def test_cn2digit(plugin):
    assert plugin._cn2digit("学生公寓三栋") == "学生公寓3栋"
    assert plugin._cn2digit("3栋") == "3栋"
    assert plugin._cn2digit("十二栋") == "12栋"
    assert plugin._cn2digit("二十三栋") == "23栋"
    assert plugin._cn2digit("十栋") == "10栋"


# ---------- _match_building ----------
def test_match_building_exact(plugin):
    assert plugin._match_building("4栋", "4栋")


def test_match_building_no_name(plugin):
    assert plugin._match_building("3栋", "学生公寓3号楼")


def test_match_building_chinese(plugin):
    assert plugin._match_building("三栋", "学生公寓3栋")


def test_match_building_no_match(plugin):
    assert not plugin._match_building("5栋", "学生公寓3号楼")


# ---------- _normalize_search_args ----------
def test_normalize_sticky(plugin):
    assert plugin._normalize_search_args(["3栋3楼335号"]) == ["3栋", "3层", "335"]


def test_normalize_floor_word(plugin):
    assert plugin._normalize_search_args(["3栋", "3楼", "335"]) == ["3栋", "3层", "335"]


def test_normalize_room_suffix(plugin):
    assert plugin._normalize_search_args(["3栋", "3层", "335号"]) == ["3栋", "3层", "335"]


# ---------- _session_is_private ----------
def test_session_is_private(plugin):
    assert plugin._session_is_private("aiocqhttp:FriendMessage:1001")
    assert not plugin._session_is_private("aiocqhttp:GroupMessage:2001")
    assert not plugin._session_is_private("bad-origin")


# ---------- _push_enabled_for ----------
def test_push_enabled_default_private_on(plugin):
    # 未显式设置，跟随全局开关（默认开启）
    assert plugin._push_enabled_for("aiocqhttp:FriendMessage:1")


def test_push_enabled_default_group_on(plugin):
    assert plugin._push_enabled_for("aiocqhttp:GroupMessage:2")


def test_push_enabled_override(plugin):
    plugin._whitelist["aiocqhttp:GroupMessage:2"] = False
    assert not plugin._push_enabled_for("aiocqhttp:GroupMessage:2")
    plugin._whitelist["aiocqhttp:GroupMessage:3"] = True
    assert plugin._push_enabled_for("aiocqhttp:GroupMessage:3")


def test_push_enabled_respects_global_off(plugin):
    plugin.config = FakeAstrBotConfig(
        {"group_push_enabled": False}
    )
    assert not plugin._push_enabled_for("aiocqhttp:GroupMessage:2")
    # 但显式开启的会话仍允许
    plugin._whitelist["aiocqhttp:GroupMessage:2"] = True
    assert plugin._push_enabled_for("aiocqhttp:GroupMessage:2")


def test_push_enabled_respects_global_private_off(plugin):
    plugin.config = FakeAstrBotConfig(
        {"private_push_enabled": False}
    )
    assert not plugin._push_enabled_for("aiocqhttp:FriendMessage:1")
    assert plugin._push_enabled_for("aiocqhttp:GroupMessage:2")


# ---------- _format_quantity ----------
def test_format_quantity_normal(plugin):
    text = plugin._format_quantity({"quantity": 55, "quantityunit": "度"}, "4栋 1层 101")
    assert "55" in text and "度" in text and "101" in text
    assert "偏低" not in text


def test_format_quantity_low(plugin):
    text = plugin._format_quantity({"quantity": 5, "quantityunit": "度"}, "4栋 1层 101")
    assert "偏低" in text


def test_format_quantity_none(plugin):
    text = plugin._format_quantity({}, "4栋 1层 101")
    assert "None" in text


# ---------- _pick ----------
def test_pick_ok(plugin):
    assert plugin._pick([{"id": 1}, {"id": 2}], 1)["id"] == 1


def test_pick_out_of_range(plugin):
    with pytest.raises(main._PickError):
        plugin._pick([{"id": 1}], 5)


# ---------- 推送开关命令 ----------
async def _collect(agen):
    return [r async for r in agen]


def test_cmd_push_enable(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    ev = FakeEvent("/电量推送开启", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_enable(ev)))
    assert plugin._whitelist["aiocqhttp:GroupMessage:2001"] is True
    assert "开启" in results[0]
    import json
    data = json.loads((tmp_path / "w.json").read_text())
    assert data["aiocqhttp:GroupMessage:2001"] is True


def test_cmd_push_disable(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    ev = FakeEvent("/电量推送关闭", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_disable(ev)))
    assert plugin._whitelist["aiocqhttp:GroupMessage:2001"] is False
    assert "关闭" in results[0]


def test_cmd_push_status_override(plugin):
    plugin._whitelist["aiocqhttp:GroupMessage:2001"] = False
    ev = FakeEvent("/电量推送状态", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_status(ev)))
    text = results[0]
    assert "群聊" in text
    assert "本会话推送：关闭" in text
    assert "本会话已显式关闭" in text


def test_cmd_push_status_follow_global(plugin):
    ev = FakeEvent("/电量推送状态", origin="aiocqhttp:FriendMessage:1001")
    results = asyncio.run(_collect(plugin.cmd_push_status(ev)))
    text = results[0]
    assert "私聊" in text
    assert "跟随全局开关" in text


# ---------- 定时播报 ----------
async def _fake_state(room_id):
    return {"quantity": 55, "quantityunit": "度"}


def test_scheduled_report_respects_disabled_session(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps(
        {"u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin}}))
    (tmp_path / "w.json").write_text(json.dumps({origin: False}))
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert sent == []


def test_scheduled_report_pushes_enabled_session(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps(
        {"u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin}}))
    (tmp_path / "w.json").write_text(json.dumps({origin: True}))
    plugin._config_missing = lambda: None
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert len(sent) == 1 and sent[0][0] == origin


def test_scheduled_report_dedup_same_room(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps({
        "u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin},
        "u2": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin},
    }))
    (tmp_path / "w.json").write_text(json.dumps({origin: True}))
    plugin._config_missing = lambda: None
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert len(sent) == 1
