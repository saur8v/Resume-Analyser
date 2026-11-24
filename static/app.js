"use strict";

// ============================================================
// Helpers
// ============================================================

const el = (id) => document.getElementById(id);

// Small DOM builder. Text always goes in as text nodes (never HTML), so
// profile data and LLM output can't inject markup through it.
function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key in node && typeof value !== "string") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG = (paths, size = 16) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"></path>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"></path>',
  shield: '<path d="M12 3l7 4v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V7z"></path>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5H5V6h5"></path>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"></path>',
  user: '<circle cx="12" cy="8" r="4"></circle><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"></path>',
  logout: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"></path>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"></path><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"></path>',
  file: '<path d="M6 3h9l4 4v14H6z"></path><path d="M14 3v5h5"></path>',
  close: '<path d="M6 6l12 12M18 6L6 18"></path>',
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"></path>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"></path>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"></path>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"></path>',
};
const icon = (name, size) => {
  const span = h("span", { "aria-hidden": "true" });
  span.innerHTML = SVG(ICONS[name], size); // constant markup from ICONS only
  return span;
};
const hydrateIcons = (root = document) =>
  root.querySelectorAll("[data-icon]").forEach((node) => { node.innerHTML = SVG(ICONS[node.dataset.icon]); });

function toast(message, kind = "", duration = 4500) {
  const node = h("div", { class: `toast${kind ? ` status-${kind}` : ""}`, text: message });
  el("toast-container").append(node);
  window.setTimeout(() => node.remove(), duration);
}

function renderMarkdown(markdown) {
  // LLM output and scraped job pages are untrusted: sanitize before inserting.
  // Reports never need images, and blocking them stops remote tracking pixels.
  return DOMPurify.sanitize(marked.parse(markdown || ""), { FORBID_TAGS: ["img", "picture", "video", "audio", "iframe", "form", "input", "style"] });
}

async function api(path, options = {}) {
  const init = { credentials: "same-origin", ...options };
  if (options.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    init.body = JSON.stringify(options.json);
  }
  delete init.json;
  const res = await fetch(path, init);
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    showAuthScreen();
    throw new Error("Your session has ended. Please sign in again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data.detail || `Request failed (${res.status}).`);
    error.status = res.status;
    throw error;
  }
  return data;
}

