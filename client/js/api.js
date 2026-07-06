/* API client + shared helpers (no framework). */
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).error || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  },
  status: () => API.get("/api/status"),
  experiments: () => API.get("/api/experiments"),
  configure: (b) => API.post("/api/configure", b),
  testRun: (b) => API.post("/api/test-run", b),
  shortRuns: (b) => API.post("/api/experiment/short-runs", b),
  promote: (id, b) => API.post(`/api/experiment/${id}/promote`, b),
  experiment: (id) => API.get(`/api/experiment/${id}`),
  startRun: (id, b) => API.post(`/api/experiment/${id}/start-run`, b),
  stopRun: (id) => API.post(`/api/experiment/${id}/stop-run`, {}),
  candidate: (id) => API.get(`/api/candidate/${id}`),
  versionTree: (id) => API.get(`/api/version-tree/${id}`),
  diff: (a, b) => API.get(`/api/diff?a=${a}&b=${b}`),
  steer: (b) => API.post("/api/steer", b),
  proposalTest: (id) => API.post(`/api/proposal/${id}/test`, {}),
  proposalAccept: (id) => API.post(`/api/proposal/${id}/accept`, {}),
  proposalDiscard: (id) => API.post(`/api/proposal/${id}/discard`, {}),
  chat: (b) => API.post("/api/chat", b),
};

/* Small DOM helpers -------------------------------------------------------- */
function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function")
      n.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c == null) return;
    n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return n;
}
function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
function fmt(n, d = 3) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  return Number(n).toFixed(d);
}
function shortId(id) { return id ? id.split("_").pop() : "?"; }
function timeAgo(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
function deltaClass(v) { return v > 0 ? "delta-pos" : v < 0 ? "delta-neg" : "delta-zero"; }
function deltaStr(v) { return `${v > 0 ? "+" : ""}${fmt(v, 3)}`; }

function toast(msg, kind = "") {
  const host = document.getElementById("toast-host");
  const t = el("div", { class: `toast ${kind}`, text: msg });
  host.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, 2400);
  setTimeout(() => t.remove(), 2800);
}

/* Simple global app state (client-side) ----------------------------------- */
const State = {
  currentExperimentId: null,
  draftConfig: null,
  status: { mock_mode: true },
};

/* SSE helper --------------------------------------------------------------- */
function openStream(expId, onEvent) {
  const es = new EventSource(`/api/experiment/${expId}/stream`);
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)); } catch (err) {}
  };
  es.onerror = () => { /* browser auto-reconnects */ };
  return es;
}
