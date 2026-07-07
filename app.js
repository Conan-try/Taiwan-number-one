const fmt = (n, digits = 0) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "－";
  return Number(n).toLocaleString("zh-TW", { maximumFractionDigits: digits, minimumFractionDigits: digits });
};
const signClass = (n) => { if (n === null || n === undefined) return "flat"; if (Number(n) > 0) return "up"; if (Number(n) < 0) return "down"; return "flat"; };
const arrow = (n) => (Number(n) > 0 ? "▲" : Number(n) < 0 ? "▼" : "－");
const chgStr = (n) => n == null ? "" : ` <span class="${signClass(n)}" style="font-size:0.78em">(${arrow(n)}${fmt(Math.abs(n))})</span>`;

async function loadJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`無法讀取 ${path}: ${res.status}`);
  return res.json();
}

function renderHero(latest) {
  document.getElementById("dataDate").textContent = latest.date || "－";
  if (latest.fetched_at) {
    const tw = new Date(latest.fetched_at);
    const twStr = tw.toLocaleString("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false
    }).replace(/\//g, "-");
    document.getElementById("fetchedAt").textContent = twStr + " (台灣時間)";
  } else {
    document.getElementById("fetchedAt").textContent = "－";
  }
  const idx = latest.weighted_index;
  if (idx) {
    document.getElementById("idxClose").textContent = fmt(idx.close,2);
    const el = document.getElementById("idxChange");
    el.textContent = `${arrow(idx.change)} ${fmt(Math.abs(idx.change),2)}（${fmt(idx.change_pct,2)}%）`;
    el.className = "hero-sub " + signClass(idx.change);
    document.getElementById("idxTurnover").textContent = fmt(idx.turnover_billion,2);
  }
  const tx = latest.tx_futures;
  if (tx) {
    document.getElementById("txPrice").textContent = fmt(tx.price,0);
    const el = document.getElementById("txChange");
    el.textContent = `${arrow(tx.change)} ${fmt(Math.abs(tx.change),0)}（${fmt(tx.change_pct,2)}%）`;
    el.className = "hero-sub " + signClass(tx.change);
  }
  document.getElementById("basis").textContent = fmt(latest.basis,2);
  if (latest.pc_ratio) {
    document.getElementById("pcRatio").textContent = fmt(latest.pc_ratio.pc_ratio,2);
    document.getElementById("vixRow").textContent = latest.pc_ratio.vix != null ? `VIX ${fmt(latest.pc_ratio.vix,2)}` : "VIX 暫無資料";
  }
}

function renderBars(containerId, items) {
  const el = document.getElementById(containerId); el.innerHTML = "";
  const valid = items.filter(i => i.value !== null && i.value !== undefined && !Number.isNaN(Number(i.value)));
  if (!valid.length) { el.innerHTML = '<p class="empty">暫無資料</p>'; return; }
  const maxAbs = Math.max(...valid.map(i => Math.abs(i.value)), 1);
  items.forEach(item => {
    const row = document.createElement("div"); row.className = "bar-row";
    const pct = item.value == null ? 0 : (Math.abs(item.value)/maxAbs)*50;
    row.innerHTML = `<div class="bar-name">${item.label}</div><div class="bar-track"><div class="bar-fill ${Number(item.value)>=0?"up":"down"}" style="width:${pct}%"></div></div><div class="bar-value ${signClass(item.value)}">${item.value==null?"－":fmt(item.value,item.digits??0)}</div>`;
    el.appendChild(row);
  });
}

function renderTable(tableId, rows) {
  const tbody = document.querySelector(`#${tableId} tbody`); tbody.innerHTML = "";
  rows.forEach(([label, value, digits=0, chg]) => {
    const tr = document.createElement("tr");
    const valStr = value == null ? "－" : fmt(value, digits);
    const chgHtml = chg != null ? chgStr(chg) : "";
    tr.innerHTML = `<td>${label}</td><td class="${signClass(value)}">${valStr}${chgHtml}</td>`;
    tbody.appendChild(tr);
  });
}