const clone = (value) => JSON.parse(JSON.stringify(value));
const NOT_SPECIFIED = "Not specified";
const known = (value) => Boolean(value) && value !== NOT_SPECIFIED;
const safeUrl = (url) => (/^https?:\/\//i.test(url || "") ? url : null);

const dayStart = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
// "Today, 4:52 PM" / "Yesterday, …" / "Oct 28". Pass inSentence for "today, …".
function formatWhen(iso, inSentence = false) {
  const date = new Date(iso);
  const days = Math.round((dayStart(new Date()) - dayStart(date)) / 86400000);
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (days === 0) return `${inSentence ? "today" : "Today"}, ${time}`;
  if (days === 1) return `${inSentence ? "yesterday" : "Yesterday"}, ${time}`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: days > 300 ? "numeric" : undefined });
}
function shortWhen(iso) {
  const date = new Date(iso);
  const days = Math.round((dayStart(new Date()) - dayStart(date)) / 86400000);
  return days === 0
    ? date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function historyGroup(iso) {
  const date = new Date(iso);
  const days = Math.round((dayStart(new Date()) - dayStart(date)) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Earlier this week";
  return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

// Titles are stored as "Company - Role".
function splitTitle(title) {
  const index = title.indexOf(" - ");
  return index === -1
    ? { company: title, role: "" }
    : { company: title.slice(0, index), role: title.slice(index + 3) };
}

// ============================================================
// State
// ============================================================

let profileRecord = null; // what the server last confirmed: { profile, version, updated_at, resume }
let analyses = [];        // history summaries, newest first
const analysisCache = new Map();
let selectedAnalysisId = null;
let lastAnalysisId = null; // the report open before visiting the profile, for "Back to analyses"
let currentView = null;
let isAnalyzing = false;
let isUploading = false; // true while uploading a resume, "undo" while undoing one

// ============================================================
// Auth
// ============================================================

let authMode = "login";

function setAuthMode(mode) {
  authMode = mode;
  const signingUp = mode === "signup";
  el("name-field").classList.toggle("hidden", !signingUp);
  el("password-hint").classList.toggle("hidden", !signingUp);
  el("auth-password").autocomplete = signingUp ? "new-password" : "current-password";
  el("auth-title").textContent = signingUp ? "Set up your desk" : "Sign in to your desk";
  el("auth-switch").textContent = signingUp ? "I already have an account" : "Create account";
  el("auth-submit").textContent = signingUp ? "Create account" : "Sign in";
  document.title = signingUp ? "resumedesk — Create account" : "resumedesk — Sign in";
}

el("password-toggle").addEventListener("click", () => {
  const input = el("auth-password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  el("password-toggle").textContent = show ? "Hide" : "Show";
  el("password-toggle").setAttribute("aria-pressed", String(show));
  el("password-toggle").setAttribute("aria-label", show ? "Hide password" : "Show password");
  input.focus();
});

function showAuthScreen() {
  closeDrawer(true);
  el("app-shell").classList.add("hidden");
  el("auth-screen").classList.remove("hidden");
  document.title = "resumedesk — Sign in";
  profileRecord = null;
  analyses = [];
  analysisCache.clear();
  selectedAnalysisId = null;
}

async function enterApp(email) {
  el("auth-screen").classList.add("hidden");
  el("app-shell").classList.remove("hidden");
  el("user-email").textContent = email;
  await Promise.all([loadProfile(), loadHistory()]);
  renderAccount();
  // New users with an empty profile start on the profile page.
  if (profileIsEmpty(profileRecord.profile)) showProfile();
  else startNewAnalysis();
}

el("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = el("auth-email").value.trim();
  const password = el("auth-password").value;
  if (!email || !password) { toast("Enter your email and password.", "error"); return; }
  if (authMode === "signup" && password.length < 8) { toast("Password must be at least 8 characters.", "error"); return; }

  const submit = el("auth-submit");
  submit.disabled = true;
  try {
    const body = authMode === "signup"
      ? { email, password, full_name: el("auth-name").value.trim() }
      : { email, password };
    const data = await api(`/api/auth/${authMode}`, { json: body });
    el("auth-password").value = "";
    await enterApp(data.email);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    submit.disabled = false;
  }
});

el("auth-switch").addEventListener("click", () => setAuthMode(authMode === "login" ? "signup" : "login"));

// ============================================================
// Account menu
// ============================================================

const avatarBtn = el("avatar-btn");
const accountDropdown = el("account-dropdown");

function initialsFor(name, email) {
  const words = (name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length) return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
  return ((email || "?")[0]).toUpperCase();
}

function renderAccount() {
  const email = el("user-email").textContent;
  const name = profileRecord?.profile.basics.full_name.trim() || "";
  el("avatar-initials").textContent = initialsFor(name, email);
  el("account-name").textContent = name || "Your account";
  avatarBtn.title = name ? `${name} · ${email}` : email;
}

function setAccountMenu(open) {
  accountDropdown.classList.toggle("hidden", !open);
  avatarBtn.setAttribute("aria-expanded", String(open));
  if (open) accountDropdown.querySelector(".account-dropdown-item").focus();
}

avatarBtn.addEventListener("click", () => setAccountMenu(accountDropdown.classList.contains("hidden")));
accountDropdown.addEventListener("click", (event) => {
  if (event.target.closest(".account-dropdown-item")) setAccountMenu(false);
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".account-menu")) setAccountMenu(false);
});

el("profile-nav-btn").addEventListener("click", () => showProfile());
el("logout-btn").addEventListener("click", async () => {
  if (!confirmDiscardDrawer()) return;
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  setAuthMode("login");
  showAuthScreen();
});

// ============================================================
// Views
// ============================================================

function setBreadcrumb(current) {
  const crumb = el("breadcrumb");
  crumb.replaceChildren();
  if (current === null) {
    crumb.append(h("span", { class: "crumb-current", text: "New analysis" }));
    return;
  }
  crumb.append(
    h("button", { type: "button", text: "Workspace", onClick: () => startNewAnalysis() }),
    h("span", { class: "crumb-sep", "aria-hidden": "true", text: "/" }),
    h("span", { class: "crumb-current", text: current }),
  );
}

function showView(view) {
  currentView = view;
  for (const name of ["analysis", "results", "profile"]) {
    el(`view-${name}`).classList.toggle("hidden", name !== view);
  }
  avatarBtn.classList.toggle("active", view === "profile");
  // On the profile page the sidebar holds the profile's section menu instead of history.
  el("sidebar-history").classList.toggle("hidden", view === "profile");
  el("sidebar-profile").classList.toggle("hidden", view !== "profile");
  window.scrollTo({ top: 0 });
}

function startNewAnalysis() {
  if (!confirmDiscardDrawer()) return;
  selectedAnalysisId = null;
  if (!isAnalyzing) {
    el("jd-url").value = "";
    el("jd-manual").value = "";
    clearUrlError();
  }
  document.title = "resumedesk — New analysis";
  setBreadcrumb(null);
  renderHistory();
  renderRecent();
  updateAnalyzeAvailability();
  showView("analysis");
}

function showProfile() {
  if (!confirmDiscardDrawer()) return;
  if (currentView !== "profile") lastAnalysisId = currentView === "results" ? selectedAnalysisId : null;
  selectedAnalysisId = null;
  document.title = "resumedesk — My profile";
  setBreadcrumb("My profile");
  renderHistory();
  renderProfile();
  showView("profile");
}

el("new-analysis-btn").addEventListener("click", () => startNewAnalysis());
el("back-to-analyses-btn").addEventListener("click", () => {
  // Return to the report you came from, if any; otherwise to a new analysis.
  if (lastAnalysisId !== null && analyses.some((item) => item.id === lastAnalysisId)) selectAnalysis(lastAnalysisId);
  else startNewAnalysis();
});
el("brand-btn").addEventListener("click", () => startNewAnalysis());
el("setup-profile-btn").addEventListener("click", () => showProfile());

// ============================================================
// History sidebar
// ============================================================

function summaryOf(analysis) {
  const insights = analysis.insights;
  return {
    id: analysis.id,
    title: analysis.title,
    job_url: analysis.job_url,
    profile_version: analysis.profile_version,
    created_at: analysis.created_at,
    location: insights ? insights.at_a_glance.location : null,
    matched_count: insights ? insights.skills_matched.length : null,
    missing_count: insights ? insights.skills_missing.length : null,
  };
}

async function loadHistory() {
  analyses = await api("/api/analyses");
  renderHistory();
}

function renderHistory() {
  const list = el("history-list");
  const query = el("history-search").value.trim().toLowerCase();
  const shown = query ? analyses.filter((item) => item.title.toLowerCase().includes(query)) : analyses;
  el("history-count").textContent = String(analyses.length);
  list.replaceChildren();

  let lastGroup = null;
  for (const item of shown) {
    const group = historyGroup(item.created_at);
    if (group !== lastGroup) {
      list.append(h("span", { class: "history-group", text: group }));
      lastGroup = group;
    }
    const { company, role } = splitTitle(item.title);
    const active = item.id === selectedAnalysisId;
    list.append(h("button", {
      type: "button",
      class: `history-item${active ? " active" : ""}`,
      "aria-current": active ? "page" : null,
      title: item.title,
      onClick: () => selectAnalysis(item.id),
    },
      h("span", { class: "history-item-top" },
        h("span", { class: "history-company", text: company }),
        h("span", { class: "history-when", text: shortWhen(item.created_at) })),
      role ? h("span", { class: "history-role", text: role }) : null,
    ));
  }

  const empty = el("history-empty");
  empty.textContent = analyses.length ? "No analyses match your search." : "Your analyses will appear here.";
  empty.classList.toggle("hidden", shown.length > 0);
}

el("history-search").addEventListener("input", renderHistory);

// ============================================================
// New analysis
// ============================================================

function updateAnalyzeAvailability() {
  const hasJobInput = Boolean(el("jd-manual").value.trim() || el("jd-url").value.trim());
  const hasProfile = Boolean(profileRecord) && !profileIsEmpty(profileRecord.profile);
  el("analyze-btn").disabled = isAnalyzing || !(hasJobInput && hasProfile);
  el("empty-profile-note").classList.toggle("hidden", !profileRecord || hasProfile);
}

el("jd-url").addEventListener("input", () => { clearUrlError(); updateAnalyzeAvailability(); });

const SCRAPE_FAILED_MESSAGE =
  "We couldn't read a job description from that link. The site may need a login or block automated access. Paste the job description below instead.";

// Shown under the URL field when a link can't be read: no analysis runs, and
// the paste box opens so the description can go straight in.
function showUrlError(message) {
  el("jd-url-error").textContent = message;
  el("jd-url-error").classList.remove("hidden");
  el("jd-url").setAttribute("aria-invalid", "true");
  el("paste-details").open = true;
  // The form is re-enabled once the request finishes; focus the paste box after that.
  window.setTimeout(() => el("jd-manual").focus(), 0);
}

function clearUrlError() {
  el("jd-url-error").classList.add("hidden");
  el("jd-url").removeAttribute("aria-invalid");
}
el("jd-manual").addEventListener("input", updateAnalyzeAvailability);
el("jd-url").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !el("analyze-btn").disabled) el("analyze-btn").click();
});

function renderRecent() {
  const grid = el("recent-grid");
  grid.replaceChildren();
  const recent = analyses.slice(0, 3);
  el("recent-section").classList.toggle("hidden", recent.length === 0);
  for (const item of recent) {
    const { company, role } = splitTitle(item.title);
    const kicker = [company, known(item.location) ? item.location : null].filter(Boolean).join(" · ");
    const total = (item.matched_count ?? 0) + (item.missing_count ?? 0);
    grid.append(h("button", { type: "button", class: "recent-card", onClick: () => selectAnalysis(item.id) },
      h("span", { class: "kicker", text: kicker, title: kicker }),
      h("span", { class: "recent-role", text: role || item.title }),
      total ? h("span", { class: "recent-line" }, h("mark", { text: `${item.matched_count} of ${total}` }), " key skills on your profile") : null,
      h("span", { class: "recent-when", text: formatWhen(item.created_at) }),
    ));
  }
}

function setProgress(title, detail) {
  el("progress-title").textContent = title;
  el("progress-detail").textContent = detail;
}

function setAnalyzing(busy) {
  isAnalyzing = busy;
  el("progress-state").classList.toggle("hidden", !busy);
  el("jd-url").disabled = busy;
  el("jd-manual").disabled = busy;
  updateAnalyzeAvailability();
}

