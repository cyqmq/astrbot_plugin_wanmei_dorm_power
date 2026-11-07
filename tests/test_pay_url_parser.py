"""pay_url_parser 单元测试。"""
import json
import urllib.parse

from pay_url_parser import parse_pay_url


def _make_url(customer="1405", item=None):
    frag_item = urllib.parse.quote(json.dumps(item)) if item else ""
    return (
        f"https://cloudpaygateway.example.com:8443/pay/index.html"
        f"?openId=abc&token=t&customerId={customer}"
        f"#/billDetail?item={frag_item}"
    )


def test_parse_typical_url():
    item = {"id": "123", "flag": "2", "payproname": "用电"}
    cfg = parse_pay_url(_make_url(item=item))
    assert cfg["schoolcode"] == "1405"
    assert cfg["payproid"] == "123"
    assert cfg["businesstype"] == "2"
    assert cfg["payproname"] == "用电"
    assert cfg["api_url"] == (
        "https://cloudpaygateway.example.com:8443/paygateway/smallpaygateway/trade"
    )


def test_parse_fallback_customer_keys():
    url = "https://cp.example.com/pay/index.html?customercode=777#/billDetail?item={}"
    cfg = parse_pay_url(url)
    assert cfg["schoolcode"] == "777"


def test_parse_missing_item_fields():
    cfg = parse_pay_url("https://cp.example.com/pay/index.html?customerId=1#/billDetail?item=x")
    # item 不是合法 JSON 时，相关字段为空，但 schoolcode/api_url 仍可解析
    assert cfg["schoolcode"] == "1"
    assert cfg["payproid"] == ""
    assert cfg["businesstype"] == ""


def test_parse_url_without_fragment():
    cfg = parse_pay_url("https://cp.example.com/pay/index.html?customerId=42")
    assert cfg["schoolcode"] == "42"
    assert cfg["payproid"] == ""
    assert cfg["api_url"].startswith("https://cp.example.com/paygateway/")


def test_api_url_uses_host_and_port():
    cfg = parse_pay_url("https://h.example.com:9443/pay/index.html?customerId=9")
    assert cfg["api_url"] == "https://h.example.com:9443/paygateway/smallpaygateway/trade"
