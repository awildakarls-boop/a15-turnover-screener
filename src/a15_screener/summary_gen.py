"""Generate natural one-sentence Chinese main-business descriptions with source provenance."""

from __future__ import annotations

import re
from typing import Any

# Patterns typical of statutory registration business scope (not real main business)
SCOPE_BOILERPLATE_RE = re.compile(
    r"(一般项目[:：]|许可项目[:：]|依法须经批准的项目|经相关部门批准后方可开展经营活动|除依法须经批准的项目外)"
)

LEADING_NOISE_RE = re.compile(
    r"^(本公司|公司|本集团|集团|发行人)?(主要|主营|系|是|致力于|专注于|专业)?(从事|开展|经营|生产和销售|研发、生产和销售|研发、生产及销售)?"
)


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    s = str(text).strip()
    if s.lower() in {"nan", "none", "null"}:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s


def _condense_sentence(text: str, max_len: int = 52) -> str:
    """Distill a clean natural Chinese one-sentence summary of products/services."""
    # Take first meaningful clause/sentence
    text = text.replace("\r", " ").replace("\n", " ").strip()
    # Remove redundant company prefix like "公司是一家..."
    text = re.sub(r"^(本公司|公司|本集团|发行人)?(主要|一直|长期)?(是一家|系一家)?", "", text).strip()
    # Split by Chinese period / semicolon
    parts = [p.strip(" ，,、;；。") for p in re.split(r"[。；;]", text) if p.strip(" ，,、;；。")]
    chosen = ""
    for p in parts:
        # Skip boilerplate sentences like stock listing date or registration address
        if any(k in p for k in ["成立", "注册资本", "上市", "股票代码", "前身为", "位于", "控股股东"]):
            continue
        chosen = p
        break
    if not chosen and parts:
        chosen = parts[0]

    # Normalize phrasing to natural Chinese
    chosen = re.sub(r"^(主要业务为|主营业务为|主营业务是|主要从事|从事|主营|主要产品及服务为|主要产品为|主要产品包括)[:：]?", "", chosen).strip()
    if not chosen:
        return ""

    if len(chosen) > max_len:
        # Cut cleanly at punctuation near max_len
        cut = chosen[:max_len]
        for sep in ["、", "，", ",", "及", "和"]:
            idx = cut.rfind(sep)
            if idx >= max_len // 2:
                cut = cut[:idx]
                break
        chosen = cut.rstrip("、，,及和的") + "等"

    if not chosen.startswith(("主营", "提供", "专注", "从事", "研发", "生产", "经营", "涵盖")):
        chosen = f"主营{chosen}"
    if not chosen.endswith("。"):
        chosen += "。"
    return chosen


def build_business_summary(
    company_row: dict[str, Any] | None,
    target_date_dash: str,
    fetched_date_dash: str,
) -> dict[str, Any]:
    """Prioritize stock_company.main_business -> introduction -> fallback '待补充'.
    Explicitly refuses to guess from statutory business_scope or company name alone.
    """
    is_latest_fallback = target_date_dash < fetched_date_dash

    if not company_row:
        return {
            "summary": "待补充",
            "source": "资料缺失",
            "source_date": None,
            "is_latest_fallback": False,
            "raw_excerpt": "",
            "website": "",
        }

    website = _clean_text(company_row.get("website"))
    if website and not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    main_business = _clean_text(company_row.get("main_business"))
    introduction = _clean_text(company_row.get("introduction"))

    # 1. Prefer main_business (provided it's not pure registration scope boilerplate)
    if main_business and not SCOPE_BOILERPLATE_RE.search(main_business[:30]):
        summary = _condense_sentence(main_business)
        if summary:
            src_label = f"Tushare stock_company.main_business"
            if is_latest_fallback:
                src_label += f"（最新资料，抓取日 {fetched_date_dash}）"
            return {
                "summary": summary,
                "source": src_label,
                "source_date": fetched_date_dash,
                "is_latest_fallback": is_latest_fallback,
                "raw_excerpt": main_business[:280],
                "website": website,
            }

    # 2. Fallback to introduction
    if introduction and not SCOPE_BOILERPLATE_RE.search(introduction[:30]):
        summary = _condense_sentence(introduction)
        if summary:
            src_label = f"Tushare stock_company.introduction"
            if is_latest_fallback:
                src_label += f"（最新资料，抓取日 {fetched_date_dash}）"
            return {
                "summary": summary,
                "source": src_label,
                "source_date": fetched_date_dash,
                "is_latest_fallback": is_latest_fallback,
                "raw_excerpt": introduction[:280],
                "website": website,
            }

    # 3. Missing (do NOT fabricate from registration business_scope or company name)
    return {
        "summary": "待补充",
        "source": f"待补充（公司官网/年报核验）" + (f" · {website}" if website else ""),
        "source_date": fetched_date_dash,
        "is_latest_fallback": is_latest_fallback,
        "raw_excerpt": "",
        "website": website,
    }
