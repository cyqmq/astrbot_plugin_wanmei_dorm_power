# 更新日志

本项目版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [v1.1.1] - 2026-10-24

### 修复
- 修复 `/电量搜房` 报错 `Attempt to decode JSON with unexpected mimetype: text/plain;charset=utf-8`：云支付接口返回的 Content-Type 为 `text/plain`（内容实为 JSON），改用 `resp.json(content_type=None)` 忽略类型检查

## [v1.1.0] - 2026-10-24

### 新增
- 配置页新增「云支付缴费链接」输入项：粘贴缴费链接并保存后，插件自动解析并回填 `schoolcode` / `payproid` / `businesstype` / `api_url`
- 新增公共解析模块 `pay_url_parser.py`，插件与 `tools/detect_config.py` 共用同一套解析逻辑

### 变更
- 插件描述与版本号更新至 v1.1.0
- README 补充配置页自动解析的使用说明

## [v1.0.0] - 2026-10-24

### 新增
- 首个版本：通过完美校园云支付缴费接口（`/paygateway/smallpaygateway/trade`）查询宿舍剩余电量，无需登录 token
- 命令：`/电量`、`/电量搜房`、`/电量绑定`、`/电量解绑`、`/电量推送`
- 每日定时播报（默认 8:00 / 20:00）与低电量预警（默认阈值 20 度）
- 附带 `tools/detect_config.py` 一键解析缴费链接的探测脚本
- 附带 `docs/获取配置教程.md` 配置获取教程

### 修复
- 将文档与示例中的真实学校配置替换为占位符，避免敏感信息泄露

### 文档
- README 补充「从 GitHub 链接安装时报插件校验失败」的代理与 ZIP 上传解决方法