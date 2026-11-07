"""AstrBot 插件：宿舍电量自动提醒（完美校园云支付接口版）。

适配「宿舍电量不在智能水电页、而是显示在云支付缴费页」的学校。
数据接口为云支付网关的 /paygateway/smallpaygateway/trade，
查询剩余电量无需登录 token。

命令：
  /电量                       查询已绑定房间的剩余电量
  /电量搜房 <楼栋> [楼层] [房间号]  搜索房间ID，如 /电量搜房 4栋 1层 101
  /电量浏览 [序号...]          递进式浏览 校区→楼栋→楼层→房间
  /电量绑定 <房间ID>           绑定房间（ID 用 /电量搜房 获取）
  /电量解绑                   清空自己的绑定
  /电量推送                   在当前会话订阅每日定时播报
  /电量推送开启 / 关闭 / 状态   启用、关闭或查看当前会话（群聊/私聊）的定时推送
  /电量推送时段 <小时>         设置当前会话推送时段，如 /电量推送时段 9,21
  /电量推送阈值 <度数>         设置当前会话低电量预警阈值，如 /电量推送阈值 15
  /电量推送配置               查看当前会话推送配置（时段/阈值/来源）

数据保存在 AstrBot 数据目录 data/plugin_data/<插件名>/users.json。
"""
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_wanmei_dorm_power"
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# unified_msg_origin 格式: <platform>:<message_type>:<session_id>
_MSG_TYPE_FRIEND = "FriendMessage"


def _load_side_module(mod_name: str, filename: str):
    """按文件路径加载同目录模块（AstrBot 插件相对导入不一定可用）。"""
    path = os.path.join(PLUGIN_DIR, filename)
    if os.path.isfile(path):
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    return None


_pay_parser = _load_side_module("_pay_url_parser", "pay_url_parser.py")
if _pay_parser is not None:
    parse_pay_url = _pay_parser.parse_pay_url
else:  # 兜底：正常 import 路径可用时
    from pay_url_parser import parse_pay_url  # type: ignore


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
WHITELIST_PATH = os.path.join(DATA_DIR, "push_whitelist.json")
ROOM_NAME_PATH = os.path.join(DATA_DIR, "room_names.json")
PUSH_SETTINGS_PATH = os.path.join(DATA_DIR, "push_settings.json")


def _now_ts() -> str:
    return time.strftime("%y-%m-%d %H:%M:%S")


class _ApiError(RuntimeError):
    """接口返回的业务错误。"""


class _PickError(ValueError):
    """递进式浏览时序号超出范围。"""


