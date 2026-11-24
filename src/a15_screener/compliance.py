"""Public-display compliance policy, licensed alternative vendor catalog,
and isolated demonstration fixture (strictly separated from real unlicensed market data).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .calculator import DailyRecord, MinuteWindowResult, evaluate_universe
from .config import BJ_TZ
from .pool import PoolStock

AUTHORIZED_ALTERNATIVE_VENDORS: list[dict[str, Any]] = [
    {
        "name": "上证所信息网络有限公司（上证信息）",
        "coverage": "沪市主板、科创板（Level-1/Level-2、日K/分钟K/逐笔/集合竞价）",
        "public_display_note": "公开互联网展示需申请《上证所行情展示许可》；历史数据文件默认仅限机构内部自用，公开展示须单独签署展示许可协议。",
        "pricing": "历史日K 1万元/年、历史分钟K 2万元/年、Level-1 历史 3万元/年、Level-2 历史 12万元/年；公开展示许可按终端/平台类型另行核价。",
        "entry_url": "https://www.sseinfo.com/services/assortment/historical/",
        "auth_url": "https://www.sseinfo.com/aboutus/authstatement/",
    },
    {
        "name": "深圳证券信息有限公司（深证信）",
        "coverage": "深市主板、创业板（基础行情、增强行情、逐笔与历史数据）",
        "public_display_note": "面向公众网站或应用展示深市行情及衍生行情指标须取得深圳证券信息有限公司展示许可或通过持牌一级信息商转授权。",
        "pricing": "需企业主体向深证信提交业务场景申请并按官方价目表询价。",
        "entry_url": "https://www.szsi.cn/",
        "auth_url": "https://www.szsi.cn/",
    },
    {
        "name": "中证股转科技有限公司（北交所行情授权）",
        "coverage": "北京证券交易所（Level-1 基础行情、历史行情与展示许可）",
        "public_display_note": "北交所行情独家经营主体，公开展示需企业主体提交资质并签署许可合同。",
        "pricing": "按北交所行情许可指南提交申请后核价。",
        "entry_url": "https://www.bse.cn/application/guide.html",
        "auth_url": "https://www.bse.cn/application/guide.html",
    },
    {
        "name": "Tushare 机构/商业定制授权",
        "coverage": "全市场 A 股日线、每日指标（含自由流通股本）、公司资料及分钟/开盘竞价（需额外权限）",
        "public_display_note": "普通个人积分协议明确限定个人查看、非商业研究，禁止向匿名访客公开展示原始行情或整表分发；公网展示须联系官方洽谈机构/商业授权。",
        "pricing": "普通积分账户不支持公网展示；商业授权需邮件咨询 waditu@163.com 询价。",
        "entry_url": "https://tushare.pro/document/1?doc_id=405",
        "auth_url": "https://tushare.pro/document/1?doc_id=405",
    },
    {
        "name": "持牌行情信息商（恒生聚源 / 万得 Wind / 东方财富 Choice / 通联数据等）",
        "coverage": "全市场沪深北 A 股行情、自由流通股本、分钟K线、集合竞价及基本面",
        "public_display_note": "具备交易所一级行情转授权资质，可为企业级公网应用提供合规的公开展示 API 与数据分发许可。",
        "pricing": "商业定制合同，通常数万元至数十万元/年起，按调用量与公网展示规模询价。",
        "entry_url": "https://www.sseinfo.com/aboutus/authstatement/",
        "auth_url": "https://www.sseinfo.com/aboutus/authstatement/",
    },
]

LICENSE_AUDIT_SUMMARY: dict[str, Any] = {
    "delivery_status_public": "真实榜单待授权",
    "reason": (
        "核实《Tushare 数据服务协议》、BaoStock 免责声明及沪深北三家交易所行情授权声明："
        "当前账户使用的 Tushare 普通积分许可仅限个人查看与非商业研究，BaoStock 未经书面许可不得公开传播，"
        "沪深北交易所要求面向匿名公网访客展示个股行情及衍生筛选榜单须具备对应行情展示许可。"
        "因此本公网版本严格遵守许可边界，不对外分发未获授权的真实个股行情与真实筛选名单；"
        "获得合法展示授权后，仅需设置环境变量 A15_PUBLISH_MODE=authorized_real 并接入授权数据源即可无缝切换。"
    ),
    "sources_checked": [
        {
            "source": "Tushare Pro（普通个人账户）",
            "allow_private_verify": True,
            "allow_public_anonymous": False,
            "basis": "《Tushare 数据服务协议》(doc_id=405) 限定个人、不可转让、非商业查看与研究，禁止向第三方公开分发。",
        },
        {
            "source": "BaoStock",
            "allow_private_verify": True,
            "allow_public_anonymous": False,
            "basis": "官网免责声明限定未经书面许可不得复制、传播或用于商业用途；且不含北交所。",
        },
        {
            "source": "腾讯财经 / 东方财富 / 新浪公开网页接口",
            "allow_private_verify": True,
            "allow_public_anonymous": False,
            "basis": "无面向第三方网站的再分发/展示许可，仅用于私人环境交叉校准开盘集合竞价与北交所覆盖验证。",
        },
    ],
    "alternative_vendors": AUTHORIZED_ALTERNATIVE_VENDORS,
}


def build_isolated_demo_snapshot(demo_date_dash: str = "2026-11-23") -> dict[str, Any]:
    """Build a 100% synthetic, isolated compliance demonstration snapshot using
    non-existent synthetic ticker codes (99xxxx) so visitors can verify all UI
    interactions without exposing any unlicensed real market data.
    Includes the exact acceptance benchmark case (8% normal / 16% free / 9% day normal / 18% day free / 12亿 / 6亿).
    """
    d_yyyymmdd = demo_date_dash.replace("-", "")
    pool = [
        PoolStock(
            ts_code="990001.SH",
            symbol="990001",
            name="基准验收示例A",
            exchange="SSE",
            market="主板",
            board="沪市主板",
            industry="示例-工业自动化",
            curr_type="CNY",
            list_status="L",
            list_date="20200101",
            delist_date=None,
            is_st=False,
            is_first_day=False,
        ),
        PoolStock(
            ts_code="990002.SZ",
            symbol="990002",
            name="双榜入选示例B",
            exchange="SZSE",
            market="创业板",
            board="创业板",
            industry="示例-半导体设备",
            curr_type="CNY",
            list_status="L",
            list_date="20210615",
            delist_date=None,
            is_st=False,
            is_first_day=False,
        ),
        PoolStock(
            ts_code="990003.SH",
            symbol="990003",
            name="N首日示例C",
            exchange="SSE",
            market="科创板",
            board="科创板",
            industry="示例-生物医药",
            curr_type="CNY",
            list_status="L",
            list_date=d_yyyymmdd,
            delist_date=None,
            is_st=False,
            is_first_day=True,
        ),
        PoolStock(
            ts_code="990004.SZ",
            symbol="990004",
            name="*ST示例D",
            exchange="SZSE",
            market="主板",
            board="深市主板",
            industry="示例-基础化工",
            curr_type="CNY",
            list_status="L",
            list_date="20180301",
            delist_date=None,
            is_st=True,
            is_first_day=False,
        ),
        PoolStock(
            ts_code="990005.BJ",
            symbol="990005",
            name="北交所示例E",
            exchange="BSE",
            market="北交所",
            board="北交所",
            industry="示例-精密制造",
            curr_type="CNY",
            list_status="L",
            list_date="20230510",
            delist_date=None,
            is_st=False,
            is_first_day=False,
            old_codes=("839995.BJ",),
        ),
    ]

    daily_map = {
        # Exact acceptance test #1: float=10000w, free=5000w, close=12, V15=800w, Vday=900w (90000 lots)
        "990001.SH": DailyRecord(
            ts_code="990001.SH",
            trade_date=d_yyyymmdd,
            open_price=Decimal("11.60"),
            close_price=Decimal("12.00"),
            pre_close=Decimal("11.20"),
            pct_chg=Decimal("7.1429"),
            vol_lots=Decimal("90000"),
            amount_k=Decimal("106200"),
            float_share_w=Decimal("10000"),
            free_share_w=Decimal("5000"),
            turnover_rate=Decimal("9.0000"),
            turnover_rate_f=Decimal("18.0000"),
            circ_mv_w=Decimal("120000"),
        ),
        # Dual-list qualifying synthetic stock (15m normal 12.5%, free 20.0%)
        "990002.SZ": DailyRecord(
            ts_code="990002.SZ",
            trade_date=d_yyyymmdd,
            open_price=Decimal("24.00"),
            close_price=Decimal("26.50"),
            pre_close=Decimal("22.50"),
            pct_chg=Decimal("17.7778"),
            vol_lots=Decimal("200000"),
            amount_k=Decimal("515000"),
            float_share_w=Decimal("8000"),
            free_share_w=Decimal("5000"),
            turnover_rate=Decimal("25.0000"),
            turnover_rate_f=Decimal("40.0000"),
            circ_mv_w=Decimal("212000"),
        ),
        # Exact 10.0000% boundary synthetic IPO stock
        "990003.SH": DailyRecord(
            ts_code="990003.SH",
            trade_date=d_yyyymmdd,
            open_price=Decimal("30.00"),
            close_price=Decimal("33.00"),
            pre_close=Decimal("20.00"),
            pct_chg=Decimal("65.0000"),
            vol_lots=Decimal("120000"),
            amount_k=Decimal("384000"),
            float_share_w=Decimal("4000"),
            free_share_w=Decimal("4000"),
            turnover_rate=Decimal("30.0000"),
            turnover_rate_f=Decimal("30.0000"),
            circ_mv_w=Decimal("132000"),
        ),
        # Free share missing (normal qualifies at 11%, free missing -> shown in normal, unverified in free)
        "990004.SZ": DailyRecord(
            ts_code="990004.SZ",
            trade_date=d_yyyymmdd,
            open_price=Decimal("4.80"),
            close_price=Decimal("5.04"),
            pre_close=Decimal("4.80"),
            pct_chg=Decimal("5.0000"),
            vol_lots=Decimal("150000"),
            amount_k=Decimal("74250"),
            float_share_w=Decimal("10000"),
            free_share_w=None,
            turnover_rate=Decimal("15.0000"),
            turnover_rate_f=None,
            circ_mv_w=Decimal("50400"),
        ),
        # BSE synthetic stock (normal 14%, free 17.5%)
        "990005.BJ": DailyRecord(
            ts_code="990005.BJ",
            trade_date=d_yyyymmdd,
            open_price=Decimal("18.50"),
            close_price=Decimal("21.00"),
            pre_close=Decimal("17.50"),
            pct_chg=Decimal("20.0000"),
            vol_lots=Decimal("70000"),
            amount_k=Decimal("140000"),
            float_share_w=Decimal("2500"),
            free_share_w=Decimal("2000"),
            turnover_rate=Decimal("28.0000"),
            turnover_rate_f=Decimal("35.0000"),
            circ_mv_w=Decimal("52500"),
        ),
    }

    minute_map = {
        "990001.SH": MinuteWindowResult(
            ts_code="990001.SH",
            trade_date=d_yyyymmdd,
            status="verified_continuous",
            source="isolated_synthetic_fixture",
            bar_time=f"{d_yyyymmdd}094500000",
            raw_volume_shares=Decimal("8500000"),
            raw_amount_yuan=Decimal("100300000"),
            auction_volume_shares=Decimal("500000"),
            auction_amount_yuan=Decimal("5800000"),
            auction_source="synthetic_auction",
            continuous_volume_shares=Decimal("8000000"),
            continuous_amount_yuan=Decimal("94500000"),
            price_0945=Decimal("11.80"),
            auction_handling="隔离合成算例：已扣除开盘集合竞价 500,000 股，连续竞价 8,000,000 股",
        ),
        "990002.SZ": MinuteWindowResult(
            ts_code="990002.SZ",
            trade_date=d_yyyymmdd,
            status="verified_continuous",
            source="isolated_synthetic_fixture",
            bar_time=f"{d_yyyymmdd}094500000",
            raw_volume_shares=Decimal("10600000"),
            raw_amount_yuan=Decimal("265000000"),
            auction_volume_shares=Decimal("600000"),
            auction_amount_yuan=Decimal("14400000"),
            auction_source="synthetic_auction",
            continuous_volume_shares=Decimal("10000000"),
            continuous_amount_yuan=Decimal("250600000"),
            price_0945=Decimal("25.20"),
            auction_handling="隔离合成算例：已扣除开盘集合竞价 600,000 股",
        ),
        "990003.SH": MinuteWindowResult(
            ts_code="990003.SH",
            trade_date=d_yyyymmdd,
            status="verified_continuous",
            source="isolated_synthetic_fixture",
            bar_time=f"{d_yyyymmdd}094500000",
            raw_volume_shares=Decimal("4300000"),
            raw_amount_yuan=Decimal("134400000"),
            auction_volume_shares=Decimal("300000"),
            auction_amount_yuan=Decimal("9000000"),
            auction_source="synthetic_auction",
            continuous_volume_shares=Decimal("4000000"),
            continuous_amount_yuan=Decimal("125400000"),
            price_0945=Decimal("31.50"),
            auction_handling="隔离合成算例：恰好 10.0000% 边界测试（全精度 >= 10% 入选）",
        ),
        "990004.SZ": MinuteWindowResult(
            ts_code="990004.SZ",
            trade_date=d_yyyymmdd,
            status="verified_continuous",
            source="isolated_synthetic_fixture",
            bar_time=f"{d_yyyymmdd}094500000",
            raw_volume_shares=Decimal("11400000"),
            raw_amount_yuan=Decimal("56088000"),
            auction_volume_shares=Decimal("400000"),
            auction_amount_yuan=Decimal("1920000"),
            auction_source="synthetic_auction",
            continuous_volume_shares=Decimal("11000000"),
            continuous_amount_yuan=Decimal("54168000"),
            price_0945=Decimal("4.95"),
            auction_handling="隔离合成算例：自由股本缺失测试（普通榜正常入选，自由口径显示缺失）",
        ),
        "990005.BJ": MinuteWindowResult(
            ts_code="990005.BJ",
            trade_date=d_yyyymmdd,
            status="verified_continuous",
            source="isolated_synthetic_fixture",
            bar_time=f"{d_yyyymmdd}0931-0945",
            raw_volume_shares=Decimal("3700000"),
            raw_amount_yuan=Decimal("72150000"),
            auction_volume_shares=Decimal("200000"),
            auction_amount_yuan=Decimal("3700000"),
            auction_source="synthetic_auction",
            continuous_volume_shares=Decimal("3500000"),
            continuous_amount_yuan=Decimal("68450000"),
            price_0945=Decimal("19.80"),
            auction_handling="隔离合成算例：北交所连续竞价 09:31-09:45 示例",
        ),
    }

    summaries = {
        "990001.SH": {
            "summary": "主营工业自动化控制系统与伺服驱动产品（合成验收基准算例）。",
            "source": "隔离验收合成算例（非真实上市公司）",
            "source_date": demo_date_dash,
            "is_latest_fallback": False,
            "raw_excerpt": "验收算例 1：流通股本 10,000 万股，自由流通股本 5,000 万股，收盘价 12 元，早盘 800 万股，全天 900 万股。",
            "website": "",
        },
        "990002.SZ": {
            "summary": "主营晶圆清洗与刻蚀等半导体前道工艺设备（合成演示算例）。",
            "source": "隔离演示算例（非真实上市公司）",
            "source_date": demo_date_dash,
            "is_latest_fallback": False,
            "raw_excerpt": "演示普通榜与自由流通榜双榜入选与排序切换。",
            "website": "",
        },
        "990003.SH": {
            "summary": "主营创新抗体药物研发与临床转化服务（合成演示算例）。",
            "source": "隔离演示算例（非真实上市公司）",
            "source_date": demo_date_dash,
            "is_latest_fallback": False,
            "raw_excerpt": "演示新股首日标记及恰好 10.00% 边界入选规则。",
            "website": "",
        },
        "990004.SZ": {
            "summary": "主营精细化工中间体与特种树脂生产销售（合成演示算例）。",
            "source": "隔离演示算例（非真实上市公司）",
            "source_date": demo_date_dash,
            "is_latest_fallback": False,
            "raw_excerpt": "演示 ST 标记以及自由流通股本缺失时普通榜正常计算的容错机制。",
            "website": "",
        },
        "990005.BJ": {
            "summary": "主营高端数控精密结构件研发制造（合成演示算例）。",
            "source": "隔离演示算例（非真实上市公司）",
            "source_date": demo_date_dash,
            "is_latest_fallback": False,
            "raw_excerpt": "演示北交所新旧代码映射（839995.BJ -> 990005.BJ）及独立分钟源接入。",
            "website": "",
        },
    }

    evaluated = evaluate_universe(pool, daily_map, minute_map, summaries)
    return {
        "trade_date": demo_date_dash,
        "trade_date_yyyymmdd": d_yyyymmdd,
        "is_synthetic_demo": True,
        "publish_mode": "compliance_notice",
        "scan_status": "demo_isolated",
        "scan_status_label": "真实榜单待授权（当前为隔离合规演示算例）",
        "generated_at": datetime.now(BJ_TZ).isoformat(),
        "funnels": evaluated["funnels"],
        "board_funnels": evaluated["board_funnels"],
        "main_lists": evaluated["main_lists"],
        "raw_unverified_lists": evaluated["raw_unverified_lists"],
        "methodology": {
            "window": "09:30–09:45 连续竞价（不含 09:25 开盘集合竞价）",
            "threshold": "全精度换手率 >= 10%",
            "demo_warning": "此数据为 99xxxx 合成演示算例，与真实行情完全隔离，仅用于公网功能演示与公式验证。",
        },
    }
