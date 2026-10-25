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
import importlib.util
import json
import os
import re
import sys
import time

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_wanmei_dorm_power"
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


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
    "1.4.0",
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
        raw = (event.message_str or "").strip().lstrip("/").strip()
        # 剥离命令名（兼容带/不带斜杠、空格、粘连）
        if raw.startswith("电量浏览"):
            raw = raw[len("电量浏览"):]
        raw = raw.strip()
        args = [a for a in re.split(r"[\s,，、]+", raw) if a]
        # 过滤命令名残留与平台附加标记（如 [MSG_ID:xxx]）
        args = [
            a for a in args
            if a not in ("电量浏览", "浏览") and not re.match(r"^\[[A-Z_]+:", a)
        ]
        try:
            text = await self._browse(args)
        except Exception as e:
            yield event.plain_result(f"浏览失败：{e}")
            return
        yield event.plain_result(text)

    @staticmethod
    def _cn2digit(s: str) -> str:
        """把中文数字替换为阿拉伯数字（学生公寓三栋 -> 学生公寓3栋）。"""
        for k, v in {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                     "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}.items():
            s = s.replace(k, v)
        return s

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
        matched_buildings = [b for b in buildings if kw_building in b[3]]
        if not matched_buildings:
            kw_norm = self._cn2digit(kw_building)
            matched_buildings = [
                b for b in buildings if kw_norm in self._cn2digit(b[3])
            ]
        if not matched_buildings:
            fuzzy = self._cn2digit(kw_building).replace("栋", "").strip()
            matched_buildings = [
                b for b in buildings
                if fuzzy and fuzzy in self._cn2digit(b[3]).replace("栋", "")
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
        return [
            r for r in rooms
            if kw_room in r[1] or kw_room in r[1].replace("房间", "")
        ]

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
        lines = [f"{i + 1}. {r['name']} → {r['id']}"
                 for i, r in enumerate(rs)]
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
            name = (info or {}).get("room_name") or room_id
            yield event.plain_result(self._format_quantity(state, name))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    # ---------- 定时任务 ----------
    async def initialize(self):
        auto = self._try_auto_configure()
        hours = str(self._cfg("cron_hours", "8,20") or "8,20")
        self.scheduler.add_job(self._scheduled_report, "cron", hour=hours, minute=0)
        self.scheduler.start()
        logger.info(
            f"[{PLUGIN_NAME}] 插件已加载，定时播报小时: {hours}，"
            f"已绑定用户: {len(self._users)}，自动解析配置: {'成功' if auto else '未触发'}"
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