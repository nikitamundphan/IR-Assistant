const API_BASE = import.meta.env.VITE_API_BASE || "";

// Sessions live in the server's memory, so a server restart makes the browser's saved session id
// invalid ("Invalid or expired session"). When that happens, quietly sign in again as the same
// user (env login) and retry the request once, instead of failing in the middle of a chat.
const renewedSessions = new Map();
let renewing = null;

async function renewSession(oldId) {
  if (!renewing) {
    renewing = (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/login/env`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        if (!res.ok) return "";
        const body = await res.json();
        // Never switch the signed-in user behind their back.
        if (body.username !== localStorage.getItem("ir_username")) return "";
        localStorage.setItem("ir_session_id", body.session_id);
        return body.session_id;
      } catch {
        return "";
      } finally {
        setTimeout(() => { renewing = null; }, 0);
      }
    })();
  }
  const fresh = await renewing;
  if (fresh) renewedSessions.set(oldId, fresh);
  return fresh;
}

async function apiFetch(url, init = {}) {
  const headers = { ...(init.headers || {}) };
  const original = headers["X-Session-Id"];
  if (original && renewedSessions.has(original)) headers["X-Session-Id"] = renewedSessions.get(original);

  const res = await fetch(url, { ...init, headers });
  if (res.status !== 401 || !original) return res;

  const body = await res.clone().json().catch(() => ({}));
  if (!/expired session/i.test(String(body.detail || ""))) return res;

  const fresh = await renewSession(original);
  if (!fresh) return res;
  return fetch(url, { ...init, headers: { ...headers, "X-Session-Id": fresh } });
}

function authHeaders(sessionId) {
  const headers = { "Content-Type": "application/json" };
  if (sessionId) headers["X-Session-Id"] = sessionId;
  return headers;
}

function dsxAnswerText(block) {
  if (!block || typeof block !== "object") return "";
  const msg = block.msg;
  if (msg && typeof msg === "object" && msg.value) return String(msg.value);
  if (typeof msg === "string") return msg;
  return "";
}

function formatDsxDetails(details) {
  if (details == null) return "";
  if (typeof details === "string") return details;
  if (Array.isArray(details)) {
    return details
      .map((item) => {
        if (item && typeof item === "object") {
          return item.message || item.msg || item.text || JSON.stringify(item);
        }
        return String(item);
      })
      .join("; ");
  }
  if (typeof details === "object") {
    for (const section of ["diagnostic", "advice", "request"]) {
      const text = dsxAnswerText(details[section]);
      if (text) {
        const advice = dsxAnswerText(details.advice);
        return advice && advice !== text ? `${text} (${advice})` : text;
      }
    }
    if (details.message) return details.message;
    if (details.error && typeof details.error === "string") return details.error;
    if (Array.isArray(details.messages)) return formatDsxDetails(details.messages);
    if (details.answer?.errors) return formatDsxDetails(details.answer.errors);
    if (details.answer?.message) return details.answer.message;
    try {
      return JSON.stringify(details);
    } catch {
      return "Unknown DSX error";
    }
  }
  return String(details);
}

async function parseError(res) {
  const err = await res.json().catch(() => ({}));
  const detail = err.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = item.loc?.slice(-1)[0] || "field";
        return `${field}: ${item.msg}`;
      })
      .join("; ");
  }

  if (detail && typeof detail === "object") {
    if (detail.message) {
      let msg = detail.message;
      if (Array.isArray(detail.probe) && detail.probe.length > 0) {
        const lines = detail.probe.slice(0, 4).map((entry) => {
          const path = entry.path || "?";
          const params = entry.params && typeof entry.params === "object"
            ? Object.entries(entry.params).map(([k, v]) => `${k}=${v}`).join("&")
            : "";
          const q = params ? `?${params}` : "";
          return `${path}${q} (${entry.status}, ${entry.count ?? 0} items)`;
        });
        msg += `\n\nAPI attempts: ${lines.join("; ")}`;
      }
      return msg;
    }
    if (detail.error && detail.details) {
      const dsx = formatDsxDetails(detail.details);
      return dsx ? `${detail.error} — ${dsx}` : detail.error;
    }
    if (detail.error) return detail.error;
    return formatDsxDetails(detail);
  }

  return "Request failed";
}

export async function fetchHealth() {
  const res = await apiFetch(`${API_BASE}/api/health`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** emxNavigator.jsp link for a DSX physical ID (release, IR, etc.). */
export function buildDsxNavigatorUrl(health, physicalId) {
  const pid = String(physicalId || "").trim();
  if (!pid) return "";
  const base = String(health?.dsx_navigator_base || "").replace(/\/$/, "");
  if (!base) return "";
  return `${base}/common/emxNavigator.jsp?physicalId=${encodeURIComponent(pid)}`;
}

export async function login(username, password) {
  const res = await apiFetch(`${API_BASE}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function loginFromEnv() {
  const res = await apiFetch(`${API_BASE}/api/login/env`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function logout(sessionId) {
  await apiFetch(`${API_BASE}/api/logout`, {
    method: "POST",
    headers: authHeaders(sessionId),
  });
}

export async function fetchMe(sessionId) {
  const res = await apiFetch(`${API_BASE}/api/me`, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchIrFieldSchema() {
  const res = await apiFetch(`${API_BASE}/api/ir-field-schema`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchDsxBrands(sessionId) {
  const res = await apiFetch(`${API_BASE}/api/dsx/brands`, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchDsxServices(sessionId, brandId = "") {
  const params = new URLSearchParams();
  if (brandId) params.set("brand_id", brandId);
  const query = params.toString();
  const url = `${API_BASE}/api/dsx/services${query ? `?${query}` : ""}`;
  const res = await apiFetch(url, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchDsxPrograms(sessionId, { serviceId = "", brandId = "" } = {}) {
  const params = new URLSearchParams();
  if (serviceId) params.set("service_id", serviceId);
  if (brandId) params.set("brand_id", brandId);
  const query = params.toString();
  if (!query) throw new Error("serviceId or brandId is required");
  const res = await apiFetch(`${API_BASE}/api/dsx/programs?${query}`, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchDsxReleases(sessionId, programId) {
  const params = new URLSearchParams({ program_id: programId });
  const res = await apiFetch(`${API_BASE}/api/dsx/releases?${params}`, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function resolveReleaseContext(sessionId, payload) {
  const res = await apiFetch(`${API_BASE}/api/dsx/resolve-release-context`, {
    method: "POST",
    headers: authHeaders(sessionId),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function resolveDsxRelease(sessionId, payload) {
  const res = await apiFetch(`${API_BASE}/api/dsx/resolve-release`, {
    method: "POST",
    headers: authHeaders(sessionId),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function resolveDetectionLevel(sessionId, payload) {
  const res = await apiFetch(`${API_BASE}/api/dsx/resolve-detection-level`, {
    method: "POST",
    headers: authHeaders(sessionId),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchSavedIrForms(sessionId) {
  const res = await apiFetch(`${API_BASE}/api/saved-ir-forms`, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchMyIncidents(sessionId) {
  const res = await apiFetch(`${API_BASE}/api/my-incidents`, { headers: authHeaders(sessionId) });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function resolveIrReferences(sessionId, payload) {
  const res = await apiFetch(`${API_BASE}/api/resolve-ir-references`, {
    method: "POST",
    headers: authHeaders(sessionId),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createIncidentReport(sessionId, payload) {
  const res = await apiFetch(`${API_BASE}/api/create-ir`, {
    method: "POST",
    headers: authHeaders(sessionId),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function searchIncidentReports(sessionId, query, feature) {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (feature) params.set("feature", feature);
  const res = await apiFetch(`${API_BASE}/api/search-ir?${params}`, { headers: authHeaders(sessionId) });
  if (!res.ok) return [];
  const body = await res.json();
  return body.items || [];
}

export async function uploadIrMedia(sessionId, irId, file, documentId) {
  const formData = new FormData();
  formData.append("file", file);
  const params = new URLSearchParams();
  if (documentId) params.set("document_id", documentId);
  const query = params.toString();
  const res = await apiFetch(
    `${API_BASE}/api/upload-ir-media/${encodeURIComponent(irId)}${query ? `?${query}` : ""}`,
    {
      method: "POST",
      headers: { "X-Session-Id": sessionId },
      body: formData,
    },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
