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

### 推荐方式一：配置页粘贴链接，保存后自动解析

1. 在 AstrBot 管理面板打开本插件的「插件配置」；
2. 在第一个输入框「云支付缴费链接」中粘贴你的完美校园缴费页链接；
3. 点击保存，插件会自动解析并回填 `schoolcode` / `payproid` / `businesstype` / `api_url`。

无需手动填写任何学校参数。

### 推荐方式二：一键探测脚本

仓库自带 `tools/detect_config.py`，**只需粘贴一次你的缴费链接**，自动解析出全部配置，还能顺带搜索并验证你的宿舍房间电量：

```bash
cd astrbot_plugin_wanmei_dorm_power/tools
python3 detect_config.py "你的完美校园缴费链接"
```

运行后会输出类似：

```
"schoolcode": "<学校代码>"
"payproid": "<缴费项目ID>"
"businesstype": "2"
"api_url": "https://<你的云支付域名>/paygateway/smallpaygateway/trade"
"default_room": "<房间ID>"
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
| `pay_url` | 云支付缴费链接（粘贴保存后自动解析并清空） | 空 |
| `schoolcode` | 学校代码（云支付 `customerId`） | 空（可自动解析） |
| `payproid` | 用电缴费项目 ID（账单 item 的 `id`） | 空（可自动解析） |
| `businesstype` | 业务类型（账单 item 的 `flag`） | `2` |
| `api_url` | 云支付网关地址 | 空（可自动解析） |
| `default_room` | 默认房间 ID（用 /电量搜房 获取） | 空（可用命令绑定） |
| `warn_threshold` | 低电量预警阈值（度） | `20` |
| `cron_hours` | 每日定时播报小时 | `8,20` |
| `private_push_enabled` | 是否允许私聊会话接收定时推送 | `true` |
| `group_push_enabled` | 是否允许群聊会话接收定时推送 | `true` |
| `push_control_whitelist` | 可控制推送开关的用户 ID（逗号分隔） | 空 |

## 使用

**自然语言查询**（需 AstrBot 已配置 LLM）：

```
@猫猫 宿舍还有多少电
```

LLM 会自动调用插件的 `query_dorm_power` 工具，返回已绑定房间的剩余电量。

**指令方式**：

```
/电量                     查询已绑定房间的剩余电量
/电量搜房 4栋 1层 101      搜索房间（支持 1~3 级关键词，逐步缩小范围）
/电量浏览 [序号...]        递进式浏览 校区→楼栋→楼层→房间
/电量绑定 <房间ID>   绑定房间（房间ID用 /电量搜房 或 /电量浏览 获取）
/电量解绑                 清空自己的绑定
/电量推送                 在当前会话订阅每日定时播报
/电量推送开启             启用当前会话（群聊/私聊）的定时推送
/电量推送关闭             关闭当前会话（群聊/私聊）的定时推送
/电量推送状态             查看当前会话推送开关状态
/电量推送时段 <小时>       设置当前会话推送时段（如 9,21）
/电量推送阈值 <度数>       设置当前会话低电量预警阈值（如 15）
/电量推送配置             查看当前会话推送配置
```

### 推送白名单与开关

- `/电量推送` 订阅推送时，会把当前会话加入推送白名单（显式开启）。
- `/电量推送开启` / `/电量推送关闭` 可单独启用或关闭**当前群或当前私聊**的推送，
  不影响其他会话；`/电量推送状态` 可查看当前会话生效的开关。
- 未显式设置的会话，跟随全局配置 `private_push_enabled`（私聊）与
  `group_push_enabled`（群聊）。例如把 `group_push_enabled` 关闭后，只有
  用 `/电量推送开启` 单独开启过的群才会收到推送。

### 会话级推送配置

- `/电量推送时段 <小时>` 单独设置某群/私聊的播报时段（如 `9,21`），
  未设置的会话使用全局 `cron_hours`。
- `/电量推送阈值 <度数>` 单独设置某群/私聊的低电量预警阈值，未设置的会话使用
  全局 `warn_threshold`。
- `/电量推送配置` 可查看当前会话生效的时段/阈值及其来源（会话级或全局）。

### 推送控制权限

- 配置项 `push_control_whitelist` 填写可控制推送开关的用户 ID（逗号分隔），
  留空表示任何绑定用户都可控制（兼容旧行为）。
- 配置后，仅白名单中的用户及机器人管理员可执行 `/电量推送开启`、
  `/电量推送关闭`、`/电量推送时段`、`/电量推送阈值`，普通成员会收到无权限提示。

示例（搜房）：

1. `/电量搜房 4栋` → 返回匹配的楼栋，提示继续加楼层；
2. `/电量搜房 4栋 1层` → 返回匹配的楼层，提示继续加房间号；
3. `/电量搜房 4栋 1层 101` → 返回房间及对应 ID（如 `<房间ID>`）；
4. `/电量绑定 <房间ID>` → 绑定成功并显示当前电量；
5. `/电量推送` → 开启每日定时播报，低于阈值自动提醒。

示例（浏览，适用于不知道楼栋怎么叫的情况）：

1. `/电量浏览` → 列出所有校区（同名校区会附上楼栋数量）；
2. `/电量浏览 4` → 选择第 4 个校区，列出楼栋；
3. `/电量浏览 4 3` → 选择第 3 栋楼，列出楼层；
4. `/电量浏览 4 3 3` → 列出该层所有房间及房间 ID；
5. `/电量绑定 <房间ID>` → 绑定房间。

## 数据来源与原理

- 接口：`POST {api_url}`
- 请求体：`{"method": "...", "bizcontent": "{...}", "timestamp": "...", "sourceId": 2}`
- 房间级联：`samllProgramGetRoom`（optype: 1校区 → 2楼栋 → 3楼层 → 4房间）
- 电量查询：`samllProgramGetRoomState`（参数 `payproid`/`schoolcode`/`roomverify`/`businesstype`）
- 返回：`businessData.quantity`（剩余电量）+ `businessData.quantityunit`（单位，如「度」）

用户绑定数据保存在 `data/plugin_data/astrbot_plugin_wanmei_dorm_power/users.json`，仅含房间 ID 与会话来源，不含任何密码/凭证。

## 常见问题

### 从 GitHub 链接安装时报「插件校验失败」？

通常是运行 AstrBot 的服务器无法访问 `raw.githubusercontent.com`（国内网络常见），
日志里会看到 `Plugin repository validation failed ...:`（冒号后为空）。解决办法：

1. **使用 GitHub 加速代理**（推荐）：
   在 AstrBot 管理面板 → 插件 →「从链接安装」时，插件地址填
   `https://github.com/cyqmq/astrbot_plugin_wanmei_dorm_power`，
   代理地址填 `https://ghfast.top` 或 `https://ghproxy.net`，再点击校验/安装。

2. **ZIP 上传安装**：
   下载插件压缩包
   `https://github.com/cyqmq/astrbot_plugin_wanmei_dorm_power/archive/refs/heads/main.zip`，
   然后在 AstrBot 插件页使用「上传插件」选择该 ZIP 安装。

## 声明

- 接口为完美校园私有协议，随时可能变更；若失效需重新抓包确认请求参数。
- 本插件仅用于查询自己宿舍的电量，请勿用于任何未授权用途。