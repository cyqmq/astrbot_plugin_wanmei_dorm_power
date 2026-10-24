#!/usr/bin/env python3
"""完美校园云支付宿舍电量插件 - 配置自动探测脚本。

输入：你在完美校园「缴费页」收到的链接（形如
      https://cloudpaygateway.xxx.com:端口/pay/index.html?...&customerId=1405#/billDetail?item={...}）
输出：
  1. 插件需要的核心配置（schoolcode / payproid / businesstype / api_url）
  2. 可选：交互式搜索并验证你的宿舍房间电量

用法：
  python detect_config.py "<缴费链接>"
  或直接运行后粘贴链接。

仅依赖 Python 标准库，无需安装任何第三方包。
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

GATEWAY_PATH = "/paygateway/smallpaygateway/trade"
SOURCE_ID = 2


# ---------- 云支付网关调用 ----------
def call_gateway(api_url: str, method: str, biz: dict) -> dict:
    payload = {
        "method": method,
        "bizcontent": json.dumps(biz, ensure_ascii=False),
        "timestamp": time.strftime("%y-%m-%d %H:%M:%S"),
        "sourceId": SOURCE_ID,
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": api_url.rsplit("/paygateway", 1)[0],
            "Referer": api_url.rsplit("/paygateway", 1)[0] + "/pay/index.html",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_rooms(api_url: str, schoolcode: str, payproid: str, businesstype: str,
              optype: int, areaid="0", buildid="0", unitid="0", levelid="0") -> list:
    biz = {
        "schoolno": schoolcode,
        "optype": optype,
        "payproid": payproid,
        "areaid": areaid,
        "buildid": buildid,
        "unitid": unitid,
        "levelid": levelid,
        "businesstype": businesstype,
        "idserial": "",
    }
    data = call_gateway(api_url, "samllProgramGetRoom", biz)
    if data.get("returncode") != "SUCCESS":
        raise RuntimeError(data.get("returnmsg") or data.get("subMsg") or "接口返回失败")
    return data.get("businessData") or []


def get_room_state(api_url: str, schoolcode: str, payproid: str,
                   businesstype: str, roomverify: str) -> dict:
    biz = {
        "payproid": payproid,
        "schoolcode": schoolcode,
        "roomverify": roomverify,
        "businesstype": businesstype,
    }
    data = call_gateway(api_url, "samllProgramGetRoomState", biz)
    if data.get("returncode") != "SUCCESS":
        raise RuntimeError(data.get("returnmsg") or data.get("subMsg") or "接口返回失败")
    return data.get("businessData") or {}


# ---------- 解析缴费链接 ----------
def parse_pay_url(url: str) -> dict:
    base, _, frag = url.partition("#")
    parsed = urllib.parse.urlparse(base)
    query = urllib.parse.parse_qs(parsed.query)
    host = parsed.netloc

    schoolcode = ""
    for key in ("customerId", "customercode", "schoolcode", "schoolCode"):
        if query.get(key):
            schoolcode = query[key][0]
            break

    api_url = f"https://{host}{GATEWAY_PATH}"

    payproid, businesstype, payproname = "", "", ""
    if "billDetail" in frag and "?" in frag:
        frag_query = urllib.parse.parse_qs(frag.split("?", 1)[1])
        item_str = frag_query.get("item", [""])[0]
        item = None
        for raw in (urllib.parse.unquote(item_str), item_str):
            try:
                item = json.loads(raw)
                break
            except (ValueError, TypeError):
                continue
        if isinstance(item, dict):
            payproid = str(item.get("id") or item.get("discountsTextProjectId") or "")
            businesstype = str(item.get("flag") or "")
            payproname = item.get("payproname") or ""

    return {
        "api_url": api_url,
        "schoolcode": schoolcode,
        "payproid": payproid,
        "businesstype": businesstype,
        "payproname": payproname,
    }


# ---------- 交互式房间搜索 ----------
def input_rooms(api_url: str, schoolcode: str, payproid: str, businesstype: str) -> str:
    print("\n>>> 开始搜索你的宿舍房间（可直接回车跳过）")

    areas = get_rooms(api_url, schoolcode, payproid, businesstype, 1)
    print("校区列表：", "、".join(f"{a['name']}" for a in areas) or "（无）")

    buildings = []
    for a in areas:
        try:
            bs = get_rooms(api_url, schoolcode, payproid, businesstype, 2, areaid=a["id"])
        except Exception:
            continue
        for b in bs:
            buildings.append((a["id"], b["id"], a["name"], b["name"]))

    kw_b = input("输入楼栋关键词（如 4栋）：").strip()
    if not kw_b:
        return ""
    matched_b = [b for b in buildings if kw_b in b[3]]
    if not matched_b:
        fuzzy = kw_b.replace("栋", "").strip()
        matched_b = [b for b in buildings if fuzzy and fuzzy in b[3].replace("栋", "")]
    if not matched_b:
        print("未匹配到楼栋。")
        return ""
    print("匹配楼栋：", "、".join(f"{b[2]} {b[3]}" for b in matched_b))

    kw_f = input("输入楼层关键词（如 1层）：").strip()
    floors = []
    for areaid, bid, aname, bname in matched_b:
        try:
            fs = get_rooms(api_url, schoolcode, payproid, businesstype, 3,
                           areaid=areaid, buildid=bid)
        except Exception:
            continue
        for f in fs:
            floors.append((areaid, bid, aname, bname, f["id"], f["name"]))
    matched_f = [f for f in floors if kw_f in f[5]]
    if not matched_f:
        print("未匹配到楼层。")
        return ""
    print("匹配楼层：", "、".join(f"{f[3]} {f[5]}" for f in matched_f))

    kw_r = input("输入房间号关键词（如 101）：").strip()
    rooms = []
    for areaid, bid, aname, bname, fid, fname in matched_f:
        try:
            rs = get_rooms(api_url, schoolcode, payproid, businesstype, 4,
                           areaid=areaid, buildid=bid, levelid=fid)
        except Exception:
            continue
        for r in rs:
            rooms.append((r["id"], f"{bname} {fname} {r['name']}"))
    matched_r = [r for r in rooms if kw_r in r[1]]
    if not matched_r:
        print("未匹配到房间。")
        return ""
    print("\n匹配到的房间：")
    for i, (rid, name) in enumerate(matched_r, 1):
        print(f"  {i}. {name}  →  {rid}")

    choice = input("输入序号选择房间（或直接粘贴房间ID，回车跳过）：").strip()
    if not choice:
        return ""
    if choice.isdigit() and 1 <= int(choice) <= len(matched_r):
        return matched_r[int(choice) - 1][0]
    return choice


# ---------- 主流程 ----------
def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("请粘贴你的完美校园缴费链接：\n").strip()
    if not url:
        print("未输入链接，退出。")
        return

    print("\n>>> 正在解析链接...")
    cfg = parse_pay_url(url)
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print("解析失败，以下字段为空：", "、".join(missing))
        print("请确认这是「缴费页」链接（形如 .../pay/index.html?...customerId=1405#/billDetail?item={...}）。")
        return

    print(f"\n解析结果：")
    print(f"  缴费项目名称 : {cfg['payproname']}")
    print(f"  学校代码     : {cfg['schoolcode']}")
    print(f"  缴费项目ID   : {cfg['payproid']}")
    print(f"  业务类型 flag: {cfg['businesstype']}")
    print(f"  接口地址     : {cfg['api_url']}")

    print("\n>>> 正在验证接口...")
    try:
        areas = get_rooms(cfg["api_url"], cfg["schoolcode"], cfg["payproid"],
                          cfg["businesstype"], 1)
        print(f"  验证成功！返回校区：{'、'.join(a['name'] for a in areas)}")
    except Exception as e:
        print(f"  验证失败：{e}")
        print("  请检查链接中的 host/端口、customerId、id、flag 是否填写正确。")
        return

    room_id = ""
    try:
        room_id = input_rooms(cfg["api_url"], cfg["schoolcode"], cfg["payproid"],
                                 cfg["businesstype"])
    except Exception as e:
        print(f"房间搜索失败：{e}")

    if room_id:
        try:
            state = get_room_state(cfg["api_url"], cfg["schoolcode"], cfg["payproid"],
                                   cfg["businesstype"], room_id)
            print(f"\n当前电量：{state.get('quantity')} {state.get('quantityunit', '度')}")
        except Exception as e:
            print(f"电量查询失败：{e}")

    print("\n" + "=" * 50)
    print("请把以下配置填入 AstrBot 管理面板 → 插件配置：")
    print("=" * 50)
    print(f'"schoolcode": "{cfg["schoolcode"]}"')
    print(f'"payproid": "{cfg["payproid"]}"')
    print(f'"businesstype": "{cfg["businesstype"]}"')
    print(f'"api_url": "{cfg["api_url"]}"')
    if room_id:
        print(f'"default_room": "{room_id}"')
    print("=" * 50)
    print("填好后在聊天中发送 /电量 即可查询；/电量搜房 也可重新搜索房间。")


if __name__ == "__main__":
    main()