el("analyze-btn").addEventListener("click", async () => {
  const manualJd = el("jd-manual").value.trim();
  const jobUrl = el("jd-url").value.trim();
  if (!manualJd && !jobUrl) { toast("Enter a job posting URL or paste a job description.", "error"); return; }
  if (isAnalyzing) return;

  clearUrlError();
  setAnalyzing(true);
  try {
    // Pasted text wins when both are filled in (the form says so).
    let jd;
    if (manualJd) {
      setProgress("Reading the job description", "Pulling out the role details and requirements…");
      jd = (await api("/api/job-description-manual", { json: { text: manualJd } })).job_description;
    } else {
      setProgress("Fetching the job posting", "Opening the page and extracting the role…");
      try {
        jd = ((await api("/api/job-description", { json: { url: jobUrl } })).job_description || "").trim();
        if (!jd) throw new Error(SCRAPE_FAILED_MESSAGE);
      } catch (err) {
        // Guardrail: the link couldn't be read, so stop here and ask for the text.
        if (err.status === 401) throw err;
        showUrlError(err.status === 422 ? err.message : SCRAPE_FAILED_MESSAGE);
        return;
      }
    }
    jd = (jd || "").trim();
    if (!jd) throw new Error("No job description could be extracted.");

    setProgress("Comparing with your profile", "Writing your report. This usually takes under a minute…");
    const analysis = await api("/api/analyses", {
      json: { job_description: jd, title: inferSessionName(jd), job_url: manualJd ? null : jobUrl },
    });
    analysisCache.set(analysis.id, analysis);
    analyses.unshift(summaryOf(analysis));
    renderHistory();
    renderRecent();
    // Only jump to the result if the user is still waiting on the form.
    if (currentView === "analysis") {
      el("jd-url").value = "";
      el("jd-manual").value = "";
      el("paste-details").open = false;
      await selectAnalysis(analysis.id);
    }
    toast("Analysis complete.", "ok");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setAnalyzing(false);
  }
});

