# Changelog

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-26

首个版本。基于 2026 年的规则与实测。

### Added

- **六道闸自查体系**：名称 / 服务类目 / ICP 备案 / 隐私 / 内容安全 / 提审节奏
- `docs/00-quick-checklist.md` — 一页纸打勾版清单
- `docs/01-naming.md` — 两种驳回理由逐字对照、3 种能过的命名结构、改名额度机制
- `docs/02-categories.md` — 个人主体全类目清单、图片处理与备忘录的边界条款、5 槽占满处置
- `docs/03-icp-filing.md` — 备案九步流程、真实驳回复盘、三套备注模板与禁词表
- `docs/04-privacy.md` — 隐私接口清单、`scope not declared` 真因、后台填表模板
- `docs/05-content-safety.md` — 5.18 五项永久限制、平台三个「不看」、三条解法与成本测算
- `docs/06-review-process.md` — 实测时长、提审表单 7 字段、截图规范
- `docs/07-official-clauses.md` — 40+ 条官方条款索引
- `scripts/precheck.py` — 静态预检脚本，支持 app.json 与 uni-app pages.json
- `examples/sample-report.md` — 真实运行输出的报告样例

### Notes

- 明确记录 `__usePrivacyCheck__` 自 2023-10-17 后失效，纠正广泛流传的错误做法
- 明确记录个人主体加急审核为 **1 次/年**（非个人主体才是 3 次）
- 明确记录「本机处理、不上传服务器」不构成 5.18 豁免
