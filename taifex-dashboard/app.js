const fmt = (n, digits = 0) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "－";
  return Number(n).toLocaleString("zh-TW", { maximumFractionDigits: digits, minimumFractionDigits: digits });
};

const signClass = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "flat";
  if (n > 0) return "up";     // 台股慣例：上漲＝紅
  if (n < 0) return "down";   // 台股慣例：下跌＝綠
  return "flat";
};

const arrow = (n) => (n > 0 ? "▲" : n < 0 ? "▼" : "－");

async function loadJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`無法讀取 ${path}`);
  return res.json();
}

function setText(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (cls) el.className = el.className.split(" ")[0] + " " + cls;
}

function renderHero(latest) {
  setText("dataDate", latest.date || "－");
  setText("fetchedAt", latest.fetched_at ? latest.fetched_at.replace("T", " ").slice(0, 16) + " UTC" : "－");

  const idx = latest.weighted_index;
  if (idx) {
    document.getElementById("idxClose").textContent = fmt(idx.close, 2);
    const cls = signClass(idx.change);
    const el = document.getElementById("idxChange");
    el.textContent = `${arrow(idx.change)} ${fmt(Math.abs(idx.change), 2)}（${fmt(idx.change_pct, 2)}%）`;
    el.className = "hero-sub " + cls;
    document.getElementById("idxTurnover").textContent = fmt(idx.turnover_billion, 2);
  }

  const tx = latest.tx_futures;
  if (tx) {
    document.getElementById("txPrice").textContent = fmt(tx.price, 0);
    const cls = signClass(tx.change);
    const el = document.getElementById("txChange");
    el.textContent = `${arrow(tx.change)} ${fmt(Math.abs(tx.change), 0)}（${fmt(tx.change_pct, 2)}%）`;
    el.className = "hero-sub " + cls;
  }
  document.getElementById("basis").textContent = fmt(latest.basis, 2);

  if (latest.pc_ratio) {
    document.getElementById("pcRatio").textContent = fmt(latest.pc_ratio.pc_ratio, 2);
    document.getElementById("vixRow").textContent =
      latest.pc_ratio.vix !== undefined && latest.pc_ratio.vix !== null
        ? `VIX ${fmt(latest.pc_ratio.vix, 2)}`
        : "VIX 暫無資料（v2加入）";
  }
}

function renderBars(containerId, items) {
  // items: [{label, value}]
  const el = document.getElementById(containerId);
  el.innerHTML = "";
  const valid = items.filter((i) => i.value !== null && i.value !== undefined && !Number.isNaN(i.value));
  if (valid.length === 0) {
    el.innerHTML = '<p class="empty">暫無資料</p>';
    return;
  }
  const maxAbs = Math.max(...valid.map((i) => Math.abs(i.value)), 1);
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    const cls = signClass(item.value);
    const pct = item.value === null || item.value === undefined ? 0 : (Math.abs(item.value) / maxAbs) * 50;
    row.innerHTML = `
      <div class="bar-name">${item.label}</div>
      <div class="bar-track">
        <div class="bar-fill ${item.value >= 0 ? "up" : "down"}" style="width:${pct}%"></div>
      </div>
      <div class="bar-value ${cls}">${item.value === null || item.value === undefined ? "－" : fmt(item.value, item.digits ?? 0)}</div>
    `;
    el.appendChild(row);
  });
}

function renderTable(tableId, rows) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = "";
  rows.forEach(([label, value, digits = 0]) => {
    const tr = document.createElement("tr");
    const cls = signClass(value);
    tr.innerHTML = `<td>${label}</td><td class="${cls}">${value === null || value === undefined ? "－" : fmt(value, digits)}</td>`;
    tbody.appendChild(tr);
  });
}

function buildLineChart(canvasId, labels, data, color) {
  const ctx = document.getElementById(canvasId);
  return new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data,
          borderColor: color,
          backgroundColor: color + "22",
          fill: true,
          tension: 0.25,
          pointRadius: 0,
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b92a3", maxTicksLimit: 6 }, grid: { color: "#1a1f28" } },
        y: { ticks: { color: "#8b92a3" }, grid: { color: "#1a1f28" } },
      },
    },
  });
}

function renderCharts(history) {
  const labels = history.map((h) => h.date);
  const idxData = history.map((h) => h.weighted_index?.close ?? null);
  const foreignData = history.map((h) => h.institutional_futures_tx?.foreign_oi_net ?? null);
  const pcData = history.map((h) => h.pc_ratio?.pc_ratio ?? null);

  buildLineChart("chartIndex", labels, idxData, "#d9a441");
  buildLineChart("chartForeign", labels, foreignData, "#e5484d");
  buildLineChart("chartPC", labels, pcData, "#2fa37a");
}

async function main() {
  try {
    const latest = await loadJSON("data/latest.json");
    renderHero(latest);

    renderBars("spotBars", [
      { label: "外資", value: latest.institutional_spot?.foreign, digits: 2 },
      { label: "投信", value: latest.institutional_spot?.trust, digits: 2 },
      { label: "自營商", value: latest.institutional_spot?.dealer, digits: 2 },
    ]);

    renderBars("futTxBars", [
      { label: "外資", value: latest.institutional_futures_tx?.foreign_oi_net },
      { label: "投信", value: latest.institutional_futures_tx?.trust_oi_net },
      { label: "自營商", value: latest.institutional_futures_tx?.dealer_oi_net },
    ]);

    renderBars("futMtxBars", [
      { label: "外資", value: latest.institutional_futures_mtx?.foreign_oi_net },
    ]);

    renderTable("largeTraderTable", [
      ["外資台指期 未平倉", latest.institutional_futures_tx?.foreign_oi_net],
      ["外資小型臺指期貨 未平倉", latest.institutional_futures_mtx?.foreign_oi_net],
      ["十大交易人 淨未平倉", latest.large_trader_futures?.top10_net],
      ["十大特定法人 淨未平倉", latest.large_trader_futures?.top10_specific_net],
    ]);

    const history = await loadJSON("data/history.json");
    renderCharts(history.history || []);
  } catch (err) {
    console.error(err);
    document.querySelector("main").innerHTML =
      '<p class="empty" style="padding:40px;text-align:center;">資料載入失敗，請確認 data/latest.json 與 data/history.json 是否存在於同一個網站目錄下。</p>';
  }
}

main();