// Fallback title when the insights call couldn't name the company and role.
// The extraction prompt starts with "**Company:** …" / "**Role:** …" lines;
// older outputs put the value under a heading instead.
const ROLE_LABEL = String.raw`role(?:\s*\/\s*title)?|job\s*title|position|title`;
const COMPANY_LABEL = String.raw`company(?:\s*(?:\/\s*organi[sz]ation|name))?|hiring\s+company|employer|organi[sz]ation`;
const ROLE_HEADING = /^(?:role(?:\s*\/\s*title)?|job\s*title|position|title)$/i;
const COMPANY_HEADING = /^(?:company(?:\s+(?:information|info|name|overview))?|about\s+(?:the\s+)?company|hiring\s+company|employer)$/i;
const isUnknownLabel = (value) => !value || /^(?:not specified|not stated|n\/a|unknown|none)$/i.test(value);
const cleanLabel = (value) => value.replace(/[*_`]/g, "").replace(/\s+/g, " ").replace(/[|•].*$/, "").trim().replace(/[.,;:]+$/, "");
const headingText = (line) => cleanLabel(line.replace(/^#{1,4}\s+/, ""));

function labeledValue(lines, labelPattern) {
  const re = new RegExp(`^(?:[-*>]+\\s*)?(?:\\*\\*)?(?:${labelPattern})(?:\\*\\*)?\\s*[:：–—-]\\s*(.+)$`, "i");
  for (const line of lines) {
    const match = line.match(re);
    if (!match) continue;
    const value = cleanLabel(match[1]);
    if (!isUnknownLabel(value) && value.length < 90) return value;
  }
  return "";
}

function valueUnderHeading(lines, headingRe) {
  for (let i = 0; i < lines.length - 1; i++) {
    if (!/^#{1,4}\s+/.test(lines[i]) || !headingRe.test(headingText(lines[i]))) continue;
    if (lines[i + 1].startsWith("#")) return "";
    const value = cleanLabel(lines[i + 1].replace(/^[-*>]\s+/, ""));
    return !isUnknownLabel(value) && value.length <= 60 && value.split(" ").length <= 7 ? value : "";
  }
  return "";
}

function inferSessionName(jobDescription) {
  const lines = jobDescription.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const company = labeledValue(lines, COMPANY_LABEL) || valueUnderHeading(lines, COMPANY_HEADING);
  const role = labeledValue(lines, ROLE_LABEL) || valueUnderHeading(lines, ROLE_HEADING);
  return `${company || "Company not specified"} - ${role || "Role not specified"}`;
}

// ============================================================
// Analysis report
// ============================================================

const GLANCE_FIELDS = [
  ["company", "Company"], ["role", "Role"], ["location", "Location"], ["pay", "Pay"], ["work_mode", "Type"],
];
const SKILLS_SHOWN = 4; // per list; the rest sit behind "Show all"

const RESULT_TABS = { analysis: "analysis", markup: "markup", job: "jd" };

function setResultTab(tab) {
  for (const [name, prefix] of Object.entries(RESULT_TABS)) {
    const active = name === tab;
    el(`${prefix}-panel`).classList.toggle("hidden", !active);
    el(`${prefix}-tab-btn`).classList.toggle("active", active);
    el(`${prefix}-tab-btn`).setAttribute("aria-selected", String(active));
  }
  // The marked-up resume uses the full width: its notes take the side column.
  document.querySelector(".result-aside").classList.toggle("hidden", tab === "markup");
  if (tab === "markup") requestAnimationFrame(layoutMarginNotes);
}

// Margin notes sit level with the entry they mark but never push the page
// apart: each note block starts at its row's top, moved down only as far as
// needed to clear the block above. Notes that run past the sheet's bottom
// get room below the board instead of stretching the paper.
function layoutMarginNotes() {
  const board = document.querySelector("#markup-panel .mk-board");
  if (!board || !board.offsetParent) return; // tab hidden: laid out when shown
  const wide = window.matchMedia("(min-width: 901px)").matches;
  board.style.marginBottom = "";
  let lowest = -Infinity;
  for (const notes of board.querySelectorAll(".mk-margin .mk-notes")) {
    notes.style.transform = "";
    if (!wide) continue;
    const natural = notes.parentElement.offsetTop;
    const top = Math.max(natural, lowest + 8);
    if (top > natural) notes.style.transform = `translateY(${top - natural}px)`;
    lowest = top + notes.offsetHeight;
  }
  if (wide && lowest > board.offsetHeight) board.style.marginBottom = `${lowest - board.offsetHeight + 16}px`;
}

let marginLayoutFrame = 0;
window.addEventListener("resize", () => {
  cancelAnimationFrame(marginLayoutFrame);
  marginLayoutFrame = requestAnimationFrame(layoutMarginNotes);
});
document.fonts?.ready.then(() => layoutMarginNotes());
el("analysis-tab-btn").addEventListener("click", () => setResultTab("analysis"));
el("markup-tab-btn").addEventListener("click", () => setResultTab("markup"));
el("jd-tab-btn").addEventListener("click", () => setResultTab("job"));

// ---- Marked-up resume: rendered from the server's markup structure ----

function markupLine(segments) {
  return segments.map((seg) => {
    if (!seg.mark) return seg.text;
    const good = seg.mark.kind === "good";
    return h("span", { class: "mk-wrap" },
      h(good ? "mark" : "span", { class: good ? "mk-good" : "mk-fix", "data-n": String(seg.mark.n) }, seg.text),
      h("sup", { class: `mk-n ${good ? "mk-n-good" : "mk-n-fix"}`, text: String(seg.mark.n) }));
  });
}

function markupNotes(notes) {
  if (!notes.length) return null;
  return h("ul", { class: "mk-notes" }, notes.map((note) => h("li", { class: `mk-note mk-note-${note.kind}` },
    h("span", { class: "mk-note-n", "aria-hidden": "true", text: note.n ? `${note.n}.` : "–" }),
    note.n ? h("span", { class: "sr-only", text: `Note ${note.n}: ` }) : null,
    note.text)));
}

function renderMarkup(markup) {
  const panel = el("markup-panel");
  panel.replaceChildren();
  if (!markup) return;
  const { header, counts } = markup;
  const contact = [...header.contact, ...header.links].join(" · ");

  const legend = h("div", { class: "mk-legend" },
    h("span", {}, h("mark", { class: "mk-good", text: "Highlighted" }), ` works for this role (${counts.good})`),
    h("span", {}, h("span", { class: "mk-fix", text: "Red pen" }), ` needs work (${counts.fix})`),
    h("span", { class: "muted small", text: "Built from the profile this analysis used." }));

  // One two-column grid: the resume on a paper sheet (column 1) and the red-pen
  // notes on the desk beside it (column 2). Each row pairs a piece of the
  // resume with its notes, so every note sits level with what it marks.
  const cells = [];
  const row = (paper, notes = []) => cells.push(
    h("div", { class: "mk-paper" }, paper),
    h("div", { class: "mk-margin" }, markupNotes(notes)));

  row(h("header", { class: "mk-head" },
    h("h2", { class: "mk-name", text: header.name || "Your resume" }),
    header.headline ? h("p", { class: "mk-headline", text: header.headline }) : null,
    contact ? h("p", { class: "mk-contact", text: contact }) : null));

  for (const section of markup.sections) {
    row(h("h3", { class: "mk-section-title", text: section.title }), section.notes);
    for (const entry of section.entries) {
      const top = entry.heading
        ? h("div", { class: "mk-entry-top" },
          h("strong", { text: entry.heading }),
          entry.dates ? h("span", { class: "mk-dates", text: entry.dates }) : null)
        : null;
      const lines = entry.style === "bullets"
        ? h("ul", { class: "mk-lines" }, entry.lines.map((line) => h("li", {}, markupLine(line))))
        : entry.lines.map((line) => h("p", { class: "mk-text" }, markupLine(line)));
      row(h("div", { class: "mk-entry" }, top, entry.sub ? h("p", { class: "mk-sub", text: entry.sub }) : null, lines),
        entry.notes);
    }
  }

  panel.append(legend, h("div", { class: "mk-board" }, h("div", { class: "mk-sheet-bg", "aria-hidden": "true" }), cells));
}

// Every skill is rendered; past the first SKILLS_SHOWN they're marked as extras,
// hidden while the card is collapsed (with a "+N more" hint in their place).
function chips(container, items, className) {
  container.replaceChildren();
  if (!items.length) { container.append(h("span", { class: "none-text", text: "None" })); return; }
  items.forEach((item, index) => {
    const name = typeof item === "string" ? item : item.skill;
    container.append(h("span", {
      class: `chip ${className}${index >= SKILLS_SHOWN ? " chip-extra" : ""}`,
      text: name,
      title: item.evidence ? `From your profile: “${item.evidence}”` : null,
    }));
  });
  if (items.length > SKILLS_SHOWN) {
    container.append(h("span", { class: "chip-more", text: `+${items.length - SKILLS_SHOWN} more` }));
  }
}

function setSkillsExpanded(expanded) {
  const card = el("skills-card");
  const extras = card.querySelectorAll(".chip-extra").length;
  const toggle = el("skills-toggle");
  card.classList.toggle("skills-collapsed", !expanded);
  toggle.classList.toggle("hidden", extras === 0);
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.textContent = expanded ? "Show fewer" : "Show all";
}

el("skills-toggle").addEventListener("click", () =>
  setSkillsExpanded(el("skills-toggle").getAttribute("aria-expanded") !== "true"));

function renderResults(analysis) {
  const insights = analysis.insights;
  const glance = insights ? insights.at_a_glance : {};
  const { company, role } = splitTitle(analysis.title);

  el("result-kicker").textContent = [
    known(glance.company) ? glance.company : company,
    known(glance.location) ? glance.location : null,
    known(glance.work_mode) ? glance.work_mode : null,
  ].filter(Boolean).join(" · ");
  el("result-role").textContent = known(glance.role) ? glance.role : (role || analysis.title);
  el("result-meta").textContent = `Analysed ${formatWhen(analysis.created_at, true)}`;

  const posting = el("open-posting-link");
  const url = safeUrl(analysis.job_url);
  posting.classList.toggle("hidden", !url);
  if (url) posting.href = url;

  el("analysis-panel").innerHTML = renderMarkdown(analysis.improvements);
  el("jd-panel").innerHTML = renderMarkdown(analysis.job_description);
  renderMarkup(analysis.markup);
  el("markup-tab-btn").classList.toggle("hidden", !analysis.markup);
  setResultTab("analysis");

  // At a glance + key skills (absent on older analyses or if that call failed).
  el("glance-card").classList.toggle("hidden", !insights);
  el("skills-card").classList.toggle("hidden", !insights);
  if (insights) {
    const list = el("glance-list");
    list.replaceChildren();
    // Always list all five fields; say so plainly when the posting leaves one out.
    for (const [key, label] of GLANCE_FIELDS) {
      const value = known(glance[key]) ? glance[key] : null;
      list.append(h("dt", { text: label }),
        h("dd", { class: value ? null : "not-disclosed", text: value || "Not disclosed" }));
    }
    chips(el("skills-matched"), insights.skills_matched, "chip-ok");
    chips(el("skills-missing"), insights.skills_missing, "chip-gap");
    setSkillsExpanded(false);
  }
}

async function selectAnalysis(id) {
  if (!confirmDiscardDrawer()) return;
  try {
    let analysis = analysisCache.get(id);
    if (!analysis) {
      analysis = await api(`/api/analyses/${id}`);
      analysisCache.set(id, analysis);
    }
    selectedAnalysisId = id;
    document.title = `resumedesk — ${analysis.title}`;
    setBreadcrumb(splitTitle(analysis.title).company);
    renderResults(analysis);
    renderHistory();
    showView("results");
  } catch (err) {
    toast(err.message, "error");
  }
}


el("delete-analysis-btn").addEventListener("click", async () => {
  const id = selectedAnalysisId;
  if (id === null || !window.confirm("Delete this analysis from your history? This can't be undone.")) return;
  try {
    await api(`/api/analyses/${id}`, { method: "DELETE" });
    analysisCache.delete(id);
    analyses = analyses.filter((item) => item.id !== id);
    startNewAnalysis();
    toast("Analysis deleted.", "ok");
  } catch (err) {
    toast(err.message, "error");
  }
});

el("download-pdf-btn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const analysis = analysisCache.get(selectedAnalysisId);
  if (!analysis) return;
  const label = button.innerHTML;
  button.disabled = true;
  button.textContent = "Preparing PDF…";
  try {
    const res = await fetch(`/api/analyses/${analysis.id}/pdf`, { credentials: "same-origin" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Failed to generate PDF.");
    }
    const objectUrl = URL.createObjectURL(await res.blob());
    const link = h("a", { href: objectUrl, download: `${analysis.title.replace(/[\\/:*?"<>|]/g, "-").trim() || "resume-analysis"}.pdf` });
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    button.disabled = false;
    button.innerHTML = label;
  }
});

// ============================================================
// Profile: loading and saving
// ============================================================

function profileIsEmpty(profile) {
  const b = profile.basics;
  const hasBasics = [b.headline, b.phone, b.location, b.summary].some(Boolean) || b.links.length > 0;
  const lists = ["skills", "experience", "projects", "education", "certifications", "achievements"];
  return !hasBasics && lists.every((key) => profile[key].length === 0) && !profile.additional_context;
}

async function loadProfile() {
  profileRecord = await api("/api/profile");
}

function applyProfileRecord(record) {
  profileRecord = record;
  renderAccount();
  if (currentView === "profile") renderProfile();
  updateAnalyzeAvailability();
}

// Every edit goes through here: copy the confirmed profile, apply one change,
// and save the whole thing with its version. The server validates it and
// rejects the save (409) if the profile changed elsewhere in the meantime.
async function saveProfileWith(mutate, successMessage = "Saved.") {
  const next = clone(profileRecord.profile);
  mutate(next);
  try {
    applyProfileRecord(await api("/api/profile", {
      method: "PUT",
      json: { profile: next, version: profileRecord.version },
    }));
    toast(successMessage, "ok");
    return true;
  } catch (err) {
    if (err.status === 409) {
      await loadProfile();
      applyProfileRecord(profileRecord);
      toast("Your profile changed in another tab. We loaded the latest version; please make that edit again.", "error");
      return true; // the open editor is now stale, so close it
    }
    toast(err.message, "error");
    return false;
  }
}

// ============================================================
// Profile: resume upload
// ============================================================

// The upload merges into the profile; say what changed in one short message.
function mergeSummaryText(summary) {
  if (!summary) return "Resume read. Check your profile and fix anything that looks off.";
  const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const parts = [];
  if (summary.added.length) parts.push(`added ${count(summary.added.length, "item")}`);
  if (summary.updated.length) parts.push(`updated ${count(summary.updated.length, "item")}`);
  let text = parts.length
    ? `Resume merged: ${parts.join(", ")}.`
    : "Resume read. Your profile already had everything on it.";
  if (summary.not_on_resume.length) {
    text += ` Kept ${count(summary.not_on_resume.length, "entry")} not on this resume; delete any that are out of date.`;
  }
  return text;
}

async function undoResumeUpload() {
  if (isUploading) return;
  isUploading = "undo"; // truthy, so uploads stay blocked; also picks the status text
  renderProfile();
  try {
    applyProfileRecord(await api("/api/profile/resume/undo", { json: { version: profileRecord.version } }));
    toast("Upload undone. Your profile is back to how it was.", "ok");
  } catch (err) {
    if (err.status === 409 || err.status === 404) await loadProfile();
    toast(err.message, "error");
  } finally {
    isUploading = false;
    renderProfile();
  }
}

function chooseResume() {
  if (isUploading) return;
  el("resume-file").value = "";
  el("resume-file").click();
}

el("resume-file").addEventListener("change", async () => {
  const file = el("resume-file").files[0];
  if (!file) return;
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { toast("Please choose a PDF resume.", "error"); return; }

  isUploading = true;
  renderProfile();
  try {
    const formData = new FormData();
    formData.append("resume", file);
    formData.append("version", String(profileRecord.version));
    const { merge_summary: summary, ...record } = await api("/api/profile/resume", { method: "POST", body: formData });
    applyProfileRecord(record);
    toast(mergeSummaryText(summary), "ok", 9000);
  } catch (err) {
    if (err.status === 409) await loadProfile();
    toast(err.message, "error");
  } finally {
    isUploading = false;
    renderProfile();
  }
});

// ============================================================
// Profile: read view
// ============================================================

const ITEM_EDITORS = {
  experience: {
    kicker: "Experience", noun: "role",
    fields: [
      { key: "title", label: "Job title", wide: true }, { key: "company", label: "Company" },
      { key: "location", label: "Location", optional: true }, { key: "start_date", label: "Start", placeholder: "e.g. May 2025" },
      { key: "end_date", label: "End", placeholder: "e.g. Sept 2025" },
      { key: "is_current", label: "I currently work here", type: "checkbox" },
      { key: "highlights", label: "Highlights", type: "list", add: "Add highlight" },
    ],
  },
  projects: {
    kicker: "Projects", noun: "project",
    fields: [
      { key: "name", label: "Project name", wide: true }, { key: "url", label: "Link", type: "url", optional: true, wide: true, placeholder: "https://github.com/…" },
      { key: "tech_stack", label: "Tech stack", type: "csv", wide: true, hint: "Separate with commas." },
      { key: "highlights", label: "Highlights", type: "list", add: "Add highlight" },
    ],
  },
  education: {
    kicker: "Education", noun: "education",
    fields: [
      { key: "institution", label: "Institution", wide: true }, { key: "degree", label: "Degree" },
      { key: "field_of_study", label: "Field of study" }, { key: "start_date", label: "Start" },
      { key: "end_date", label: "End", hint: "Add “(expected)” if you haven't finished yet." },
      { key: "grade", label: "Grade / GPA", optional: true },
      { key: "highlights", label: "Highlights", type: "list", add: "Add highlight" },
    ],
  },
  certifications: {
    kicker: "Certifications", noun: "certification",
    fields: [{ key: "name", label: "Name", wide: true }, { key: "issuer", label: "Issuer" }, { key: "date", label: "Date", optional: true }],
  },
};

const BASICS_FIELDS = [
  { key: "full_name", label: "Full name" }, { key: "location", label: "Location" },
  { key: "headline", label: "Headline", wide: true, placeholder: "e.g. AI Engineer building LLM applications" },
  { key: "email", label: "Email", type: "email" }, { key: "phone", label: "Phone", optional: true },
  { key: "summary", label: "Summary", type: "textarea", wide: true, rows: 5 },
  { key: "links", label: "Links", type: "links" },
];

const SECTIONS = [
  { id: "about", label: "About" },
  { id: "experience", label: "Experience", count: (p) => p.experience.length },
  { id: "projects", label: "Projects", count: (p) => p.projects.length },
  { id: "skills", label: "Skills", count: (p) => p.skills.reduce((n, g) => n + g.skills.length, 0) },
  { id: "education", label: "Education", count: (p) => p.education.length },
  { id: "certifications", label: "Certifications", count: (p) => p.certifications.length },
  { id: "achievements", label: "Achievements", count: (p) => p.achievements.length },
  { id: "context", label: "Additional context" },
];

function editButton(label, onClick) {
  return h("button", { type: "button", class: "icon-button", "aria-label": label, title: label, onClick }, icon("pencil"));
}

function sectionCard(id, title, action, ...body) {
  return h("section", { id: `sec-${id}`, class: "card profile-section", "aria-labelledby": `sec-${id}-title` },
    h("div", { class: "section-head" }, h("h2", { id: `sec-${id}-title`, class: "section-title", text: title }), action),
    body);
}

function addButton(text, onClick) {
  return h("button", { type: "button", class: "text-action", onClick }, icon("plus"), text);
}

function dateRange(item) {
  const end = item.is_current ? "Present" : item.end_date;
  return [item.start_date, end].filter(Boolean).join(" – ");
}

function bulletList(items) {
  return items.length ? h("ul", { class: "entry-list" }, items.map((text) => h("li", { text }))) : null;
}

function renderAbout(profile) {
  const b = profile.basics;
  const resume = profileRecord.resume;
  const links = b.links.filter((link) => safeUrl(link.url));

  const resumeBar = h("div", { class: "resume-bar" },
    h("span", { class: "resume-bar-text" },
      isUploading ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon("file", 18),
      h("span", {
        role: isUploading ? "status" : null,
        text: isUploading === "undo" ? "Restoring your previous profile…"
          : isUploading ? "Reading your resume…"
          : resume ? `Built from ${resume.filename} · uploaded ${formatWhen(resume.uploaded_at, true)}` : "No resume uploaded yet",
      })),
    h("span", { class: "resume-bar-actions" },
      resume ? h("a", { href: "/api/profile/resume", target: "_blank", rel: "noopener", text: "View PDF" }) : null,
      h("button", { type: "button", class: "link-button", disabled: isUploading, text: resume ? "Upload new resume" : "Upload resume", onClick: chooseResume }),
      profileRecord.can_undo_upload
        ? h("button", { type: "button", class: "link-button", disabled: isUploading, text: "Undo last upload", onClick: undoResumeUpload })
        : null,
      h("button", { type: "button", class: "link-button", text: "View JSON", onClick: () => openJsonViewer("Your profile JSON", profileRecord.profile) })),
  );

  return h("section", { id: "sec-about", class: "card profile-section", "aria-labelledby": "profile-name" },
    h("div", { class: "profile-head" },
      h("span", { class: "profile-avatar", "aria-hidden": "true", text: initialsFor(b.full_name, b.email) }),
      h("div", { class: "profile-identity" },
        h("h1", { id: "profile-name", text: b.full_name || "Your name" }),
        b.headline ? h("p", { class: "profile-headline", text: b.headline }) : h("p", { class: "placeholder-text", text: "Add a headline that sums up what you do." }),
        h("p", { class: "profile-meta" },
          b.location ? h("span", { text: b.location }) : null,
          b.email ? h("span", { text: b.email }) : null,
          b.phone ? h("span", { text: b.phone }) : null,
          links.map((link) => h("a", { href: link.url, target: "_blank", rel: "noopener noreferrer", text: link.label || link.url })))),
      h("button", { type: "button", class: "btn btn-secondary btn-sm", onClick: () => openBasicsEditor() }, icon("pencil", 15), "Edit")),
    b.summary ? h("p", { class: "profile-summary", text: b.summary }) : h("p", { class: "placeholder-text", text: "No summary yet." }),
    resumeBar,
  );
}

function renderOnboarding() {
  return h("section", { class: "onboarding", "aria-labelledby": "onboard-title" },
    h("h2", { id: "onboard-title", text: "Start with your resume" }),
    h("p", { class: "muted", text: "Upload a PDF and we'll fill in your profile. You can review and edit everything afterwards." }),
    isUploading
      ? h("div", { class: "progress", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }),
        h("div", {}, h("strong", { text: "Reading your resume" }), h("span", { text: "Turning it into your profile…" })))
      : h("div", { class: "row" },
        h("button", { type: "button", class: "btn btn-primary", onClick: chooseResume }, "Upload resume PDF"),
        h("button", { type: "button", class: "btn btn-secondary", onClick: () => openBasicsEditor() }, "Fill in manually")),
  );
}

function renderExperience(profile) {
  const items = profile.experience.map((item, index) => h("article", { class: "entry" },
    h("span", { class: "entry-badge", "aria-hidden": "true", text: (item.company || item.title || "?")[0].toUpperCase() }),
    h("div", { class: "entry-body" },
      h("div", { class: "entry-top" }, h("h3", { text: item.title || "Untitled role" }), h("span", { class: "entry-dates", text: dateRange(item) })),
      h("span", { class: "entry-sub", text: [item.company, item.location].filter(Boolean).join(" · ") }),
      bulletList(item.highlights)),
    editButton(`Edit ${item.title || "role"}${item.company ? ` at ${item.company}` : ""}`, () => openItemEditor("experience", index)),
  ));
  return sectionCard("experience", "Experience", addButton("Add role", () => openItemEditor("experience", null)),
    items.length ? items : h("p", { class: "empty-row", text: "No roles yet." }));
}

function renderProjects(profile) {
  const cards = profile.projects.map((item, index) => {
    const url = safeUrl(item.url);
    return h("article", { class: "project-card" },
      h("div", { class: "project-card-top" },
        h("h3", { text: item.name || "Untitled project" }),
        editButton(`Edit ${item.name || "project"}`, () => openItemEditor("projects", index))),
      bulletList(item.highlights),
      item.tech_stack.length ? h("div", { class: "chips" }, item.tech_stack.map((tech) => h("span", { class: "chip chip-neutral", text: tech }))) : null,
      url ? h("a", { class: "project-link", href: url, target: "_blank", rel: "noopener noreferrer", text: url.replace(/^https?:\/\/(www\.)?/, "") })
        : h("span", { class: "no-link", text: "No link yet" }),
    );
  });
  return sectionCard("projects", "Projects", addButton("Add project", () => openItemEditor("projects", null)),
    cards.length ? h("div", { class: "project-grid" }, cards) : h("p", { class: "empty-row", text: "No projects yet." }));
}

function renderSkills(profile) {
  const groups = profile.skills.filter((group) => group.skills.length);
  return sectionCard("skills", "Skills", h("button", { type: "button", class: "text-action", onClick: openSkillsEditor }, icon("pencil", 15), "Edit skills"),
    groups.length
      ? h("div", { class: "skill-groups" }, groups.map((group) => h("div", { class: "skill-group" },
        h("h3", { text: group.category || "Skills" }),
        h("div", { class: "chips" }, group.skills.map((skill) => h("span", { class: "chip", text: skill }))))))
      : h("p", { class: "empty-row", text: "No skills yet." }));
}

function renderEducation(profile) {
  const items = profile.education.map((item, index) => h("article", { class: "entry" },
    h("div", { class: "entry-body" },
      h("div", { class: "entry-top" },
        h("h3", { text: [item.degree, item.field_of_study].filter(Boolean).join(", ") || "Education" }),
        h("span", { class: "entry-dates", text: dateRange(item) })),
      h("span", { class: "entry-sub", text: [item.institution, item.grade].filter(Boolean).join(" · ") }),
      bulletList(item.highlights)),
    editButton(`Edit ${item.institution || "education"}`, () => openItemEditor("education", index)),
  ));
  return sectionCard("education", "Education", addButton("Add education", () => openItemEditor("education", null)),
    items.length ? items : h("p", { class: "empty-row", text: "No education yet." }));
}

function renderCertifications(profile) {
  const items = profile.certifications.map((item, index) => h("article", { class: "entry" },
    h("div", { class: "entry-body" },
      h("div", { class: "entry-top" }, h("h3", { text: item.name || "Certification" }), h("span", { class: "entry-dates", text: item.date })),
      item.issuer ? h("span", { class: "entry-sub", text: item.issuer }) : null),
    editButton(`Edit ${item.name || "certification"}`, () => openItemEditor("certifications", index)),
  ));
  return sectionCard("certifications", "Certifications", addButton("Add certification", () => openItemEditor("certifications", null)),
    items.length ? items : h("p", { class: "empty-row", text: "No certifications yet." }));
}

function renderAchievements(profile) {
  return sectionCard("achievements", "Achievements",
    h("button", { type: "button", class: "text-action", onClick: openAchievementsEditor }, icon("pencil", 15), profile.achievements.length ? "Edit" : "Add"),
    profile.achievements.length ? h("ul", { class: "entry-list" }, profile.achievements.map((text) => h("li", { text })))
      : h("p", { class: "empty-row", text: "No achievements yet." }));
}

function renderContext(profile) {
  if (!profile.additional_context) {
    return h("section", { id: "sec-context", class: "card profile-section context-empty", "aria-labelledby": "sec-context-title" },
      h("div", {},
        h("h2", { id: "sec-context-title", class: "section-title", text: "Additional context" }),
        h("p", { class: "muted small", text: "Anything true that isn't on your resume, such as client-facing work, tools you use daily or availability. The analysis reads it as fact." })),
      h("button", { type: "button", class: "btn btn-primary", onClick: openContextEditor }, "Add context"));
  }
  return sectionCard("context", "Additional context", h("button", { type: "button", class: "text-action", onClick: openContextEditor }, icon("pencil", 15), "Edit"),
    h("p", { class: "profile-summary", text: profile.additional_context }));
}

function profileTips(profile) {
  const tips = [];
  const noLink = profile.projects.findIndex((item) => !item.url);
  const noLinkCount = profile.projects.filter((item) => !item.url).length;
  if (!profile.basics.headline) tips.push(["Add a headline", () => openBasicsEditor()]);
  if (!profile.basics.summary) tips.push(["Write a short summary", () => openBasicsEditor()]);
  const bare = profile.experience.findIndex((item) => !item.highlights.length);
  if (bare !== -1) tips.push([`Add highlights to ${profile.experience[bare].title || "a role"}`, () => openItemEditor("experience", bare)]);
  if (noLinkCount) tips.push([`Add links to ${noLinkCount} project${noLinkCount > 1 ? "s" : ""}`, () => openItemEditor("projects", noLink)]);
  if (!profile.additional_context) tips.push(["Add context that isn't on your resume", openContextEditor]);
  return tips;
}

function renderProfile() {
  if (!profileRecord) return;
  const profile = profileRecord.profile;
  const main = el("profile-main");
  // replaceChildren() would print a literal "null", so drop absent parts first.
  main.replaceChildren(...[
    profileIsEmpty(profile) && !profileRecord.resume ? renderOnboarding() : null,
    renderAbout(profile),
    renderExperience(profile),
    renderProjects(profile),
    renderSkills(profile),
    renderEducation(profile),
    renderCertifications(profile),
    renderAchievements(profile),
    renderContext(profile),
  ].filter(Boolean));

  const nav = el("profile-nav-links");
  nav.replaceChildren(...SECTIONS.map((section) => h("a", {
    href: `#sec-${section.id}`,
    "data-section": section.id,
    onClick: (event) => { event.preventDefault(); el(`sec-${section.id}`).scrollIntoView({ behavior: "smooth", block: "start" }); },
  }, section.label, section.count ? h("span", { text: String(section.count(profile)) }) : null)));
  watchProfileSections();

  const tips = profileTips(profile);
  el("profile-tips").replaceChildren(...(tips.length
    ? tips.map(([text, action]) => h("li", {}, h("button", { type: "button", text, onClick: action })))
    : [h("li", { class: "tips-done", text: "Your profile looks complete." })]));
}

// Highlight the sidebar entry for whichever profile section is on screen.
let sectionObserver = null;
function watchProfileSections() {
  sectionObserver?.disconnect();
  const links = [...el("profile-nav-links").querySelectorAll("a")];
  const setActive = (id) => links.forEach((link) => {
    const active = link.dataset.section === id;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "true"); else link.removeAttribute("aria-current");
  });
  setActive("about");
  const onScreen = new Set();
  sectionObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) onScreen.add(entry.target.id); else onScreen.delete(entry.target.id);
    }
    const first = SECTIONS.find((section) => onScreen.has(`sec-${section.id}`));
    if (first) setActive(first.id);
  }, { rootMargin: "-15% 0px -55% 0px" });
  el("profile-main").querySelectorAll(".profile-section").forEach((section) => sectionObserver.observe(section));
}

