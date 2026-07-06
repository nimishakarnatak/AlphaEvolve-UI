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
      badge.className = "badge badge-live";
      badge.title = "Live: calling Gemini Flash";
    } else {
      badge.textContent = "mock mode";
      badge.className = "badge badge-mock";
      badge.title = State.status.has_key
        ? "MOCK_MODE is on (key present but disabled)"
        : "no GEMINI_API_KEY — offline mock";
    }
  } catch (e) {
    badge.textContent = "server offline";
    badge.className = "badge badge-mock";
    badge.title = "Could not reach /api/status";
  }
}

async function boot() {
  // wire nav
  document.querySelectorAll("[data-nav]").forEach((n) => {
    n.addEventListener("click", () => navigate(n.getAttribute("data-nav")));
  });
  // status badge — poll so it always reflects the running server
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
