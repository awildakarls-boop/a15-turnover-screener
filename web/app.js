(function () {
  "use strict";

  const BOARD_ORDER = ["沪市主板", "深市主板", "创业板", "科创板", "北交所"];

  const state = {
    manifest: null,
    snapshot: null,
    selectedDate: null,
    caliber: "free", // Only free-float turnover list is displayed
    board: "",
    industry: "",
    search: "",
    sortKey: "active_tr15",
    sortDir: "desc",
    expandedCodes: new Set(),
  };

  const els = {
    deliveryBadge: document.getElementById("deliveryBadge"),
    metaTargetDate: document.getElementById("metaTargetDate"),
    metaScanStatus: document.getElementById("metaScanStatus"),
    metaUpdatedAt: document.getElementById("metaUpdatedAt"),
    statusBanner: document.getElementById("statusBanner"),
    kpiSelectedLabel: document.getElementById("kpiSelectedLabel"),
    kpiSelectedCount: document.getElementById("kpiSelectedCount"),
    kpiPoolTotal: document.getElementById("kpiPoolTotal"),
    kpiSuspended: document.getElementById("kpiSuspended"),
    kpiUnverified: document.getElementById("kpiUnverified"),
    selectDate: document.getElementById("selectDate"),
    selectBoard: document.getElementById("selectBoard"),
    selectIndustry: document.getElementById("selectIndustry"),
    inputSearch: document.getElementById("inputSearch"),
    btnResetFilters: document.getElementById("btnResetFilters"),
    mainTableTitle: document.getElementById("mainTableTitle"),
    visibleRowCount: document.getElementById("visibleRowCount"),
    totalRowCount: document.getElementById("totalRowCount"),
    thTurnover15m: document.getElementById("thTurnover15m"),
    thTurnoverDay: document.getElementById("thTurnoverDay"),
    mainTbody: document.getElementById("mainTbody"),
    emptyState: document.getElementById("emptyState"),
    rawUnverifiedSection: document.getElementById("rawUnverifiedSection"),
    rawRowCount: document.getElementById("rawRowCount"),
    rawTbody: document.getElementById("rawTbody"),
    complianceModeTag: document.getElementById("complianceModeTag"),
    licenseReasonText: document.getElementById("licenseReasonText"),
    licenseSourcesList: document.getElementById("licenseSourcesList"),
    vendorTbody: document.getElementById("vendorTbody"),
  };

  function esc(v) {
    if (v === null || v === undefined) return "";
    return String(v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtNum(v, digits = 2, suffix = "") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "缺失";
    return Number(v).toFixed(digits) + suffix;
  }

  function fmtSignedPct(v, digits = 2) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(digits)}%`;
  }

  function fmtInt(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("zh-CN");
  }

  function fmtWanShares(shares) {
    if (shares === null || shares === undefined) return "—";
    return `${(Number(shares) / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 万股`;
  }

  function fmtWanYuan(yuan) {
    if (yuan === null || yuan === undefined) return "—";
    const n = Number(yuan);
    if (Math.abs(n) >= 1e8) {
      return `${(n / 1e8).toFixed(2)} 亿元`;
    }
    return `${(n / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 万元`;
  }

  function fmtTimeShort(iso) {
    if (!iso) return "--";
    return String(iso).replace("T", " ").slice(0, 19);
  }

  function pctClass(v) {
    if (v === null || v === undefined) return "val-muted";
    const n = Number(v);
    if (n > 0) return "val-up";
    if (n < 0) return "val-down";
    return "";
  }

  async function fetchJson(url) {
    const sep = url.includes("?") ? "&" : "?";
    const bustUrl = `${url}${sep}_t=${Date.now()}`;
    const resp = await fetch(bustUrl, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
    return resp.json();
  }

  function getActiveRows() {
    if (!state.snapshot || !state.snapshot.main_lists) return [];
    return state.snapshot.main_lists[state.caliber] || [];
  }

  function getRawUnverifiedRows() {
    if (!state.snapshot || !state.snapshot.raw_unverified_lists) return [];
    return state.snapshot.raw_unverified_lists[state.caliber] || [];
  }

  function getSortValue(row, key) {
    if (key === "active_tr15") {
      return state.caliber === "normal" ? row.turnover_15m_normal : row.turnover_15m_free;
    }
    if (key === "active_trday") {
      return state.caliber === "normal" ? row.turnover_day_normal : row.turnover_day_free;
    }
    return row[key];
  }

  function renderHeaderAndBanner() {
    const m = state.manifest || {};
    const s = state.snapshot;

    els.metaTargetDate.textContent = s ? s.trade_date : (m.default_target_date || "--");
    els.metaUpdatedAt.textContent = fmtTimeShort(s ? s.generated_at : m.generated_at);

    const statusLabel = s ? s.scan_status_label : (m.pending_status === "waiting_update" ? "等待更新" : "加载中");
    els.metaScanStatus.textContent = statusLabel;

    // Delivery badge
    const mode = m.mode || "compliance_notice";
    if (mode === "authorized_real") {
      els.deliveryBadge.textContent = "真实盘后榜单 · 个人学习参考";
      els.deliveryBadge.className = "badge badge-ok";
      els.complianceModeTag.textContent = "真实数据 · 仅限个人学习参考";
      els.complianceModeTag.className = "badge badge-ok";
    } else if (mode === "private_verified_real") {
      els.deliveryBadge.textContent = "私人环境真实验算模式";
      els.deliveryBadge.className = "badge badge-info";
      els.complianceModeTag.textContent = "仅限私人本地核验";
      els.complianceModeTag.className = "badge badge-info";
    } else {
      els.deliveryBadge.textContent = "真实榜单待授权（合规演示）";
      els.deliveryBadge.className = "badge badge-warn";
      els.complianceModeTag.textContent = "公网合规模式 · 真实榜单待授权";
      els.complianceModeTag.className = "badge badge-warn";
    }

    // Banner notifications
    const bannerParts = [];
    let bannerClass = "banner";

    if (mode === "compliance_notice") {
      bannerParts.push(
        `<div><strong>公网展示合规提示（交付状态：真实榜单待授权）</strong>：受 Tushare 个人数据服务协议及交易所行情展示许可限制，当前公网快照仅展示与真实行情完全隔离的 <code>99xxxx</code> 合成验收算例（含 8%/16%/12亿/6亿 基准算例），不含未授权真实个股数据。取得合法展示许可后设置 <code>A15_PUBLISH_MODE=authorized_real</code> 即可无缝切换真实榜单。</div>`
      );
    }

    if (m.pending_date && m.pending_date !== state.selectedDate) {
      bannerClass = "banner banner-info";
      bannerParts.push(
        `<div>目标交易日 <strong>${esc(m.pending_date)}</strong> 状态：<strong>等待更新</strong>。当前正展示最近成功结果（<strong>${esc(m.latest_success_date)}</strong>）。</div>`
      );
    } else if (s && s.scan_status === "partial_coverage") {
      const f = s.funnels[state.caliber];
      bannerParts.push(
        `<div><strong>部分覆盖提示</strong>：${esc(s.trade_date)} 当前口径尚有 <strong>${f.unverified}</strong> 只未核验（如北交所缺少可分离竞价分钟源），已核验板块均严格按连续竞价统计，未核验个股已隔离。</div>`
      );
    } else if (s && s.scan_status === "waiting_update" && m.latest_success_date) {
      bannerClass = "banner banner-danger";
      bannerParts.push(
        `<div>目标交易日 ${esc(s.trade_date)} 数据尚未更新。</div>
         <div class="banner-actions">
           <button type="button" class="btn-primary-sm" id="btnJumpLatest">查看最近成功结果 (${esc(m.latest_success_date)})</button>
         </div>`
      );
    }

    if (bannerParts.length > 0) {
      els.statusBanner.innerHTML = bannerParts.join("");
      els.statusBanner.className = bannerClass;
      const jumpBtn = document.getElementById("btnJumpLatest");
      if (jumpBtn && m.latest_success_date) {
        jumpBtn.addEventListener("click", () => loadDate(m.latest_success_date));
      }
    } else {
      els.statusBanner.className = "banner hidden";
      els.statusBanner.innerHTML = "";
    }
  }

  function renderSummaryAndBoards() {
    const s = state.snapshot;
    if (!s || !s.funnels) return;

    const f = s.funnels[state.caliber];
    els.kpiSelectedLabel.textContent = "自由流通榜入选";
    els.kpiSelectedCount.textContent = fmtInt(f.selected);
    els.kpiPoolTotal.textContent = fmtInt(f.pool_total);
    els.kpiSuspended.textContent = fmtInt(f.suspended);
    els.kpiUnverified.textContent = fmtInt(f.unverified);
  }

  function populateIndustryOptions() {
    const rows = getActiveRows();
    const industries = Array.from(new Set(rows.map((r) => r.industry).filter(Boolean))).sort((a, b) =>
      a.localeCompare(b, "zh-CN")
    );
    const prev = state.industry;
    els.selectIndustry.innerHTML =
      `<option value="">全部行业 (${industries.length})</option>` +
      industries.map((ind) => `<option value="${esc(ind)}">${esc(ind)}</option>`).join("");
    if (industries.includes(prev)) {
      els.selectIndustry.value = prev;
    } else {
      state.industry = "";
      els.selectIndustry.value = "";
    }
  }

  function buildDetailRowHtml(r) {
    const oldCodeLine =
      r.old_codes && r.old_codes.length
        ? `<span class="kv-k">北交所旧代码映射</span><span class="kv-v">${esc(r.old_codes.join(", "))}</span>`
        : "";
    const websiteLink = r.website
      ? `<a href="${esc(r.website)}" target="_blank" rel="noopener noreferrer">${esc(r.website)}</a>`
      : "暂无";
    const fallbackBadge = r.business_is_latest_fallback
      ? `<span class="badge badge-info" style="margin-left:6px">最新资料 (${esc(r.business_source_date || "")})</span>`
      : "";
    const warningsHtml =
      r.warnings && r.warnings.length
        ? `<div class="excerpt-box" style="color:#92400e;background:#fffbeb"><strong>复核提示：</strong>${esc(r.warnings.join("；"))}</div>`
        : "";

    return `
      <tr class="detail-row">
        <td colspan="11">
          <div class="detail-grid">
            <div class="detail-card">
              <h4>公司主营简介与核对依据</h4>
              <div class="kv-list">
                <span class="kv-k">一句话主营</span>
                <span class="kv-v kv-v-left">${esc(r.business_summary || "待补充")}</span>
                <span class="kv-k">行业分类</span>
                <span class="kv-v kv-v-left">${esc(r.industry)}（来源：${esc(r.industry_source || "Tushare stock_basic.industry")}）</span>
                <span class="kv-k">简介来源</span>
                <span class="kv-v kv-v-left">${esc(r.business_source || "待补充")}${fallbackBadge}</span>
                <span class="kv-k">公司官网</span>
                <span class="kv-v kv-v-left">${websiteLink}</span>
                ${oldCodeLine}
              </div>
              ${r.business_raw_excerpt ? `<div class="excerpt-box"><strong>原文摘录：</strong>${esc(r.business_raw_excerpt)}</div>` : ""}
            </div>

            <div class="detail-card">
              <h4>双口径换手率与股本市值对照</h4>
              <div class="kv-list">
                <span class="kv-k">15 分钟普通换手率</span>
                <span class="kv-v">${fmtNum(r.turnover_15m_normal, 2, "%")}</span>
                <span class="kv-k">15 分钟自由流通换手率</span>
                <span class="kv-v">${fmtNum(r.turnover_15m_free, 2, "%")}</span>
                <span class="kv-k">全天普通换手率</span>
                <span class="kv-v">${fmtNum(r.turnover_day_normal, 2, "%")}</span>
                <span class="kv-k">全天自由流通换手率</span>
                <span class="kv-v">${fmtNum(r.turnover_day_free, 2, "%")}</span>
                <span class="kv-k">流通股本</span>
                <span class="kv-v">${fmtNum(r.float_share_yi, 2, " 亿股")}（${fmtNum(r.float_share_wan, 2, " 万股")}）</span>
                <span class="kv-k">自由流通股本</span>
                <span class="kv-v">${r.free_share_yi !== null ? `${fmtNum(r.free_share_yi, 2, " 亿股")}（${fmtNum(r.free_share_wan, 2, " 万股")}）` : "缺失"}</span>
                <span class="kv-k">收盘流通 / 自由市值</span>
                <span class="kv-v">${fmtNum(r.circ_mv_yi, 2, " 亿元")} / ${fmtNum(r.free_mv_yi, 2, " 亿元")}</span>
              </div>
            </div>

            <div class="detail-card">
              <h4>早盘量额与日内复盘</h4>
              <div class="kv-list">
                <span class="kv-k">09:30–09:45 成交量 / 额</span>
                <span class="kv-v">${fmtWanShares(r.v15_shares)} / ${fmtWanYuan(r.a15_yuan)}</span>
                <span class="kv-k">其中开盘集合竞价量</span>
                <span class="kv-v">${r.auction_vol_shares !== null && r.auction_vol_shares !== undefined ? fmtWanShares(r.auction_vol_shares) : "—"}</span>
                <span class="kv-k">全天成交量 / 成交额</span>
                <span class="kv-v">${fmtWanShares(r.vday_shares)} / ${fmtWanYuan(r.aday_yuan)}</span>
                <span class="kv-k">前 15 分钟成交量占全天</span>
                <span class="kv-v">${fmtNum(r.vol_ratio_15m_to_day, 2, "%")}</span>
                <span class="kv-k">当日涨跌幅</span>
                <span class="kv-v ${pctClass(r.pct_chg)}">${fmtSignedPct(r.pct_chg)}</span>
                <span class="kv-k">09:45 至收盘涨跌幅</span>
                <span class="kv-v ${pctClass(r.pct_0945_to_close)}">${fmtSignedPct(r.pct_0945_to_close)}</span>
                <span class="kv-k">K 线来源与时间</span>
                <span class="kv-v kv-v-left">${esc(r.minute_source || "")} · ${esc(r.minute_bar_time || "")}</span>
              </div>
              ${warningsHtml}
            </div>
          </div>
        </td>
      </tr>
    `;
  }

  function renderTable() {
    const allRows = getActiveRows();
    const cal = state.caliber;

    els.mainTableTitle.textContent = "自由流通换手率榜（09:30–09:45 含开盘竞价 ≥ 10%）";
    els.thTurnover15m.textContent = "15分钟自由换手 (%)";
    els.thTurnoverDay.textContent = "全天自由换手 (%)";

    // Filter
    const q = state.search.trim().toLowerCase();
    const filtered = allRows.filter((r) => {
      if (state.board && r.board !== state.board) return false;
      if (state.industry && r.industry !== state.industry) return false;
      if (q) {
        const hay = [
          r.ts_code,
          r.symbol,
          r.name,
          r.industry,
          r.business_summary,
          ...(r.old_codes || []),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    // Sort
    const sorted = filtered.slice().sort((a, b) => {
      const va = getSortValue(a, state.sortKey);
      const vb = getSortValue(b, state.sortKey);
      const dir = state.sortDir === "asc" ? 1 : -1;
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      if (typeof va === "number" && typeof vb === "number") {
        if (va !== vb) return (va - vb) * dir;
      } else {
        const cmp = String(va).localeCompare(String(vb), "zh-CN");
        if (cmp !== 0) return cmp * dir;
      }
      return String(a.ts_code).localeCompare(String(b.ts_code));
    });

    els.visibleRowCount.textContent = String(sorted.length);
    els.totalRowCount.textContent = String(allRows.length);

    // Update header sort indicators
    document.querySelectorAll("#screenerTable thead th.sortable").forEach((th) => {
      th.classList.toggle("sort-active", th.getAttribute("data-sort") === state.sortKey);
    });

    if (sorted.length === 0) {
      els.mainTbody.innerHTML = "";
      els.emptyState.classList.remove("hidden");
      if (allRows.length === 0 && state.snapshot) {
        const f = state.snapshot.funnels[cal];
        if (f.unverified === 0) {
          els.emptyState.innerHTML = `<strong>完整扫描后 0 只入选</strong>：${esc(state.snapshot.trade_date)} 全市场已核验股票在当前口径下均未达到 10% 阈值。`;
        } else {
          els.emptyState.innerHTML = `当前日期正式已核验榜单暂无入选（尚有 ${f.unverified} 只未核验）。`;
        }
      } else {
        els.emptyState.textContent = "当前筛选条件下无匹配股票，请放宽板块、行业或搜索关键词。";
      }
    } else {
      els.emptyState.classList.add("hidden");
      const htmlParts = [];
      for (const r of sorted) {
        const expanded = state.expandedCodes.has(r.ts_code);
        const tr15 = cal === "normal" ? r.turnover_15m_normal : r.turnover_15m_free;
        const trDay = cal === "normal" ? r.turnover_day_normal : r.turnover_day_free;
        const tags =
          (r.is_st ? `<span class="tag-st">ST</span>` : "") +
          (r.is_first_day ? `<span class="tag-ipo">上市首日</span>` : "");

        htmlParts.push(`
          <tr class="data-row${expanded ? " expanded" : ""}" data-code="${esc(r.ts_code)}">
            <td class="sticky-col col-code">
              <span class="expand-caret">${expanded ? "▾" : "▸"}</span>
              <span class="code-text">${esc(r.ts_code)}</span>
            </td>
            <td class="sticky-col col-name">
              <div class="name-line"><span>${esc(r.name)}</span>${tags}</div>
              <div class="biz-one-line" title="${esc(r.business_summary || "待补充")}">${esc(r.business_summary || "待补充")}</div>
            </td>
            <td>${esc(r.board)}</td>
            <td title="来源：${esc(r.industry_source || "Tushare stock_basic.industry")}">${esc(r.industry)}</td>
            <td class="num">${r.price_0945 !== null ? fmtNum(r.price_0945, 2) : "—"}</td>
            <td class="num">${fmtNum(r.close_price, 2)}</td>
            <td class="num val-strong">${fmtNum(tr15, 2, "%")}</td>
            <td class="num">${fmtNum(trDay, 2, "%")}</td>
            <td class="num" title="${fmtNum(r.float_share_wan, 2, " 万股")}">${fmtNum(r.float_share_yi, 2)}</td>
            <td class="num">${fmtNum(r.circ_mv_yi, 2)}</td>
            <td class="num">${fmtNum(r.free_mv_yi, 2)}</td>
          </tr>
        `);
        if (expanded) {
          htmlParts.push(buildDetailRowHtml(r));
        }
      }
      els.mainTbody.innerHTML = htmlParts.join("");

      els.mainTbody.querySelectorAll("tr.data-row").forEach((tr) => {
        tr.addEventListener("click", () => {
          const code = tr.getAttribute("data-code");
          if (state.expandedCodes.has(code)) {
            state.expandedCodes.delete(code);
          } else {
            state.expandedCodes.add(code);
          }
          renderTable();
        });
      });
    }

    // Raw unverified section (if any)
    const rawRows = getRawUnverifiedRows();
    if (rawRows.length > 0) {
      els.rawUnverifiedSection.classList.remove("hidden");
      els.rawRowCount.textContent = String(rawRows.length);
      els.rawTbody.innerHTML = rawRows
        .map((r) => {
          const tr15 = cal === "normal" ? r.turnover_15m_normal : r.turnover_15m_free;
          const trDay = cal === "normal" ? r.turnover_day_normal : r.turnover_day_free;
          return `
            <tr>
              <td class="sticky-col col-code"><span class="code-text">${esc(r.ts_code)}</span></td>
              <td class="sticky-col col-name">
                <div class="name-line">${esc(r.name)}</div>
                <div class="biz-one-line">${esc(r.business_summary || "待补充")}</div>
              </td>
              <td>${esc(r.board)}</td>
              <td>${esc(r.industry)}</td>
              <td class="num">${r.price_0945 !== null ? fmtNum(r.price_0945, 2) : "—"}</td>
              <td class="num">${fmtNum(r.close_price, 2)}</td>
              <td class="num">${fmtNum(tr15, 2, "%")}</td>
              <td class="num">${fmtNum(trDay, 2, "%")}</td>
              <td class="num">${fmtNum(r.float_share_yi, 2)}</td>
              <td class="num">${fmtNum(r.circ_mv_yi, 2)}</td>
              <td style="white-space:normal;max-width:260px">${esc(r.auction_handling || r.minute_source)}</td>
            </tr>
          `;
        })
        .join("");
    } else {
      els.rawUnverifiedSection.classList.add("hidden");
    }
  }

  function renderCompliance() {
    const audit = (state.manifest && state.manifest.license_audit) || null;
    if (!audit) return;
    els.licenseReasonText.textContent = audit.reason || "";

    els.licenseSourcesList.innerHTML = (audit.sources_checked || [])
      .map(
        (item) => `
        <div class="license-src-item">
          <strong>${esc(item.source)}</strong>：
          私人验算 ${item.allow_private_verify ? "✅ 允许" : "❌ 禁止"} ｜
          公网匿名展示 ${item.allow_public_anonymous ? "✅ 允许" : "❌ 不可公开"}<br/>
          <span class="val-muted">${esc(item.basis)}</span>
        </div>
      `
      )
      .join("");

    els.vendorTbody.innerHTML = (audit.alternative_vendors || [])
      .map(
        (v) => `
        <tr>
          <td><strong>${esc(v.name)}</strong></td>
          <td>${esc(v.coverage)}</td>
          <td>${esc(v.public_display_note)}</td>
          <td>${esc(v.pricing)}</td>
          <td><a href="${esc(v.entry_url)}" target="_blank" rel="noopener noreferrer">官方申请/说明</a></td>
        </tr>
      `
      )
      .join("");
  }

  async function loadDate(dateDash) {
    state.selectedDate = dateDash;
    state.expandedCodes.clear();
    els.selectDate.value = dateDash;
    try {
      state.snapshot = await fetchJson(`./data/days/${encodeURIComponent(dateDash)}.json`);
    } catch (err) {
      state.snapshot = {
        trade_date: dateDash,
        scan_status: "waiting_update",
        scan_status_label: "等待更新",
        generated_at: null,
        funnels: {
          normal: { pool_total: 0, suspended: 0, prescreen_excluded: 0, verified_not_selected: 0, selected: 0, unverified: 0 },
          free: { pool_total: 0, suspended: 0, prescreen_excluded: 0, verified_not_selected: 0, selected: 0, unverified: 0 },
        },
        board_funnels: {},
        main_lists: { normal: [], free: [] },
        raw_unverified_lists: { normal: [], free: [] },
      };
    }
    populateIndustryOptions();
    renderHeaderAndBanner();
    renderSummaryAndBoards();
    renderTable();
  }

  function bindEvents() {
    els.selectDate.addEventListener("change", (e) => loadDate(e.target.value));
    els.selectBoard.addEventListener("change", (e) => {
      state.board = e.target.value;
      renderTable();
    });
    els.selectIndustry.addEventListener("change", (e) => {
      state.industry = e.target.value;
      renderTable();
    });
    els.inputSearch.addEventListener("input", (e) => {
      state.search = e.target.value;
      renderTable();
    });
    els.btnResetFilters.addEventListener("click", () => {
      state.board = "";
      state.industry = "";
      state.search = "";
      els.selectBoard.value = "";
      els.selectIndustry.value = "";
      els.inputSearch.value = "";
      renderTable();
    });

    document.querySelectorAll("#screenerTable thead th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.getAttribute("data-sort");
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        } else {
          state.sortKey = key;
          state.sortDir = key === "ts_code" || key === "name" || key === "board" || key === "industry" ? "asc" : "desc";
        }
        renderTable();
      });
    });
  }

  async function init() {
    bindEvents();
    try {
      const manifest = await fetchJson("./data/manifest.json");
      state.manifest = manifest;
      renderCompliance();

      const dates = manifest.available_dates && manifest.available_dates.length
        ? manifest.available_dates
        : [manifest.default_target_date].filter(Boolean);

      els.selectDate.innerHTML = dates
        .map((d) => {
          const tag = d === manifest.default_target_date ? "（默认目标日 D）" : "";
          return `<option value="${esc(d)}">${esc(d)}${tag}</option>`;
        })
        .join("");

      const initialDate =
        (manifest.default_target_date && dates.includes(manifest.default_target_date)
          ? manifest.default_target_date
          : dates[0]) || manifest.latest_success_date;

      if (initialDate) {
        await loadDate(initialDate);
      }
    } catch (err) {
      els.statusBanner.className = "banner banner-danger";
      els.statusBanner.textContent = `无法加载站点快照清单 (data/manifest.json)：${err.message}`;
    }
  }

  init();
})();