// ============================================================
// Side panel: edit one thing at a time
// ============================================================

let drawer = null; // { initial, value, onSave, opener, readOnly }

function isDrawerDirty() {
  return Boolean(drawer) && !drawer.readOnly && JSON.stringify(drawer.value) !== drawer.initial;
}

function confirmDiscardDrawer() {
  if (!drawer) return true;
  if (isDrawerDirty() && !window.confirm("Discard your unsaved changes?")) return false;
  closeDrawer(true);
  return true;
}

function closeDrawer(force = false) {
  if (!drawer) return;
  if (!force && isDrawerDirty() && !window.confirm("Discard your unsaved changes?")) return;
  const opener = drawer.opener;
  drawer = null;
  el("drawer-root").classList.add("hidden");
  document.body.classList.remove("drawer-open");
  el("drawer-body").replaceChildren();
  if (opener && document.contains(opener)) opener.focus();
}

function openDrawer({ kicker, title, value, render, onSave, onRemove, readOnly = false, saveLabel = "Save" }) {
  if (drawer && !confirmDiscardDrawer()) return;
  drawer = { initial: JSON.stringify(value), value, onSave, readOnly, opener: document.activeElement };
  el("drawer-kicker").textContent = kicker;
  el("drawer-title").textContent = title;
  el("drawer-save").textContent = saveLabel;
  el("drawer-save").classList.toggle("hidden", readOnly);
  el("drawer-cancel").textContent = readOnly ? "Close" : "Cancel";
  el("drawer-remove").classList.toggle("hidden", !onRemove);
  el("drawer-remove").onclick = onRemove || null;
  el("drawer-body").replaceChildren(...[].concat(render(value)));
  el("drawer-root").classList.remove("hidden");
  document.body.classList.add("drawer-open");
  const first = el("drawer-body").querySelector("input, textarea, button");
  (first || el("drawer-close")).focus();
}