@register(
    "astrbot_plugin_wanmei_dorm_power",
    "cyqmq",
    "宿舍电量提醒（完美校园云支付接口）",
    "1.6.0",
)
class DormPowerCpPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._users: dict[str, dict] = {}
        # 推送白名单：origin -> True(显式开启) / False(显式关闭)；不在其中则跟随全局开关
        self._whitelist: dict[str, bool] = {}
        # 会话级推送配置：origin -> {cron_hours, warn_threshold, enabled}
        self._push_settings: dict[str, dict] = {}
        # 房间ID -> 友好名（如「4栋 1层 101」），绑定后展示用
        self._room_names: dict[str, str] = {}
        self._session: aiohttp.ClientSession | None = None
        self._load_users()
        self._load_whitelist()
        self._load_push_settings()
        self._load_room_names()

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

    @property
    def _private_push_enabled(self) -> bool:
        """全局是否允许私聊会话接收定时推送。"""
        return bool(self._cfg("private_push_enabled", True))

    @property
    def _group_push_enabled(self) -> bool:
        """全局是否允许群聊会话接收定时推送。"""
        return bool(self._cfg("group_push_enabled", True))

    @property
    def _push_control_whitelist(self) -> set[str]:
        """可控制推送开关的用户 ID 白名单；为空表示任何绑定用户都可控制（兼容旧行为）。"""
        raw = str(self._cfg("push_control_whitelist", "") or "")
        return {s.strip() for s in re.split(r"[,，、\s]+", raw) if s.strip()}

    # ---------- 推送开关判定 ----------
    @staticmethod
    def _session_is_private(origin: str) -> bool:
        """根据 unified_msg_origin 判断会话是否为私聊。"""
        try:
            return origin.split(":")[1] == _MSG_TYPE_FRIEND
        except IndexError:
            return False

    def _push_enabled_for(self, origin: str) -> bool:
        """判断某会话当前是否允许接收推送。

        优先级：会话级配置 enabled > 旧版白名单显式设置 > 全局私聊/群聊开关。
        """
        settings = self._push_settings.get(origin)
        if settings and "enabled" in settings:
            return bool(settings["enabled"])
        override = self._whitelist.get(origin)
        if override is not None:
            return override
        if self._session_is_private(origin):
            return self._private_push_enabled
        return self._group_push_enabled

    # ---------- 会话级推送配置 ----------
    def _session_setting(self, origin: str, key: str):
        """取某会话的推送配置项；未设置时返回 None。"""
        return (self._push_settings.get(origin) or {}).get(key)

    def _session_cron_hours(self, origin: str) -> list[int]:
        """取某会话的推送时段（小时列表），未设置则用全局 cron_hours。"""
        raw = self._session_setting(origin, "cron_hours")
        if not raw:
            raw = str(self._cfg("cron_hours", "8,20") or "8,20")
        return self._parse_hours(raw)

    def _session_warn_threshold(self, origin: str) -> float:
        """取某会话的低电量阈值，未设置则用全局 warn_threshold。"""
        raw = self._session_setting(origin, "warn_threshold")
        if raw is None:
            return self._warn_threshold
        try:
            return float(raw)
        except (TypeError, ValueError):
            return self._warn_threshold

    @staticmethod
    def _parse_hours(raw: str) -> list[int]:
        """解析 "8,20" / "8, 20" 为 [8, 20]，过滤非法值。"""
        hours = []
        for part in re.split(r"[,，、\s]+", str(raw or "")):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 23:
                hours.append(int(part))
        return sorted(set(hours))

    @staticmethod
    def _valid_hours(raw: str) -> bool:
        """校验时段字符串是否全部为合法小时。"""
        parts = [p.strip() for p in re.split(r"[,，、\s]+", str(raw or "")) if p.strip()]
        return bool(parts) and all(
            p.isdigit() and 0 <= int(p) <= 23 for p in parts
        )

    def _can_control_push(self, event: AstrMessageEvent) -> bool:
        """判断当前用户是否有权控制本会话推送开关。

        配置 push_control_whitelist 为空时任何绑定用户均可控制（兼容旧行为）；
        否则仅白名单用户或机器人管理员可控制。
        """
        admins = self._push_control_whitelist
        if not admins:
            return True
        if event.get_sender_id() in admins:
            return True
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        return False

    # ---------- HTTP 调用 ----------
    def _ensure_session(self) -> aiohttp.ClientSession:
        """惰性创建并复用全局 aiohttp 会话，避免每次请求重建连接池。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def _post(self, method: str, biz: dict) -> dict:
        """向云支付网关发起 POST，返回解析后的 JSON。"""
        payload = {
            "method": method,
            "bizcontent": json.dumps(biz, ensure_ascii=False),
            "timestamp": _now_ts(),
            "sourceId": 2,
        }
        async with self._ensure_session().post(self._api_url, json=payload) as resp:
            if resp.status != 200:
                raise _ApiError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
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

    # ---------- 推送白名单 ----------
    def _load_whitelist(self):
        try:
            with open(WHITELIST_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._whitelist = {str(k): bool(v) for k, v in data.items()}
        except Exception:
            self._whitelist = {}

    def _save_whitelist(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(WHITELIST_PATH, "w", encoding="utf-8") as f:
                json.dump(self._whitelist, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"[{PLUGIN_NAME}] 推送白名单保存失败: {e}")

    # ---------- 会话级推送配置 ----------
    def _load_push_settings(self):
        try:
            with open(PUSH_SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._push_settings = {
                str(k): dict(v) for k, v in data.items() if isinstance(v, dict)
            }
        except Exception:
            self._push_settings = {}

    def _save_push_settings(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(PUSH_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._push_settings, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"[{PLUGIN_NAME}] 推送配置保存失败: {e}")

    def _set_session_enabled(self, origin: str, enabled: bool):
        """设置某会话推送开关（写入会话级配置）。"""
        self._push_settings.setdefault(origin, {})["enabled"] = enabled
        self._save_push_settings()

    # ---------- 房间友好名缓存 ----------
    def _load_room_names(self):
        try:
            with open(ROOM_NAME_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._room_names = {str(k): str(v) for k, v in data.items()}
        except Exception:
            self._room_names = {}

    def _save_room_names(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(ROOM_NAME_PATH, "w", encoding="utf-8") as f:
                json.dump(self._room_names, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"[{PLUGIN_NAME}] 房间名缓存保存失败: {e}")

    def _remember_room_names(self, results: list[tuple[str, str]]):
        """记录搜房/浏览得到的 房间ID->友好名，绑定后展示友好名。"""
        changed = False
        for rid, name in results:
            if rid and self._room_names.get(rid) != name:
                self._room_names[rid] = name
                changed = True
        if changed:
            self._save_room_names()

    # ---------- 消息发送 ----------
    async def _send(self, origin: str, text: str) -> None:
        try:
            await self.context.send_message(origin, MessageChain().message(text))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 消息发送失败: {e}")

    @staticmethod
    def _args(event: AstrMessageEvent, cmd: str) -> list[str]:
        """取指令参数，兼容带/不带 / 前缀、参数与指令名粘连、中文逗号顿号分隔。

        注意：AstrBot 的 CommandFilter 已保证消息匹配该命令（命令名或命令名+空格），
        因此只需剥离命令名前缀即可。
        """
        text = (event.message_str or "").strip().lstrip("/").strip()
        rest = text[len(cmd):] if text.startswith(cmd) else ""
        tokens = [a for a in re.split(r"[\s,，、]+", rest) if a]
        # 过滤平台附加的元数据标记（如 NapCat 消息末尾的 [MSG_ID:xxx]）
        return [a for a in tokens if not re.match(r"^\[[A-Z_]+:", a)]

    # ---------- 配置检查 ----------
    def _try_auto_configure(self) -> bool:
        """从配置里的 pay_url（缴费链接）自动解析并回填核心配置。

        用户只需在管理面板粘贴缴费链接并保存；插件在初始化或收到命令时
        检测到 pay_url 尚未解析，则自动解析出 schoolcode / payproid /
        businesstype / api_url 写入配置，并清空 pay_url，避免重复解析。
        """
        if self.config is None:
            return False
        pay_url = str(self._cfg("pay_url", "") or "").strip()
        if not pay_url:
            return False
        try:
            cfg = parse_pay_url(pay_url)
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 缴费链接解析失败: {e}")
            return False
        if not cfg.get("schoolcode") or not cfg.get("payproid") or not cfg.get("api_url"):
            logger.warning(f"[{PLUGIN_NAME}] 缴费链接解析结果不完整: {cfg}")
            return False
        try:
            changed = False
            for key in ("schoolcode", "payproid", "businesstype", "api_url"):
                value = cfg[key]
                if str(self._cfg(key, "") or "") != str(value):
                    self.config[key] = value
                    changed = True
            self.config["pay_url"] = ""
            self.config.save_config()
            if changed:
                logger.info(
                    f"[{PLUGIN_NAME}] 已从缴费链接自动解析配置："
                    f"schoolcode={cfg['schoolcode']} payproid={cfg['payproid']} "
                    f"businesstype={cfg['businesstype']} api_url={cfg['api_url']}"
                )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 配置自动保存失败: {e}")
            return False
        return True

    def _config_missing(self) -> str | None:
        """检查核心配置是否齐全；缺失时返回提示文案。"""
        self._try_auto_configure()
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
            "可在 AstrBot 管理面板「插件配置」的『云支付缴费链接』中粘贴你的缴费链接并保存，"
            "插件会自动解析配置；或用仓库 tools/detect_config.py 手动获取。"
        )

    # ---------- 格式化电量 ----------
    def _format_quantity(self, state: dict, room_name: str, threshold: float | None = None) -> str:
        quantity = state.get("quantity")
        unit = state.get("quantityunit", "度")
        if threshold is None:
            threshold = self._warn_threshold
        text = f"⚡ {room_name} 剩余电量：{quantity} {unit}"
        try:
            if float(quantity) <= threshold:
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
            name = (info or {}).get("room_name") or self._room_names.get(room_id) or room_id
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
                "用法：/电量搜房 <楼栋> [楼层] [房间号]\n"
                "例如：/电量搜房 3栋 3楼 335 或 /电量搜房 3栋3楼335号"
            )
            return
        args = self._normalize_search_args(args)
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

    # ---------- 命令：递进式浏览 ----------
    @filter.command("电量浏览")
    async def cmd_browse(self, event: AstrMessageEvent):
        """递进式浏览校区/楼栋/楼层/房间：/电量浏览 [序号...]"""
        err = self._config_missing()
        if err:
            yield event.plain_result(err)
            return
        args = self._args(event, "电量浏览")
        try:
            text = await self._browse(args)
        except Exception as e:
            yield event.plain_result(f"浏览失败：{e}")
            return
        yield event.plain_result(text)

    @staticmethod
    def _cn2digit(s: str) -> str:
        """把常见中文数字替换为阿拉伯数字（学生公寓三栋 -> 学生公寓3栋）。

        支持「十」相关的两位组合：十=10、十二=12、二十=20、二十三=23。
        """
        digits = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
                  "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}

        def _to_num(m):
            inner = m.group(1)
            if "十" in inner:
                parts = inner.split("十")
                tens = digits.get(parts[0], "1")
                ones = digits.get(parts[1], "0") if len(parts) > 1 and parts[1] else "0"
                return str(int(tens + ones))
            return inner

        s = re.sub(r"([零一二三四五六七八九]?十[零一二三四五六七八九]?)", _to_num, s)
        for k, v in digits.items():
            s = s.replace(k, v)
        return s

    @staticmethod
    def _match_building(kw: str, name: str) -> bool:
        """楼栋名匹配：直接包含，或归一化数字部分一致（如「3栋」匹配「3号楼」「三栋」）。"""
        if kw in name:
            return True
        kw_digits = re.sub(r"\D", "", DormPowerCpPlugin._cn2digit(kw))
        name_digits = re.sub(r"\D", "", DormPowerCpPlugin._cn2digit(name))
        return bool(kw_digits) and kw_digits in name_digits

    @staticmethod
    def _normalize_search_args(args: list[str]) -> list[str]:
        """拆分并归一化搜房关键词。

        支持紧凑输入与常见说法：
          '3栋3楼335号'    -> ['3栋', '3层', '335']
          '3栋 3楼 335'   -> ['3栋', '3层', '335']
          '三栋 3层 335'  -> ['三栋', '3层', '335']
        """
        if len(args) == 1:
            text = args[0]
            m = re.fullmatch(
                r"([\d一二三四五六七八九十]+)栋\s*([\d一二三四五六七八九十]+)(?:楼|层)\s*(\d+)(?:号|房间|房)?",
                text,
            )
            if m:
                b, f, r = m.groups()
                args = [f"{b}栋", f"{f}层", r]
        norm = []
        for a in args:
            a = a.strip()
            a = re.sub(r"^([\d一二三四五六七八九十]+)楼$", r"\1层", a)
            a = re.sub(r"(\d+)(?:号|房)$", r"\1", a)
            norm.append(a)
        return norm

    async def _search_rooms(self, args: list[str]) -> list[tuple[str, str]]:
        """按关键词逐级下探搜索房间，返回 [(room_id, 展示名)]。

        一级关键词匹配楼栋，二级匹配楼层，三级匹配房间号。
        支持中文数字楼栋名（如「学生公寓三栋」匹配「3栋」）与紧凑输入。
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
        matched_buildings = [b for b in buildings if self._match_building(kw_building, b[3])]
        if not matched_buildings:
            kw_norm = self._cn2digit(kw_building)
            matched_buildings = [
                b for b in buildings if self._match_building(kw_norm, b[3])
            ]
        if not matched_buildings:
            return []
        if len(args) == 1:
            return [("", f"{b[2]} {b[3]}（继续加楼层关键词）") for b in matched_buildings]

        # 2. 楼层
        kw_floor = args[1].replace("楼", "层")
        floors = []
        for areaid, bid, aname, bname in matched_buildings:
            try:
                fs = await self._get_rooms(3, areaid=areaid, buildid=bid)
            except Exception:
                continue
            for f in fs:
                floors.append((areaid, bid, aname, bname, f["id"], f["name"]))
        matched_floors = [
            f for f in floors
            if kw_floor in f[5] or kw_floor.rstrip("层") in f[5]
        ]
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
        matched = [
            r for r in rooms
            if kw_room in r[1] or kw_room in r[1].replace("房间", "")
        ]
        self._remember_room_names(matched)
        return matched

    # ---------- 递进式浏览 ----------
    @staticmethod
    def _pick(items: list[dict], idx: int) -> dict:
        """按用户给的序号（从 1 开始）取出列表项，越界时抛 _PickError。"""
        if idx < 1 or idx > len(items):
            raise _PickError(f"序号 {idx} 超出范围（1~{len(items)}），请重新发送 /电量浏览。")
        return items[idx - 1]

    @staticmethod
    def _only_note(items: list) -> str:
        """当列表只有一个元素时给出提示，适配只有单个校区/单栋楼的学校。"""
        return "（仅一个）" if len(items) == 1 else ""

    async def _browse(self, args: list[str]) -> str:
        """无状态递进式浏览：校区 → 楼栋 → 楼层 → 房间。

        序号路径即状态：/电量浏览 1 2 3 表示「第1个校区 → 第2栋楼 → 第3层」。
        """
        if not args:
            areas = await self._get_rooms(1)
            if not areas:
                return "未获取到校区信息。"
            # 同名校区附上楼栋数量，方便用户区分（如两个「银屏岭校区」）
            name_counts: dict[str, int] = {}
            for a in areas:
                name_counts[a["name"]] = name_counts.get(a["name"], 0) + 1
            labels = []
            for a in areas:
                label = a["name"]
                if name_counts[a["name"]] > 1:
                    try:
                        bs = await self._get_rooms(2, areaid=a["id"])
                        label += f"（{len(bs)}栋）"
                    except Exception:
                        pass
                labels.append(label)
            lines = [f"{i + 1}. {labels[i]}{self._only_note(areas)}"
                     for i in range(len(areas))]
            return (
                "🏫 请选择校区（发送 /电量浏览 <序号>）：\n"
                + "\n".join(lines)
                + "\n示例：/电量浏览 1"
            )

        if not all(a.isdigit() for a in args):
            return "用法：/电量浏览 <序号...>，序号用空格分隔。\n例如：/电量浏览 1 2 3"
        if len(args) > 4:
            return "最多 4 级：校区 → 楼栋 → 楼层 → 房间。"

        idxs = [int(a) for a in args]
        areas = await self._get_rooms(1)
        area = self._pick(areas, idxs[0])

        if len(args) == 1:
            bs = await self._get_rooms(2, areaid=area["id"])
            lines = [f"{i + 1}. {b['name']}{self._only_note(bs)}"
                     for i, b in enumerate(bs)]
            return (
                f"已选校区：{area['name']}\n"
                "🏢 请选择楼栋（发送 /电量浏览 1 <序号>）：\n"
                + "\n".join(lines)
                + "\n示例：/电量浏览 1 1"
            )

        bs = await self._get_rooms(2, areaid=area["id"])
        building = self._pick(bs, idxs[1])

        if len(args) == 2:
            fs = await self._get_rooms(3, areaid=area["id"], buildid=building["id"])
            lines = [f"{i + 1}. {f['name']}{self._only_note(fs)}"
                     for i, f in enumerate(fs)]
            return (
                f"已选：{area['name']} / {building['name']}\n"
                "🏬 请选择楼层（发送 /电量浏览 1 {} <序号>）：\n".format(idxs[1])
                + "\n".join(lines)
                + f"\n示例：/电量浏览 1 {idxs[1]} 1"
            )

        fs = await self._get_rooms(3, areaid=area["id"], buildid=building["id"])
        floor = self._pick(fs, idxs[2])

        rs = await self._get_rooms(
            4, areaid=area["id"], buildid=building["id"], levelid=floor["id"]
        )
        rooms = [(r["id"], f"{building['name']} {floor['name']} {r['name']}")
                 for r in rs]
        self._remember_room_names(rooms)
        lines = [f"{i + 1}. {name} → {rid}" for i, (rid, name) in enumerate(rooms)]
        return (
            f"已选：{area['name']} / {building['name']} / {floor['name']}\n"
            "🛏️ 房间列表（发送 /电量绑定 <房间ID> 绑定）：\n"
            + "\n".join(lines)
        )

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
        if quantity is None:
            yield event.plain_result(
                "绑定失败：未查询到该房间的电量数据，请确认房间ID是否正确。"
            )
            return
        uid = event.get_sender_id()
        info = self._users.setdefault(uid, {})
        info["room_id"] = room_id
        info["room_name"] = self._room_names.get(room_id, room_id)
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
        """在当前会话订阅每日定时播报（同时开启本会话推送）"""
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
        self._set_session_enabled(event.unified_msg_origin, True)
        hours = self._session_cron_hours(event.unified_msg_origin)
        threshold = self._session_warn_threshold(event.unified_msg_origin)
        yield event.plain_result(
                f"✅ 已订阅每日定时播报（{','.join(str(h) for h in hours)} 点），本会话推送已开启。"
                f"电量低于 {threshold:g} 度时会额外提醒。"
            )

    # ---------- 命令：推送开关 ----------
    @filter.command("电量推送开启")
    async def cmd_push_enable(self, event: AstrMessageEvent):
        """启用当前会话（群聊/私聊）的定时推送"""
        if not self._can_control_push(event):
            yield event.plain_result("无权限：仅推送管理白名单中的用户可开启本会话推送。")
            return
        self._set_session_enabled(event.unified_msg_origin, True)
        yield event.plain_result("本会话推送已开启。")

    @filter.command("电量推送关闭")
    async def cmd_push_disable(self, event: AstrMessageEvent):
        """关闭当前会话（群聊/私聊）的定时推送"""
        if not self._can_control_push(event):
            yield event.plain_result("无权限：仅推送管理白名单中的用户可关闭本会话推送。")
            return
        self._set_session_enabled(event.unified_msg_origin, False)
        yield event.plain_result("本会话推送已关闭。")

    @filter.command("电量推送状态")
    async def cmd_push_status(self, event: AstrMessageEvent):
        """查看当前会话推送开关状态"""
        origin = event.unified_msg_origin
        private = self._session_is_private(origin)
        scope = "私聊" if private else "群聊"
        enabled = self._push_enabled_for(origin)
        settings = self._push_settings.get(origin)
        if settings and "enabled" in settings:
            mode = f"本会话已显式{'开启' if settings['enabled'] else '关闭'}"
        elif origin in self._whitelist:
            mode = f"本会话已显式{'开启' if self._whitelist[origin] else '关闭'}"
        else:
            mode = "跟随全局开关"
        global_switch = self._private_push_enabled if private else self._group_push_enabled
        hours = self._session_cron_hours(origin)
        threshold = self._session_warn_threshold(origin)
        yield event.plain_result(
            f"当前会话：{scope}\n"
            f"本会话推送：{'开启' if enabled else '关闭'}\n"
            f"设置模式：{mode}\n"
            f"推送时段：{','.join(str(h) for h in hours)} 点\n"
            f"低电量阈值：{threshold:g} 度\n"
            f"全局{scope}推送开关：{'开启' if global_switch else '关闭'}"
        )

    # ---------- 命令：会话级推送配置 ----------
    @filter.command("电量推送时段")
    async def cmd_push_hours(self, event: AstrMessageEvent):
        """设置当前会话推送时段：/电量推送时段 <小时>"""
        if not self._can_control_push(event):
            yield event.plain_result("无权限：仅推送管理白名单中的用户可修改本会话推送时段。")
            return
        args = self._args(event, "电量推送时段")
        if not args:
            yield event.plain_result(
                "用法：/电量推送时段 <小时>，如 /电量推送时段 9,21（每天 9:00 和 21:00 播报）。"
            )
            return
        raw = ",".join(args)
        if not self._valid_hours(raw):
            yield event.plain_result(f"时段格式不正确：{raw}（示例 9,21）。")
            return
        self._push_settings.setdefault(event.unified_msg_origin, {})["cron_hours"] = raw
        self._save_push_settings()
        hours = self._parse_hours(raw)
        yield event.plain_result(f"本会话推送时段已设为 {','.join(str(h) for h in hours)} 点。")

    @filter.command("电量推送阈值")
    async def cmd_push_threshold(self, event: AstrMessageEvent):
        """设置当前会话低电量预警阈值：/电量推送阈值 <度数>"""
        if not self._can_control_push(event):
            yield event.plain_result("无权限：仅推送管理白名单中的用户可修改本会话推送阈值。")
            return
        args = self._args(event, "电量推送阈值")
        if not args:
            yield event.plain_result(
                "用法：/电量推送阈值 <度数>，如 /电量推送阈值 15（电量低于 15 度时提醒）。"
            )
            return
        try:
            value = float(args[0])
        except (TypeError, ValueError):
            yield event.plain_result(f"阈值格式不正确：{args[0]}（请输入数字）。")
            return
        if value < 0:
            yield event.plain_result("阈值不能为负数。")
            return
        self._push_settings.setdefault(event.unified_msg_origin, {})["warn_threshold"] = value
        self._save_push_settings()
        yield event.plain_result(f"本会话低电量预警阈值已设为 {value:g} 度。")

    @filter.command("电量推送配置")
    async def cmd_push_config(self, event: AstrMessageEvent):
        """查看当前会话推送配置"""
        origin = event.unified_msg_origin
        hours = self._session_cron_hours(origin)
        threshold = self._session_warn_threshold(origin)
        enabled = self._push_enabled_for(origin)
        settings = self._push_settings.get(origin)
        hour_src = "会话级" if (settings or {}).get("cron_hours") else "全局"
        thr_src = "会话级" if (settings or {}).get("warn_threshold") is not None else "全局"
        yield event.plain_result(
            f"推送配置（{origin}）\n"
            f"推送开关：{'开启' if enabled else '关闭'}\n"
            f"推送时段：{','.join(str(h) for h in hours)} 点（{hour_src}）\n"
            f"低电量阈值：{threshold:g} 度（{thr_src}）"
        )

    # ---------- LLM 自然语言工具 ----------
    @filter.llm_tool(name="query_dorm_power")
    async def tool_query_dorm_power(self, event: AstrMessageEvent):
        """查询当前用户绑定的宿舍剩余电量（完美校园云支付）。

        当用户询问宿舍还剩多少电、电量情况、电费余额时调用。
        """
        err = self._config_missing()
        if err:
            yield event.plain_result(err)
            return
        uid = event.get_sender_id()
        info = self._users.get(uid)
        room_id = (info or {}).get("room_id") or str(self._cfg("default_room", ""))
        if not room_id:
            yield event.plain_result(
                "你还没有绑定宿舍房间。请先发送 /电量绑定 <房间ID> 绑定，"
                "或发送 /电量浏览 按提示找到房间。"
            )
            return
        try:
            state = await self._get_room_state(room_id)
            name = (info or {}).get("room_name") or self._room_names.get(room_id) or room_id
            yield event.plain_result(self._format_quantity(state, name))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    # ---------- 定时任务 ----------
    async def initialize(self):
        auto = self._try_auto_configure()
        hours = str(self._cfg("cron_hours", "8,20") or "8,20")
        # 每小时整点触发一次，内部按各会话的时段判断是否播报，支持会话级时段配置
        self.scheduler.add_job(self._scheduled_report, "cron", hour="*", minute=0)
        self.scheduler.start()
        logger.info(
            f"[{PLUGIN_NAME}] 插件已加载，默认播报小时: {hours}，"
            f"已绑定用户: {len(self._users)}，白名单会话: {len(self._whitelist)}，"
            f"会话级配置: {len(self._push_settings)}，"
            f"自动解析配置: {'成功' if auto else '未触发'}"
        )

    async def terminate(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=False)

    async def _scheduled_report(self):
        if self._config_missing():
            logger.warning(f"[{PLUGIN_NAME}] 核心配置未设置，跳过定时播报")
            return
        self._load_users()
        self._load_whitelist()
        self._load_push_settings()
        now_hour = datetime.now().hour
        # 按会话分组：同一会话内多人绑定同一房间只播报一次，避免重复刷屏
        by_origin: dict[str, list[dict]] = {}
        for info in self._users.values():
            origin = info.get("origin")
            room_id = info.get("room_id")
            if not origin or not room_id:
                continue
            by_origin.setdefault(origin, []).append(info)
        for origin, infos in by_origin.items():
            # 仅推送允许的会话（会话级配置 + 白名单 + 全局私聊/群聊开关）
            if not self._push_enabled_for(origin):
                continue
            # 当前小时在会话时段内才播报
            if now_hour not in self._session_cron_hours(origin):
                continue
            threshold = self._session_warn_threshold(origin)
            sent_rooms: set[str] = set()
            for info in infos:
                room_id = info["room_id"]
                if room_id in sent_rooms:
                    continue
                sent_rooms.add(room_id)
                try:
                    state = await self._get_room_state(room_id)
                    name = (info.get("room_name") or self._room_names.get(room_id) or room_id)
                    text = self._format_quantity(state, name, threshold)
                    await self._send(origin, text)
                except Exception as e:
                    logger.error(f"[{PLUGIN_NAME}] 定时播报失败: {e}")