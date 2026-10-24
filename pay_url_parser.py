"""解析完美校园云支付「缴费链接」的公共模块（仅依赖标准库）。

供以下场景共用：
1. AstrBot 插件 main.py —— 配置页粘贴缴费链接后自动解析回填配置；
2. tools/detect_config.py —— 命令行一键探测脚本。
"""
import json
import urllib.parse

GATEWAY_PATH = "/paygateway/smallpaygateway/trade"


def parse_pay_url(url: str) -> dict:
    """从云支付缴费链接解析插件核心配置。

    支持形如：
      https://cloudpaygateway.<学校域名>:<端口>/pay/index.html?openId=...&token=...&customerId=1405#/billDetail?item={...}

    返回字段：api_url / schoolcode / payproid / businesstype / payproname。
    """
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