async function submitDrawer() {
  if (!drawer || drawer.readOnly) return;
  const save = el("drawer-save");
  save.disabled = true;
  try {
    if (await drawer.onSave(drawer.value)) closeDrawer(true);
  } finally {
    save.disabled = false;
  }
}

el("drawer-save").addEventListener("click", submitDrawer);
el("drawer-body").addEventListener("submit", (event) => { event.preventDefault(); submitDrawer(); });
el("drawer-cancel").addEventListener("click", () => closeDrawer());
el("drawer-close").addEventListener("click", () => closeDrawer());
el("drawer-scrim").addEventListener("click", () => closeDrawer());
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!accountDropdown.classList.contains("hidden")) { setAccountMenu(false); avatarBtn.focus(); return; }
  if (drawer) closeDrawer();
});

// ---- Field renderers (write straight into the object being edited) ----

let fieldSeq = 0;
const nextId = () => `f${++fieldSeq}`;

function fieldLabel(id, field) {
  return h("label", { for: id }, field.label, field.optional ? h("span", { class: "optional", text: " (optional)" }) : null);
}

function textField(target, field) {
  const id = nextId();
  const isArea = field.type === "textarea";
  const input = isArea
    ? h("textarea", { id, rows: String(field.rows || 4) })
    : h("input", { id, type: field.type === "email" || field.type === "url" ? field.type : "text", placeholder: field.placeholder || null });
  if (field.type === "csv") {
    input.value = target[field.key].join(", ");
    input.addEventListener("input", () => { target[field.key] = input.value.split(",").map((s) => s.trim()).filter(Boolean); });
  } else {
    input.value = target[field.key] ?? "";
    input.addEventListener("input", () => { target[field.key] = input.value; });
  }
  return h("div", { class: `field${field.wide || isArea ? " field-wide" : ""}` },
    fieldLabel(id, field), input, field.hint ? h("p", { class: "hint", text: field.hint }) : null);
}

