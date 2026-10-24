"""AstrBot 插件：宿舍电量自动提醒（完美校园云支付接口版）。

适配「宿舍电量不在智能水电页、而是显示在云支付缴费页」的学校。
数据接口为云支付网关的 /paygateway/smallpaygateway/trade，
查询剩余电量无需登录 token。

命令：
  /电量                       查询已绑定房间的剩余电量
  /电量搜房 <楼栋> [楼层] [房间号]  搜索房间ID，如 /电量搜房 4栋 1层 101
  /电量绑定 <房间ID>           绑定房间（ID 用 /电量搜房 获取）
  /电量解绑                   清空自己的绑定
  /电量推送                   在当前会话订阅每日定时播报

数据保存在 AstrBot 数据目录 data/plugin_data/<插件名>/users.json。
"""
import asyncio
import json
import os
import re
import time

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_wanmei_dorm_power"
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_data_dir() -> str:
    """AstrBot 规定的持久化目录：data/plugin_data/<plugin_name>/。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
        if get_astrbot_plugin_data_path:
            return os.path.join(str(get_astrbot_plugin_data_path()), PLUGIN_NAME)
    except Exception:
        pass
    try:
        from astrbot.api.star import StarTools
        getter = getattr(StarTools, "get_data_dir", None)
        if callable(getter):
            return str(getter(PLUGIN_NAME))
    except Exception:
        pass
    return os.path.abspath(os.path.join("data", "plugin_data", PLUGIN_NAME))


DATA_DIR = _resolve_data_dir()
USER_DATA_PATH = os.path.join(DATA_DIR, "users.json")


def _now_ts() -> str:
    return time.strftime("%y-%m-%d %H:%M:%S")


class _ApiError(RuntimeError):
    """接口返回的业务错误。"""


@register(
    "astrbot_plugin_wanmei_dorm_power",
    "cyqmq",
    "宿舍电量提醒（完美校园云支付接口）",
    "1.0.0",
)
class DormPowerCpPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._users: dict[str, dict] = {}
        self._load_users()

    # ---------- 配置读取 ----------
    def _cfg(self, key: str, default=None):
        if self.config is None:
            return default
        try:
            data = self.config
            getter = getattr(data, "get", None)
            if callable(getter):
                v = getter(key, None)
                if v is not None:
                    return v
            for v in data.values():
                if isinstance(v, dict) and key in v:
                    return v[key]
        except Exception:
            pass
        return default

    @property
    def _api_url(self) -> str:
        return str(self._cfg("api_url", "") or "")

    @property
    def _schoolcode(self) -> str:
        return str(self._cfg("schoolcode", "") or "")

    @property
    def _payproid(self) -> str:
        return str(self._cfg("payproid", "") or "")

    @property
    def _businesstype(self) -> str:
        return str(self._cfg("businesstype", "2") or "2")

    @property
    def _warn_threshold(self) -> float:
        try:
            return float(self._cfg("warn_threshold", 20) or 20)
        except (TypeError, ValueError):
            return 20.0

    # ---------- HTTP 调用 ----------
    async def _post(self, method: str, biz: dict) -> dict:
        """向云支付网关发起 POST，返回解析后的 JSON。"""
        payload = {
            "method": method,
            "bizcontent": json.dumps(biz, ensure_ascii=False),
            "timestamp": _now_ts(),
            "sourceId": 2,
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._api_url, json=payload) as resp:
                if resp.status != 200:
                    raise _ApiError(f"HTTP {resp.status}")
                data = await resp.json()
        if data.get("returncode") != "SUCCESS":
            raise _ApiError(data.get("returnmsg") or data.get("subMsg") or "接口返回失败")
        return data

    async def _get_room_state(self, room_id: str) -> dict:
        """查询房间剩余电量。返回 businessData 字典。"""
        biz = {
            "payproid": self._payproid,
            "schoolcode": self._schoolcode,
            "roomverify": room_id,
            "businesstype": self._businesstype,
        }
        data = await self._post("samllProgramGetRoomState", biz)
        return data.get("businessData") or {}

    async def _get_rooms(self, optype: int, areaid="0", buildid="0",
                         unitid="0", levelid="0") -> list:
        """获取房间级联列表：1校区/2楼栋/3楼层/4房间。"""
        biz = {
            "schoolno": self._schoolcode,
            "optype": optype,
            "payproid": self._payproid,
            "areaid": areaid,
            "buildid": buildid,
            "unitid": unitid,
            "levelid": levelid,
            "businesstype": self._businesstype,
            "idserial": "",
        }
        data = await self._post("samllProgramGetRoom", biz)
        return data.get("businessData") or []

    # ---------- 用户数据 ----------
    def _load_users(self):
        try:
            with open(USER_DATA_PATH, encoding="utf-8") as f:
                self._users = json.load(f)
        except Exception:
            self._users = {}

    def _save_users(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(USER_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(self._users, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"[{PLUGIN_NAME}] 用户数据保存失败: {e}")

    # ---------- 消息发送 ----------
    async def _send(self, origin: str, text: str) -> None:
        try:
            await self.context.send_message(origin, MessageChain().message(text))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 消息发送失败: {e}")

    @staticmethod
    def _args(event: AstrMessageEvent, cmd: str) -> list[str]:
        """取指令参数，兼容带/不带 / 前缀、参数与指令名粘连、中文逗号顿号分隔。"""
        text = (event.message_str or "").strip().lstrip("/").strip()
        if text.startswith(cmd):
            rest = text[len(cmd):]
        elif text.split() and text.split()[0].startswith(cmd):
            tokens = text.split()
            rest = tokens[0][len(cmd):] + " " + " ".join(tokens[1:])
        else:
            rest = ""
        return [a for a in re.split(r"[\s,，、]+", rest) if a]

    # ---------- 配置检查 ----------
    def _config_missing(self) -> str | None:
        """检查核心配置是否齐全；缺失时返回提示文案。"""
        missing = []
        if not self._schoolcode:
            missing.append("schoolcode（学校代码）")
        if not self._payproid:
            missing.append("payproid（缴费项目ID）")
        if not self._api_url:
            missing.append("api_url（接口地址）")
        if not missing:
            return None
        return (
            "插件核心配置未设置：" + "、".join(missing) + "。\n"
            "请用仓库 tools/detect_config.py 解析你的缴费链接自动获取，"
            "或在 AstrBot 管理面板「插件配置」中手动填写。"
        )

    # ---------- 格式化电量 ----------
    def _format_quantity(self, state: dict, room_name: str) -> str:
        quantity = state.get("quantity")
        unit = state.get("quantityunit", "度")
        text = f"⚡ {room_name} 剩余电量：{quantity} {unit}"
        try:
            if float(quantity) <= self._warn_threshold:
                text += "\n⚠️ 电量偏低，请及时充值！"
        except (TypeError, ValueError):
            pass
        return text

    # ---------- 命令：查询 ----------
    @filter.command("电量")
    async def cmd_power(self, event: AstrMessageEvent):
        """查询已绑定房间的剩余电量"""
        err = self._config_missing()
        if err:
            yield event.plain_result(err)
            return
        uid = event.get_sender_id()
        info = self._users.get(uid)
        room_id = (info or {}).get("room_id") or str(self._cfg("default_room", ""))
        if not room_id:
            yield event.plain_result(
                "还没有绑定房间。请先发 /电量搜房 <楼栋> [楼层] [房间号] 找到房间，"
                "再 /电量绑定 <房间ID>。"
            )
            return
        try:
            state = await self._get_room_state(room_id)
            name = (info or {}).get("room_name") or room_id
            yield event.plain_result(self._format_quantity(state, name))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    # ---------- 命令：搜房 ----------
    @filter.command("电量搜房")
    async def cmd_search(self, event: AstrMessageEvent):
        """搜索房间：/电量搜房 <楼栋关键词> [楼层关键词] [房间号关键词]"""
        err = self._config_missing()
        if err:
            yield event.plain_result(err)
            return
        args = self._args(event, "电量搜房")
        if not args:
            yield event.plain_result(
                "用法：/电量搜房 <楼栋关键词> [楼层关键词] [房间号关键词]\n"
                "例如：/电量搜房 4栋 1层 101"
            )
            return
        try:
            results = await self._search_rooms(args)
        except Exception as e:
            yield event.plain_result(f"搜索失败：{e}")
            return
        if not results:
            yield event.plain_result("没有匹配到房间，请检查关键词（楼栋名/楼层/房间号）后重试。")
            return
        lines = []
        has_room = False
        for rid, name in results:
            if rid:
                has_room = True
                lines.append(f"{name} → {rid}")
            else:
                lines.append(name)
        tail = (
            "找到后用 /电量绑定 <房间ID> 绑定。"
            if has_room
            else "继续发送 /电量搜房 <楼栋> <楼层> <房间号> 缩小范围。"
        )
        yield event.plain_result("匹配结果：\n" + "\n".join(lines) + "\n" + tail)

    async def _search_rooms(self, args: list[str]) -> list[tuple[str, str]]:
        """按关键词逐级下探搜索房间，返回 [(room_id, 展示名)]。

        一级关键词匹配楼栋，二级匹配楼层，三级匹配房间号。
        """
        # 1. 校区 -> 楼栋
        areas = await self._get_rooms(1)
        buildings = []
        for area in areas:
            try:
                bs = await self._get_rooms(2, areaid=area["id"])
            except Exception:
                continue
            for b in bs:
                buildings.append((area["id"], b["id"], area.get("name", ""), b["name"]))

        kw_building = args[0]
        matched_buildings = [b for b in buildings if kw_building in b[3]]
        if not matched_buildings:
            fuzzy = kw_building.replace("栋", "").strip()
            matched_buildings = [
                b for b in buildings if fuzzy and fuzzy in b[3].replace("栋", "")
            ]
        if not matched_buildings:
            return []
        if len(args) == 1:
            return [("", f"{b[2]} {b[3]}（继续加楼层关键词）") for b in matched_buildings]

        # 2. 楼层
        kw_floor = args[1]
        floors = []
        for areaid, bid, aname, bname in matched_buildings:
            try:
                fs = await self._get_rooms(3, areaid=areaid, buildid=bid)
            except Exception:
                continue
            for f in fs:
                floors.append((areaid, bid, aname, bname, f["id"], f["name"]))
        matched_floors = [f for f in floors if kw_floor in f[5]]
        if not matched_floors:
            return []
        if len(args) == 2:
            return [("", f"{f[3]} {f[4]} {f[5]}（继续加房间号关键词）") for f in matched_floors]

        # 3. 房间
        kw_room = args[2]
        rooms = []
        for areaid, bid, aname, bname, fid, fname in matched_floors:
            try:
                rs = await self._get_rooms(4, areaid=areaid, buildid=bid, levelid=fid)
            except Exception:
                continue
            for r in rs:
                rooms.append((r["id"], f"{bname} {fname} {r['name']}"))
        return [r for r in rooms if kw_room in r[1]]

    # ---------- 命令：绑定/解绑/推送 ----------
    @filter.command("电量绑定")
    async def cmd_bind(self, event: AstrMessageEvent):
        """绑定房间：/电量绑定 <房间ID>"""
        err = self._config_missing()
        if err:
            yield event.plain_result(err)
            return
        args = self._args(event, "电量绑定")
        if not args:
            yield event.plain_result("用法：/电量绑定 <房间ID>，房间ID用 /电量搜房 获取。")
            return
        room_id = args[0]
        try:
            state = await self._get_room_state(room_id)
            quantity = state.get("quantity")
            unit = state.get("quantityunit", "度")
        except Exception as e:
            yield event.plain_result(f"绑定失败：{e}（请确认房间ID是否正确）")
            return
        uid = event.get_sender_id()
        info = self._users.setdefault(uid, {})
        info["room_id"] = room_id
        info["room_name"] = room_id
        info["origin"] = event.unified_msg_origin
        self._save_users()
        yield event.plain_result(f"✅ 绑定成功！当前剩余电量：{quantity} {unit}")

    @filter.command("电量解绑")
    async def cmd_unbind(self, event: AstrMessageEvent):
        """清空自己的房间绑定"""
        uid = event.get_sender_id()
        if uid in self._users:
            del self._users[uid]
            self._save_users()
        yield event.plain_result("已解绑。")

    @filter.command("电量推送")
    async def cmd_push(self, event: AstrMessageEvent):
        """在当前会话订阅每日定时播报"""
        err = self._config_missing()
        if err:
            yield event.plain_result(err)
            return
        uid = event.get_sender_id()
        info = self._users.get(uid)
        if not info or not info.get("room_id"):
            yield event.plain_result("请先绑定房间再订阅推送：/电量绑定 <房间ID>")
            return
        self._users[uid]["origin"] = event.unified_msg_origin
        self._save_users()
        hours = str(self._cfg("cron_hours", "8,20") or "8,20")
        yield event.plain_result(
            f"✅ 已订阅每日定时播报（{hours} 点）。"
            f"电量低于 {self._warn_threshold:g} 度时会额外提醒。"
        )

    # ---------- 定时任务 ----------
    async def initialize(self):
        hours = str(self._cfg("cron_hours", "8,20") or "8,20")
        self.scheduler.add_job(self._scheduled_report, "cron", hour=hours, minute=0)
        self.scheduler.start()
        logger.info(
            f"[{PLUGIN_NAME}] 插件已加载，定时播报小时: {hours}，已绑定用户: {len(self._users)}"
        )

    async def terminate(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=False)

    async def _scheduled_report(self):
        if self._config_missing():
            logger.warning(f"[{PLUGIN_NAME}] 核心配置未设置，跳过定时播报")
            return
        self._load_users()
        for uid, info in self._users.items():
            origin = info.get("origin")
            room_id = info.get("room_id")
            if not origin or not room_id:
                continue
            try:
                state = await self._get_room_state(room_id)
                name = info.get("room_name") or room_id
                text = self._format_quantity(state, name)
                await self._send(origin, text)
            except Exception as e:
                logger.error(f"[{PLUGIN_NAME}] 定时播报失败: {e}")