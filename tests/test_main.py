"""main.py 纯逻辑单元测试（不依赖真实 AstrBot）。"""
import asyncio
import json
from datetime import datetime

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
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    ev = FakeEvent("/电量推送开启", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_enable(ev)))
    assert plugin._push_settings["aiocqhttp:GroupMessage:2001"]["enabled"] is True
    assert "开启" in results[0]
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["aiocqhttp:GroupMessage:2001"]["enabled"] is True


def test_cmd_push_disable(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    ev = FakeEvent("/电量推送关闭", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_disable(ev)))
    assert plugin._push_settings["aiocqhttp:GroupMessage:2001"]["enabled"] is False
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


# ---------- 会话级推送配置 ----------
def test_parse_hours(plugin):
    assert plugin._parse_hours("8,20") == [8, 20]
    assert plugin._parse_hours("9, 21") == [9, 21]
    assert plugin._parse_hours("") == []
    assert plugin._parse_hours("8,abc,25") == [8]  # 过滤非法值


def test_valid_hours(plugin):
    assert plugin._valid_hours("9,21")
    assert plugin._valid_hours("8")
    assert not plugin._valid_hours("")
    assert not plugin._valid_hours("8,abc")
    assert not plugin._valid_hours("24")


def test_session_cron_hours_fallback_global(plugin):
    plugin.config = FakeAstrBotConfig({"cron_hours": "8,20"})
    assert plugin._session_cron_hours("aiocqhttp:GroupMessage:2") == [8, 20]


def test_session_cron_hours_override(plugin):
    plugin._push_settings["aiocqhttp:GroupMessage:2"] = {"cron_hours": "9,21"}
    assert plugin._session_cron_hours("aiocqhttp:GroupMessage:2") == [9, 21]


def test_session_warn_threshold_fallback_global(plugin):
    plugin.config = FakeAstrBotConfig({"warn_threshold": 20})
    assert plugin._session_warn_threshold("aiocqhttp:GroupMessage:2") == 20.0


def test_session_warn_threshold_override(plugin):
    plugin._push_settings["aiocqhttp:GroupMessage:2"] = {"warn_threshold": 15}
    assert plugin._session_warn_threshold("aiocqhttp:GroupMessage:2") == 15.0


# ---------- 推送控制权限 ----------
def test_can_control_push_empty_whitelist(plugin):
    # 白名单为空 -> 任何人可控制
    ev = FakeEvent("/电量推送关闭", origin="aiocqhttp:GroupMessage:2001", sender_id="u1")
    assert plugin._can_control_push(ev)


def test_can_control_push_whitelisted(plugin):
    plugin.config = FakeAstrBotConfig({"push_control_whitelist": "10001,10002"})
    ev = FakeEvent("/电量推送关闭", origin="aiocqhttp:GroupMessage:2001", sender_id="10001")
    assert plugin._can_control_push(ev)


def test_can_control_push_denied(plugin):
    plugin.config = FakeAstrBotConfig({"push_control_whitelist": "10001"})
    ev = FakeEvent("/电量推送关闭", origin="aiocqhttp:GroupMessage:2001", sender_id="9999")
    assert not plugin._can_control_push(ev)


def test_cmd_push_enable_denied(plugin):
    plugin.config = FakeAstrBotConfig({"push_control_whitelist": "10001"})
    ev = FakeEvent("/电量推送开启", origin="aiocqhttp:GroupMessage:2001", sender_id="9999")
    results = asyncio.run(_collect(plugin.cmd_push_enable(ev)))
    assert "无权限" in results[0]


def test_cmd_push_hours(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    ev = FakeEvent("/电量推送时段 9,21", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_hours(ev)))
    assert plugin._push_settings["aiocqhttp:GroupMessage:2001"]["cron_hours"] == "9,21"
    assert "9,21" in results[0]


def test_cmd_push_hours_invalid(plugin):
    ev = FakeEvent("/电量推送时段 abc", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_hours(ev)))
    assert "格式不正确" in results[0]


def test_cmd_push_threshold(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    ev = FakeEvent("/电量推送阈值 15", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_threshold(ev)))
    assert plugin._push_settings["aiocqhttp:GroupMessage:2001"]["warn_threshold"] == 15.0
    assert "15" in results[0]


def test_cmd_push_config_shows_session_override(plugin):
    plugin._push_settings["aiocqhttp:GroupMessage:2001"] = {
        "cron_hours": "9,21", "warn_threshold": 15, "enabled": True}
    ev = FakeEvent("/电量推送配置", origin="aiocqhttp:GroupMessage:2001")
    results = asyncio.run(_collect(plugin.cmd_push_config(ev)))
    text = results[0]
    assert "9,21" in text and "会话级" in text


# ---------- 定时播报 ----------
async def _fake_state(room_id):
    return {"quantity": 55, "quantityunit": "度"}


def test_scheduled_report_respects_disabled_session(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps(
        {"u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin}}))
    (tmp_path / "w.json").write_text(json.dumps({origin: False}))
    plugin.config = FakeAstrBotConfig({"cron_hours": str(datetime.now().hour)})
    plugin._config_missing = lambda: None
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert sent == []


def test_scheduled_report_pushes_enabled_session(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps(
        {"u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin}}))
    (tmp_path / "w.json").write_text(json.dumps({origin: True}))
    plugin.config = FakeAstrBotConfig({"cron_hours": str(datetime.now().hour)})
    plugin._config_missing = lambda: None
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert len(sent) == 1 and sent[0][0] == origin


def test_scheduled_report_dedup_same_room(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps({
        "u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin},
        "u2": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin},
    }))
    (tmp_path / "w.json").write_text(json.dumps({origin: True}))
    plugin.config = FakeAstrBotConfig({"cron_hours": str(datetime.now().hour)})
    plugin._config_missing = lambda: None
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert len(sent) == 1


def test_scheduled_report_skips_out_of_session_hours(monkeypatch, tmp_path, plugin):
    monkeypatch.setattr(main, "WHITELIST_PATH", str(tmp_path / "w.json"))
    monkeypatch.setattr(main, "PUSH_SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "USER_DATA_PATH", str(tmp_path / "u.json"))
    origin = "aiocqhttp:GroupMessage:2001"
    (tmp_path / "u.json").write_text(json.dumps(
        {"u1": {"room_id": "r1", "room_name": "4栋 1层 101", "origin": origin}}))
    (tmp_path / "w.json").write_text(json.dumps({origin: True}))
    # 会话级时段设为不包含当前小时（取当前小时+1，若为23则取0）
    other_hour = (datetime.now().hour + 1) % 24
    (tmp_path / "s.json").write_text(json.dumps(
        {origin: {"enabled": True, "cron_hours": str(other_hour)}}))
    plugin.config = FakeAstrBotConfig({"cron_hours": str(datetime.now().hour)})
    plugin._config_missing = lambda: None
    plugin._get_room_state = _fake_state
    sent = []
    plugin._send = lambda o, t: _noop_append(sent, o, t)
    asyncio.run(plugin._scheduled_report())
    assert sent == []