function checkboxField(target, field) {
  const input = h("input", { type: "checkbox", checked: Boolean(target[field.key]) });
  input.addEventListener("change", () => { target[field.key] = input.checked; });
  return h("label", { class: "check field-wide" }, input, field.label);
}

// A list of rows the user can add, remove and reorder. `renderRow(item, index)`
// returns the row's inputs; the list re-renders itself after structural changes.
function listEditor(items, { legend, addLabel, blank, renderRow, rowClass = "list-row", noun = "item" }) {
  const box = h("fieldset", { class: "list-editor field-wide" });
  const draw = (focusIndex = null) => {
    box.replaceChildren(h("legend", { text: legend }));
    items.forEach((item, index) => {
      const controls = h("span", { class: "list-row-controls" },
        index > 0 ? h("button", { type: "button", class: "icon-button", "aria-label": `Move ${noun} ${index + 1} up`, onClick: () => { [items[index - 1], items[index]] = [items[index], items[index - 1]]; draw(index - 1); } }, icon("up", 15)) : null,
        index < items.length - 1 ? h("button", { type: "button", class: "icon-button", "aria-label": `Move ${noun} ${index + 1} down`, onClick: () => { [items[index + 1], items[index]] = [items[index], items[index + 1]]; draw(index + 1); } }, icon("down", 15)) : null,
        h("button", { type: "button", class: "icon-button", "aria-label": `Remove ${noun} ${index + 1}`, onClick: () => { items.splice(index, 1); draw(); } }, icon("close", 15)));
      box.append(renderRow(item, index, controls));
    });
    box.append(h("button", { type: "button", class: "add-row", onClick: () => { items.push(blank()); draw(items.length - 1); } }, `+ ${addLabel}`));
    if (focusIndex !== null) box.querySelectorAll(`.${rowClass.split(" ")[0]}`)[focusIndex]?.querySelector("input")?.focus();
  };
  draw();
  return box;
}

