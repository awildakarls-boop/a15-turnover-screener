# A 股开盘 15 分钟换手率筛选站

盘后只读静态快照架构的中文筛选网站，按北京时间（`Asia/Shanghai`）默认展示严格早于今日的最近开市日（D）连续竞价 `09:30–09:45`（扣除 `09:25` 开盘集合竞价）换手率 `>= 10%` 的 A 股名单，支持**普通换手率**与**自由流通换手率**双口径切换、全板块漏斗对账与公司主营核验。

## 目录结构

```text
├── src/a15_screener/          # 核心采集、股票池、竞价校准、全精度计算、合规发布模块
├── scripts/
│   ├── probe_sources.py       # Tushare / BaoStock 接口权限与覆盖探测
│   ├── verify_auction_calibration.py # 09:30-09:45 时间边界与开盘集合竞价交叉校准
│   ├── run_daily.py           # 单日采集（含跨进程文件锁、断点续跑、原子发布）
│   ├── backfill.py            # 多交易日历史回补
│   ├── export_public.py       # 按合规模式导出 web/data/ 公开快照
│   ├── audit_snapshot.py      # 单日快照全链路对账与样本验算输出
│   ├── serve_local.py         # 本地只读预览服务（private / public 双模式，不调行情源）
│   └── verify_ui.py           # Playwright 桌面端 + 手机端端到端交互验收
├── web/                       # 纯静态响应式前端（HTML/CSS/JS + data/ 快照）
├── tests/test_acceptance.py   # 与正式数据完全隔离的验收单元测试套件
├── docs/probe_and_audit_report.md # 接口实测、竞价校准与 2026-11-23 真实验算报告
├── .github/workflows/daily-update.yml # GitHub Actions 定时采集（20:30 / 次日 07:30 CST）与 Pages 发布
├── .env.example               # 仅含变量名的配置示例（不含密钥）
└── requirements.txt
```

## 快速启动与常用命令

### 1. 安装依赖与配置环境变量
```bash
pip install -r requirements.txt
# 仅在运行环境设置 TUSHARE_TOKEN（切勿提交到 Git）
# PowerShell:
$env:TUSHARE_TOKEN="你的Token"
```

### 2. 运行隔离验收测试
```bash
pytest -v
```

### 3. 采集默认目标交易日 D 或指定历史日
```bash
# 采集默认目标交易日（严格早于北京今日的最近开市日）
python scripts/run_daily.py

# 采集指定交易日（支持断点续跑与幂等重跑）
python scripts/run_daily.py --date 20261123

# 回补最近 3 个已收盘交易日
python scripts/backfill.py --days 3
```

### 4. 本地只读浏览与验算
```bash
# 私人环境浏览本地真实全量验算榜单（读取 data/private/，仅监听 127.0.0.1）
python scripts/serve_local.py --mode private --port 8765

# 浏览公网合规快照（读取 web/data/，与公网发布完全一致）
python scripts/serve_local.py --mode public --port 8766

# 执行桌面端(1440x900) + 移动端(390x844) 端到端自动验收
python scripts/verify_ui.py
```

### 5. 公网发布模式切换
- 默认 `A15_PUBLISH_MODE=compliance_notice`：仅将合规说明与与真实行情完全隔离的 `99xxxx` 合成验收算例写入 `web/data/`，严防向匿名公网泄露未授权行情数据。
- 当您取得交易所/持牌数据商的合法公开展示授权后，仅需切换：
```bash
python scripts/export_public.py --mode authorized_real
```

## 云端定时更新与固定 HTTPS 部署（零基础设施成本方案）

本项目采用纯静态快照 + GitHub Actions 定时任务架构，推荐托管在 **GitHub Pages + GitHub Actions**（公开仓库运行成本 **¥0 / 月**）或 **Cloudflare Pages / Netlify**：

1. 在 GitHub 新建仓库并推送当前代码（`.gitignore` 已严格排除 `.env`、`data/private/`、`data/state.db`）。
2. 在仓库 `Settings -> Secrets and variables -> Actions` 中添加 Secret：`TUSHARE_TOKEN`；可选在 `Variables` 中配置 `A15_PUBLISH_MODE`（未获公开展示授权前保持 `compliance_notice`）。
3. 在仓库 `Settings -> Pages` 中将 `Source` 设为 **GitHub Actions**。
4. 工作流 `.github/workflows/daily-update.yml` 已配置：
   - 北京时间交易日 **20:00**（`0 12 * * 1-5` UTC）自动采集并更新当日数据；
   - 北京时间交易日 **20:40**（`40 12 * * 1-5` UTC）自动复查补齐（若 20:00 尚有延迟则自动补全，已完整则复用缓存快速完成）；
   - 支持在 Actions 页面手动触发 `workflow_dispatch` 指定日期回补；
   - 使用 `concurrency` 互斥锁防止并发冲突，自动验证云端主机对 BaoStock 的连通性，并扫描 `web/` 确保无 Token 泄露后自动部署至固定 HTTPS 地址 `https://<username>.github.io/<repo>/`。

## 数据公开展示许可核查与运行成本

- **当前交付状态（公网）**：`真实榜单待授权`
- **核查依据**：
  - Tushare 普通积分账户受《Tushare 数据服务协议》(doc_id=405) 约束，仅限个人、非商业查看与研究；
  - BaoStock 免责声明限定未经书面许可不得公开传播；
  - 沪深北三家交易所（上证信息、深证信、中证股转科技）明确要求面向公众互联网展示个股行情及衍生榜单须取得相应行情展示许可。
- **运行成本**：
  - 静态网站托管 + 云端定时采集（GitHub Pages + GitHub Actions / Cloudflare Pages）：**¥0 / 月**（免费额度充足）。
  - 若需向匿名公众合规展示真实榜单，须向交易所信息公司或持牌一级信息商申请商业展示许可（上证信息历史分钟 K 参考 2 万元/年、Level-1 历史 3 万元/年、公网展示许可按平台类型另行核价；具体入口详见页面底部及 `src/a15_screener/compliance.py`）。
