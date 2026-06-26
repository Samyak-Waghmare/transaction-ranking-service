/* Frontend logic for the Transaction & Ranking demo.
 * Pure vanilla JS, no build step. Talks to the backend over fetch().
 */
(function () {
  "use strict";

  const LS_KEY = "trs.apiBase";
  const $ = (sel) => document.querySelector(sel);

  // ---- API base URL resolution: localStorage > config.js default ----
  let apiBase = (localStorage.getItem(LS_KEY) || window.__API_BASE__ || "").trim();

  const els = {
    apiBase: $("#apiBase"),
    saveApi: $("#saveApi"),
    status: $("#status"),
    docsLink: $("#docsLink"),
    txForm: $("#txForm"),
    txId: $("#txId"),
    genId: $("#genId"),
    resendBtn: $("#resendBtn"),
    txOut: $("#txOut"),
    sumForm: $("#sumForm"),
    sumOut: $("#sumOut"),
    rankBtn: $("#rankBtn"),
    rankBody: $("#rankBody"),
    weights: $("#weights"),
    seedBtn: $("#seedBtn"),
    resetBtn: $("#resetBtn"),
    refreshAllBtn: $("#refreshAllBtn"),
    toast: $("#toast"),
  };

  // ---------------------------------------------------------------- utils
  function normalizeBase(url) {
    return url.replace(/\/+$/, ""); // strip trailing slashes
  }

  function toast(msg, kind) {
    els.toast.textContent = msg;
    els.toast.className = "toast show " + (kind || "");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (els.toast.className = "toast"), 3200);
  }

  function setStatus(state, text) {
    els.status.className = "status status--" + state;
    els.status.innerHTML = "●&nbsp;" + text;
  }

  async function api(path, options) {
    if (!apiBase) {
      throw new Error("Set the backend API base URL first (top-right).");
    }
    const res = await fetch(apiBase + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      /* non-JSON response */
    }
    if (!res.ok) {
      const err = new Error(
        (body && body.error && body.error.message) ||
          `Request failed with status ${res.status}`
      );
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  function fmt(n) {
    return Number(n).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function timeAgo(iso) {
    const then = new Date(iso).getTime();
    const secs = Math.max(0, (Date.now() - then) / 1000);
    if (secs < 60) return Math.floor(secs) + "s ago";
    if (secs < 3600) return Math.floor(secs / 60) + "m ago";
    if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
    return Math.floor(secs / 86400) + "d ago";
  }

  function uuid() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  // ------------------------------------------------------- connection
  async function checkHealth() {
    if (!apiBase) {
      setStatus("unknown", "not connected");
      return;
    }
    try {
      const h = await api("/health");
      setStatus("ok", `connected · ${h.users} users / ${h.transactions} txns`);
      els.docsLink.textContent = apiBase + "/docs";
    } catch (e) {
      setStatus("bad", "unreachable");
    }
  }

  function saveApiBase() {
    apiBase = normalizeBase(els.apiBase.value.trim());
    localStorage.setItem(LS_KEY, apiBase);
    checkHealth().then(() => {
      if (apiBase) {
        toast("Connected to " + apiBase, "ok");
        loadRanking();
      }
    });
  }

  // ------------------------------------------------------- transactions
  function renderTxResult(data) {
    const cls = data.duplicate ? "out--dup" : "out--ok";
    els.txOut.className = "out " + cls;
    const tag = data.duplicate
      ? "↩ DUPLICATE (idempotent replay — not counted again)"
      : "✓ CREATED";
    els.txOut.textContent = tag + "\n\n" + JSON.stringify(data, null, 2);
  }

  async function submitTx(useExistingId) {
    const fd = new FormData(els.txForm);
    const body = {
      userId: fd.get("userId").trim(),
      amount: parseFloat(fd.get("amount")),
      type: fd.get("type"),
    };
    let txId = fd.get("transactionId").trim();
    if (useExistingId && !txId) {
      toast("Generate or submit an ID first, then 'Send again'.", "err");
      return;
    }
    if (txId) body.transactionId = txId;

    try {
      const data = await api("/transaction", {
        method: "POST",
        body: JSON.stringify(body),
      });
      // Remember the server/used id so 'Send again' can replay it.
      els.txId.value = data.transactionId;
      renderTxResult(data);
      toast(data.duplicate ? "Duplicate detected — counted once." : "Transaction recorded.", data.duplicate ? "" : "ok");
      loadRanking();
    } catch (e) {
      els.txOut.className = "out out--err";
      els.txOut.textContent =
        "✗ ERROR " + (e.status || "") + "\n\n" + JSON.stringify(e.body || { message: e.message }, null, 2);
      toast(e.message, "err");
    }
  }

  // ------------------------------------------------------- summary
  async function loadSummary(userId) {
    try {
      const s = await api("/summary/" + encodeURIComponent(userId));
      const rows = s.transactions
        .map(
          (t) =>
            `<tr><td>${t.transactionId}</td><td class="num">${fmt(t.amount)}</td><td>${t.type}</td><td>${timeAgo(t.timestamp)}</td></tr>`
        )
        .join("");
      els.sumOut.innerHTML = `
        <div class="stat-grid">
          <div class="stat"><div class="k">Total value</div><div class="v">${fmt(s.totalValue)}</div></div>
          <div class="stat"><div class="k">Transactions</div><div class="v">${s.transactionCount}</div></div>
          <div class="stat"><div class="k">Average</div><div class="v">${fmt(s.averageTransaction)}</div></div>
          <div class="stat"><div class="k">Last activity</div><div class="v" style="font-size:14px">${timeAgo(s.lastActivity)}</div></div>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>Transaction ID</th><th class="num">Amount</th><th>Type</th><th>When</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="4" class="muted">No transactions.</td></tr>'}</tbody>
          </table>
        </div>`;
    } catch (e) {
      els.sumOut.innerHTML = `<p class="muted">${e.status === 404 ? "No such user yet." : e.message}</p>`;
      if (e.status !== 404) toast(e.message, "err");
    }
  }

  // ------------------------------------------------------- ranking
  function breakdownBar(b, score) {
    const total = score || 1;
    const pv = (b.volume / total) * 100;
    const pf = (b.frequency / total) * 100;
    const pr = (b.recency / total) * 100;
    return `<div class="bar" title="volume ${b.volume.toFixed(3)} · frequency ${b.frequency.toFixed(3)} · recency ${b.recency.toFixed(3)}">
      <span class="b-vol" style="width:${pv}%"></span>
      <span class="b-freq" style="width:${pf}%"></span>
      <span class="b-rec" style="width:${pr}%"></span>
    </div>`;
  }

  async function loadRanking() {
    if (!apiBase) return;
    try {
      const data = await api("/ranking");
      const w = data.weights;
      els.weights.textContent = `weights → volume ${w.volume} · frequency ${w.frequency} · recency ${w.recency}`;
      if (!data.ranking.length) {
        els.rankBody.innerHTML =
          '<tr><td colspan="7" class="muted center">No data yet — seed or submit a transaction.</td></tr>';
        return;
      }
      els.rankBody.innerHTML = data.ranking
        .map(
          (e) => `
        <tr class="${e.rank === 1 ? "rank-1" : ""}">
          <td>${e.rank}</td>
          <td>${e.userId}</td>
          <td class="score">${e.score.toFixed(3)}</td>
          <td>${breakdownBar(e.breakdown, e.score)}</td>
          <td class="num">${fmt(e.totalValue)}</td>
          <td class="num">${e.transactionCount}</td>
          <td>${timeAgo(e.lastActivity)}</td>
        </tr>`
        )
        .join("");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ------------------------------------------------------- demo
  async function seed() {
    try {
      const r = await api("/demo/seed", { method: "POST" });
      toast(r.message, "ok");
      loadRanking();
      checkHealth();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function reset() {
    try {
      await api("/demo/reset", { method: "POST" });
      toast("All data cleared.", "ok");
      els.txOut.textContent = "";
      els.sumOut.innerHTML = "";
      loadRanking();
      checkHealth();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ------------------------------------------------------- wire up
  els.apiBase.value = apiBase;
  els.saveApi.addEventListener("click", saveApiBase);
  els.apiBase.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveApiBase();
  });

  els.genId.addEventListener("click", () => (els.txId.value = uuid()));
  els.txForm.addEventListener("submit", (e) => {
    e.preventDefault();
    submitTx(false);
  });
  els.resendBtn.addEventListener("click", () => submitTx(true));

  els.sumForm.addEventListener("submit", (e) => {
    e.preventDefault();
    loadSummary(new FormData(els.sumForm).get("userId").trim());
  });

  els.rankBtn.addEventListener("click", loadRanking);
  els.seedBtn.addEventListener("click", seed);
  els.resetBtn.addEventListener("click", reset);
  els.refreshAllBtn.addEventListener("click", () => {
    checkHealth();
    loadRanking();
  });

  // initial
  checkHealth().then(loadRanking);
})();