function stringListField(target, field) {
  const items = target[field.key];
  return listEditor(items, {
    legend: field.label, addLabel: field.add || "Add", blank: () => "", noun: field.label.toLowerCase().replace(/s$/, ""),
    renderRow: (value, index, controls) => {
      const input = h("input", { type: "text", value, "aria-label": `${field.label} ${index + 1}` });
      input.addEventListener("input", () => { items[index] = input.value; });
      return h("div", { class: "list-row" }, input, controls);
    },
  });
}

function linksField(target, field) {
  const items = target[field.key];
  return listEditor(items, {
    legend: field.label, addLabel: "Add link", blank: () => ({ label: "", url: "" }), noun: "link",
    renderRow: (link, index, controls) => {
      const label = h("input", { type: "text", value: link.label, placeholder: "Label, e.g. GitHub", "aria-label": `Link ${index + 1} label` });
      const url = h("input", { type: "url", value: link.url, placeholder: "https://…", "aria-label": `Link ${index + 1} URL` });
      label.addEventListener("input", () => { link.label = label.value; });
      url.addEventListener("input", () => { link.url = url.value; });
      return h("div", { class: "list-row" }, label, url, controls);
    },
  });
}

function renderFields(target, fields) {
  const grid = h("div", { class: "field-grid" });
  for (const field of fields) {
    if (field.type === "checkbox") grid.append(checkboxField(target, field));
    else if (field.type === "list") grid.append(stringListField(target, field));
    else if (field.type === "links") grid.append(linksField(target, field));
    else grid.append(textField(target, field));
  }
  return grid;
}

// Drop blank rows so saves don't store empty highlights or links.
function tidy(value) {
  for (const [key, item] of Object.entries(value)) {
    if (!Array.isArray(item)) continue;
    value[key] = item
      .map((entry) => (typeof entry === "string" ? entry.trim() : entry))
      .filter((entry) => (typeof entry === "string" ? entry : Object.values(entry).some((v) => (Array.isArray(v) ? v.length : String(v).trim()))));
  }
  return value;
}

function blankItem(fields) {
  const item = {};
  for (const field of fields) item[field.key] = field.type === "checkbox" ? false : ["list", "csv"].includes(field.type) ? [] : "";
  return item;
}

// ---- Editors ----

function openItemEditor(section, index) {
  const config = ITEM_EDITORS[section];
  const isNew = index === null;
  const value = isNew ? blankItem(config.fields) : clone(profileRecord.profile[section][index]);
  openDrawer({
    kicker: config.kicker,
    title: isNew ? `Add ${config.noun}` : `Edit ${config.noun}`,
    value,
    saveLabel: isNew ? `Add ${config.noun}` : `Save ${config.noun}`,
    render: (v) => renderFields(v, config.fields),
    onSave: (v) => saveProfileWith((p) => {
      const item = tidy(v);
      if (isNew) p[section].push(item); else p[section][index] = item;
    }, isNew ? `Added ${config.noun}.` : "Saved."),
    onRemove: isNew ? null : async () => {
      if (!window.confirm(`Remove this ${config.noun} from your profile?`)) return;
      if (await saveProfileWith((p) => { p[section].splice(index, 1); }, `Removed ${config.noun}.`)) closeDrawer(true);
    },
  });
}

function openBasicsEditor() {
  openDrawer({
    kicker: "About", title: "Edit basics",
    value: clone(profileRecord.profile.basics),
    render: (v) => renderFields(v, BASICS_FIELDS),
    onSave: (v) => saveProfileWith((p) => { p.basics = tidy(v); }),
  });
}

function openSkillsEditor() {
  const value = { skills: clone(profileRecord.profile.skills) };
  openDrawer({
    kicker: "Skills", title: "Edit skills", value,
    render: (v) => listEditor(v.skills, {
      legend: "Skill groups", addLabel: "Add group", blank: () => ({ category: "", skills: [] }), noun: "group", rowClass: "group-row",
      renderRow: (group, index, controls) => {
        const category = h("input", { type: "text", value: group.category, placeholder: "Category, e.g. Languages", "aria-label": `Group ${index + 1} name` });
        const skills = h("textarea", { rows: "2", "aria-label": `Group ${index + 1} skills, separated by commas` });
        skills.value = group.skills.join(", ");
        category.addEventListener("input", () => { group.category = category.value; });
        skills.addEventListener("input", () => { group.skills = skills.value.split(",").map((s) => s.trim()).filter(Boolean); });
        return h("div", { class: "group-row" }, h("div", { class: "group-row-head" }, category, controls), skills,
          h("p", { class: "hint", text: "Separate skills with commas." }));
      },
    }),
    onSave: (v) => saveProfileWith((p) => {
      p.skills = v.skills.filter((group) => group.category.trim() || group.skills.length);
    }),
  });
}

function openAchievementsEditor() {
  openDrawer({
    kicker: "Achievements", title: "Edit achievements",
    value: { achievements: clone(profileRecord.profile.achievements) },
    render: (v) => renderFields(v, [{ key: "achievements", label: "Achievements", type: "list", add: "Add achievement" }]),
    onSave: (v) => saveProfileWith((p) => { p.achievements = tidy(v).achievements; }),
  });
}

function openContextEditor() {
  openDrawer({
    kicker: "Not on your resume", title: "Additional context",
    value: { additional_context: profileRecord.profile.additional_context },
    render: (v) => [
      h("p", { class: "muted small", text: "Tell the analysis anything true that isn't on your resume: client-facing work, tools you use daily, availability, visa status. It's treated as fact, so keep it accurate." }),
      renderFields(v, [{ key: "additional_context", label: "Additional context", type: "textarea", rows: 10 }]),
    ],
    onSave: (v) => saveProfileWith((p) => { p.additional_context = v.additional_context.trim(); }),
  });
}

function openJsonViewer(title, data) {
  const text = JSON.stringify(data, null, 2);
  openDrawer({
    kicker: "Sent to the analysis", title, value: {}, readOnly: true,
    render: () => [
      h("p", { class: "muted small", text: "This is exactly what the analysis model reads about you." }),
      h("button", {
        type: "button", class: "btn btn-secondary btn-sm", style: "align-self: flex-start",
        onClick: async () => {
          try { await navigator.clipboard.writeText(text); toast("Copied.", "ok"); } catch { toast("Couldn't copy to the clipboard.", "error"); }
        },
      }, "Copy JSON"),
      h("pre", { class: "json-view", text }),
    ],
  });
}

window.addEventListener("beforeunload", (event) => {
  if (isDrawerDirty() || isAnalyzing || isUploading) event.preventDefault();
});

// ============================================================
// Boot
// ============================================================

(async () => {
  hydrateIcons();
  setAuthMode("login");
  try {
    const me = await api("/api/auth/me");
    await enterApp(me.email);
  } catch {
    showAuthScreen();
  }
})();
