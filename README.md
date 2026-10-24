# astrbot_plugin_wanmei_dorm_power

完美校园「云支付缴费接口」版宿舍电量查询与自动提醒 AstrBot 插件。

> 适用场景：宿舍电量不在「智能水电」页展示，而是显示在完美校园**云支付缴费页**（如 `cloudpaygateway.xxx.com/pay/index.html#/billDetail`）的学校。
> 本插件直接调用云支付网关的 `samllProgramGetRoomState` 接口查询剩余电量，**无需登录 token**。

## 安装

1. 将本目录放到 AstrBot 的 `data/plugins/astrbot_plugin_wanmei_dorm_power/`。
2. 在 AstrBot 管理面板「插件」页刷新/重载插件。
3. 获取并填写插件配置（见下）。

## 配置获取（必看）

插件核心参数是学校相关的，不同学校/不同缴费项目都不一样，**必须自行配置**：

- `schoolcode`：学校代码（云支付 `customerId`）
- `payproid`：用电缴费项目 ID（账单 item 的 `id`）
- `businesstype`：业务类型（账单 item 的 `flag`，用电一般为 `2`）
- `api_url`：云支付网关地址（`https://云支付域名/paygateway/smallpaygateway/trade`）

### 推荐方式：一键探测脚本

仓库自带 `tools/detect_config.py`，**只需粘贴一次你的缴费链接**，自动解析出全部配置，还能顺带搜索并验证你的宿舍房间电量：

```bash
cd astrbot_plugin_wanmei_dorm_power/tools
python3 detect_config.py "你的完美校园缴费链接"
```

运行后会输出类似：

```
"schoolcode": "1405"
"payproid": "6353"
"businesstype": "2"
"api_url": "https://cloudpaygateway.59wanmei.com:8087/paygateway/smallpaygateway/trade"
"default_room": "99-9--104-101"
```

把这些值填到 AstrBot 管理面板 → 插件配置即可。

### 手动方式：从缴费链接解析

1. 在完美校园打开「用电支出」缴费页，复制浏览器地址栏链接；
2. `customerId=` 参数就是 `schoolcode`；
3. `#/billDetail?item={...}` 里 JSON 的 `id` 是 `payproid`，`flag` 是 `businesstype`；
4. `api_url = https://链接的域名/paygateway/smallpaygateway/trade`。

更详细的抓包兜底方法见 `docs/获取配置教程.md`。

## 插件配置项

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `schoolcode` | 学校代码（云支付 `customerId`） | 空（必须配置） |
| `payproid` | 用电缴费项目 ID（账单 item 的 `id`） | 空（必须配置） |
| `businesstype` | 业务类型（账单 item 的 `flag`） | `2` |
| `api_url` | 云支付网关地址 | 空（必须配置） |
| `default_room` | 默认房间 ID（如 `99-9--104-101`） | 空（可用命令绑定） |
| `warn_threshold` | 低电量预警阈值（度） | `20` |
| `cron_hours` | 每日定时播报小时 | `8,20` |

## 使用

```
/电量                     查询已绑定房间的剩余电量
/电量搜房 4栋 1层 101      搜索房间（支持 1~3 级关键词，逐步缩小范围）
/电量绑定 99-9--104-101   绑定房间（房间ID用 /电量搜房 获取）
/电量解绑                 清空自己的绑定
/电量推送                 在当前会话订阅每日定时播报
```

示例：

1. `/电量搜房 4栋` → 返回匹配的楼栋，提示继续加楼层；
2. `/电量搜房 4栋 1层` → 返回匹配的楼层，提示继续加房间号；
3. `/电量搜房 4栋 1层 101` → 返回房间及对应 ID（如 `99-9--104-101`）；
4. `/电量绑定 99-9--104-101` → 绑定成功并显示当前电量；
5. `/电量推送` → 开启每日定时播报，低于阈值自动提醒。

## 数据来源与原理

- 接口：`POST {api_url}`
- 请求体：`{"method": "...", "bizcontent": "{...}", "timestamp": "...", "sourceId": 2}`
- 房间级联：`samllProgramGetRoom`（optype: 1校区 → 2楼栋 → 3楼层 → 4房间）
- 电量查询：`samllProgramGetRoomState`（参数 `payproid`/`schoolcode`/`roomverify`/`businesstype`）
- 返回：`businessData.quantity`（剩余电量）+ `businessData.quantityunit`（单位，如「度」）

用户绑定数据保存在 `data/plugin_data/astrbot_plugin_wanmei_dorm_power/users.json`，仅含房间 ID 与会话来源，不含任何密码/凭证。

## 声明

- 接口为完美校园私有协议，随时可能变更；若失效需重新抓包确认请求参数。
- 本插件仅用于查询自己宿舍的电量，请勿用于任何未授权用途。