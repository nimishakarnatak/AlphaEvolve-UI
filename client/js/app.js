/* Client-side hash router + bootstrap. */
const ROUTES = [
  { re: /^#\/setup/, screen: Screen1, nav: "#/setup" },
  { re: /^#\/short-runs(?:\/([^/]+))?/, screen: Screen2, nav: "#/short-runs" },
  { re: /^#\/experiment(?:\/([^/]+))?/, screen: Screen3, nav: "#/experiment" },
  { re: /^#\/versions(?:\/([^/]+))?/, screen: Screen4, nav: "#/versions" },
];

let currentScreen = null;

function navigate(hash) {
  if (location.hash === hash) { route(); }
  else location.hash = hash;
}

async function route() {
  const view = document.getElementById("view");
  const hash = location.hash || "#/setup";
  const match = ROUTES.find((r) => r.re.test(hash)) || ROUTES[0];
  const m = hash.match(match.re);
  const id = (m && m[1]) || null;

  // cleanup previous screen (e.g. close SSE)
  if (currentScreen && typeof currentScreen.cleanup === "function") currentScreen.cleanup();
  currentScreen = match.screen;

  // nav active state
  document.querySelectorAll(".nav-link").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("data-nav") === match.nav);
  });

  try {
    await match.screen.render(view, id);
  } catch (e) {
    view.innerHTML = `<div class="container"><div class="empty">Error: ${e.message}</div></div>`;
    console.error(e);
  }
}

// Reflect the *running* server's mode. The badge is polled (not set once at
// load) so it never goes stale relative to the backend — e.g. after you restart
// the server in a different mode, the badge updates within a few seconds without
// a manual reload.
async function refreshBadge() {
  const badge = document.getElementById("mode-badge");
  try {
    State.status = await API.status();
    if (State.status.live_enabled) {
      badge.textContent = "live · " + State.status.model;
      badge.className = "badge badge-live badge-toggle";
      badge.title = "Live: calling Gemini Flash. Click to switch to mock.";
    } else {
      badge.textContent = "mock mode";
      badge.className = "badge badge-mock badge-toggle";
      badge.title = State.status.has_key
        ? "Mock (offline). Click to switch to live."
        : "no GEMINI_API_KEY — add one to enable live. Click to try.";
    }
  } catch (e) {
    badge.textContent = "server offline";
    badge.className = "badge badge-mock";
    badge.title = "Could not reach /api/status";
  }
}

// Toggle mock/live from the badge (no restart / env editing needed).
async function toggleMode() {
  const currentlyLive = !!(State.status && State.status.live_enabled);
  try {
    // going live means mock=false; going mock means mock=true
    const st = await API.setMode(currentlyLive);
    State.status = st;
    await refreshBadge();
    if (!currentlyLive && !st.live_enabled) {
      toast(st.has_key ? "Could not enable live" :
        "No GEMINI_API_KEY on the server — add it to .env to use live mode", "bad");
    } else {
      toast(st.live_enabled ? "Switched to LIVE (Gemini Flash)" : "Switched to MOCK (offline)", "good");
    }
  } catch (e) { toast("Mode switch failed: " + e.message, "bad"); }
}

async function boot() {
  // wire nav
  document.querySelectorAll("[data-nav]").forEach((n) => {
    n.addEventListener("click", () => navigate(n.getAttribute("data-nav")));
  });
  // status badge — clickable toggle + poll so it always reflects the server
  const badge = document.getElementById("mode-badge");
  if (badge) badge.addEventListener("click", toggleMode);
  await refreshBadge();
  setInterval(refreshBadge, 4000);
  // default experiment for deep links
  try {
    const exps = await API.experiments();
    if (exps.length) State.currentExperimentId = exps[0].id;
  } catch (e) {}

  window.addEventListener("hashchange", route);
  if (!location.hash) location.hash = "#/setup";
  else route();
}

boot();