// 前五大/前十大交易人詳細表
function renderLargeTrader(lt) {
  const el = document.getElementById("largeTraderDetail");
  if (!el) return;
  if (!lt) { el.innerHTML = '<p class="empty">暫無資料</p>'; return; }

  const near = lt.near_month || {};
  const all  = lt.all_months  || {};

  const makeSection = (title, data) => {
    if (!data || Object.keys(data).length === 0) return "";
    return `
    <div class="lt-section">
      <div class="lt-title">${title}</div>
      <table class="data-table lt-table">
        <thead><tr><th></th><th>買方</th><th>賣方</th><th>淨額</th></tr></thead>
        <tbody>
          <tr>
            <td>前五大交易人</td>
            <td>${fmt(data.top5_buy)}${chgStr(data.top5_buy_chg)}</td>
            <td>${fmt(data.top5_sell)}${chgStr(data.top5_sell_chg)}</td>
            <td class="${signClass(data.top5_net)}">${fmt(data.top5_net)}</td>
          </tr>
          <tr>
            <td>前十大交易人</td>
            <td>${fmt(data.top10_buy)}${chgStr(data.top10_buy_chg)}</td>
            <td>${fmt(data.top10_sell)}${chgStr(data.top10_sell_chg)}</td>
            <td class="${signClass(data.top10_net)}">${fmt(data.top10_net)}</td>
          </tr>
          <tr>
            <td>前十大特定法人</td>
            <td>${fmt(data.top10_specific_buy)}${chgStr(data.top10_specific_buy_chg)}</td>
            <td>${fmt(data.top10_specific_sell)}${chgStr(data.top10_specific_sell_chg)}</td>
            <td class="${signClass(data.top10_specific_net)}">${fmt(data.top10_specific_net)}</td>
          </tr>
        </tbody>
      </table>
    </div>`;
  };

  el.innerHTML = makeSection("近月份", near) + makeSection("所有月份", all);
}

function renderTxo(txo) {
  const el = document.getElementById("txoDetail");
  if (!el) return;
  if (!txo || (!txo.dealer && !txo.foreign)) { el.innerHTML = '<p class="empty">暫無資料</p>'; return; }
  const row = (label, d) => {
    if (!d) return "";
    return `<tr>
      <td>${label}</td>
      <td class="${signClass(d.call_oi_net)}">${fmt(d.call_oi_net)}</td>
      <td class="${signClass(d.call_amt_net)}">${fmt(d.call_amt_net)}</td>
      <td class="${signClass(d.put_oi_net)}">${fmt(d.put_oi_net)}</td>
      <td class="${signClass(d.put_amt_net)}">${fmt(d.put_amt_net)}</td>
    </tr>`;
  };
  el.innerHTML = `
    <table class="data-table lt-table">
      <thead><tr><th></th><th>買權淨部位(口)</th><th>買權淨金額(千元)</th><th>賣權淨部位(口)</th><th>賣權淨金額(千元)</th></tr></thead>
      <tbody>${row("自營商", txo.dealer)}${row("外資", txo.foreign)}</tbody>
    </table>
    <p class="empty" style="margin:10px 0 0;font-size:0.72rem">賣權淨部位為正=看空避險部位增加；口數與金額為未平倉淨額</p>`;
}

function renderCharts(history) {
  if (typeof Chart === "undefined") { console.warn("Chart.js 未載入，跳過圖表"); return; }
  const labels = history.map(h => h.date);
  const make = (id, data, color) => {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    new Chart(ctx, { type:"line", data:{ labels, datasets:[{ data, borderColor:color, backgroundColor:color+"22", fill:true, tension:0.25, pointRadius:0, borderWidth:2 }]}, options:{ responsive:true, plugins:{legend:{display:false}}, scales:{ x:{ticks:{color:"#8b92a3",maxTicksLimit:6},grid:{color:"#1a1f28"}}, y:{ticks:{color:"#8b92a3"},grid:{color:"#1a1f28"}} }}});
  };
  make("chartIndex",   history.map(h=>h.weighted_index?.close??null),                    "#d9a441");
  make("chartForeign", history.map(h=>h.institutional_futures_tx?.foreign_oi_net??null), "#e5484d");
  make("chartPC",      history.map(h=>h.pc_ratio?.pc_ratio??null),                       "#2fa37a");
}

async function main() {
  try {
    const latest = await loadJSON("data/latest.json");
    renderHero(latest);
    renderBars("spotBars",[
      {label:"外資",  value:latest.institutional_spot?.foreign, digits:2},
      {label:"投信",  value:latest.institutional_spot?.trust,   digits:2},
      {label:"自營商",value:latest.institutional_spot?.dealer,  digits:2}]);
    renderBars("futTxBars",[
      {label:"外資",  value:latest.institutional_futures_tx?.foreign_oi_net},
      {label:"投信",  value:latest.institutional_futures_tx?.trust_oi_net},
      {label:"自營商",value:latest.institutional_futures_tx?.dealer_oi_net}]);
    renderBars("futMtxBars",[{label:"外資",value:latest.institutional_futures_mtx?.foreign_oi_net}]);
    renderLargeTrader(latest.large_trader_futures);
    renderTxo(latest.txo_positions);
  } catch(err) {
    console.error("主資料載入失敗:", err);
    document.querySelector("main").innerHTML = `<p class="empty" style="padding:40px;text-align:center;">資料載入失敗：${err.message}</p>`;
    return;
  }
  try {
    const history = await loadJSON("data/history.json");
    renderCharts(history.history || []);
  } catch(err) {
    console.error("圖表載入失敗:", err);
    document.querySelectorAll(".chart-card canvas").forEach(c => {
      c.style.display="none";
      c.insertAdjacentHTML("afterend","<p class='empty' style='padding:20px;text-align:center'>圖表資料累積中，明日起自動顯示</p>");
    });
  }
}

main();
