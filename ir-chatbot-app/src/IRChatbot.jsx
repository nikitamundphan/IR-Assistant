import React, { useState, useRef, useEffect } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Bot,
  User,
  ShieldAlert,
  Paperclip,
  Send,
  LogOut,
  FileText,
  Plus,
  ExternalLink,
  Sparkles,
  ChevronRight,
  Layers,
  Zap,
} from "lucide-react";
import {
  login,
  loginFromEnv,
  fetchHealth,
  buildDsxNavigatorUrl,
  logout,
  fetchIrFieldSchema,
  fetchDsxBrands,
  fetchDsxServices,
  fetchDsxPrograms,
  fetchDsxReleases,
  resolveReleaseContext,
  resolveDsxRelease,
  resolveDetectionLevel,
  resolveIrReferences,
  fetchSavedIrForms,
  fetchMyIncidents,
  createIncidentReport,
  searchIncidentReports,
  uploadIrMedia,
} from "./api.js";

const SEVERITIES = [
  { label: "Critical", value: "1", color: "#EF4444", desc: "Unusable / blocking" },
  { label: "High", value: "2", color: "#F97316", desc: "Major function broken" },
  { label: "Medium", value: "3", color: "#EAB308", desc: "Workaround exists" },
  { label: "Low", value: "4", color: "#10B981", desc: "Minor / cosmetic" },
];

const ENVIRONMENTS = ["Cloud", "On-Premise", "Windows", "Linux", "MacOS"];

const DETAIL_KEYS = [
  "rel_eno_id", "rel_name", "rel_title", "rel_level_id",
  "feature_eno_id", "feature_name",
  "detection_level_eno_id", "detection_level_name", "detection_level_title",
  "default_clarifier", "default_corrector", "default_validator", "owner",
];

const FALLBACK_FORM_SECTIONS = [
  {
    id: "release",
    label: "Target release (fix version)",
    intro: "The release where the issue should be fixed — not the build where you found it.",
    fields: [
      { key: "rel_title", label: "Release title", prompt: "What is the release title (display name)?", hint: "Human-readable release name (e.g. R2026x GA).", placeholder: "e.g. R2026x GA", type: "text" },
      { key: "rel_name", label: "Release short name", prompt: "What is the release short name / code?", hint: "Internal release code (e.g. REL002009).", placeholder: "e.g. REL002009", type: "text" },
      { key: "rel_level_id", label: "Release level ID", prompt: "What is the release level ID?", hint: "Usually the same as the release code.", placeholder: "e.g. REL002009", type: "text" },
    ],
  },
  {
    id: "feature",
    label: "Concerned feature",
    intro: "The product feature or functional area affected.",
    fields: [
      { key: "feature_name", label: "Feature name", prompt: "Which feature is affected?", hint: "Display name of the feature or collection.", placeholder: "Feature name", type: "text" },
    ],
  },
  {
    id: "detection",
    label: "Issue detected version",
    intro: "The program or build level where you detected the issue.",
    fields: [
      {
        key: "detection_level_name",
        label: "Issue detected version",
        prompt: "Which version did you detect this issue in?",
        hint: "Program code or version label (e.g. PRG044546). The ID is looked up automatically.",
        placeholder: "e.g. PRG044546",
        type: "text",
      },
    ],
  },
  {
    id: "actors",
    label: "Clarifier, corrector & validator",
    intro: "DS login (trigram) of the people who clarify, correct, and validate this IR.",
    fields: [
      { key: "default_clarifier", label: "Clarifier", prompt: "Who is the clarifier (DS login)?", hint: "Usually your trigram.", placeholder: "e.g. nmn38", type: "text" },
      { key: "default_corrector", label: "Corrector", prompt: "Who is the corrector (DS login)?", hint: "Developer responsible for the fix.", placeholder: "Trigram", type: "text" },
      { key: "default_validator", label: "Validator", prompt: "Who is the validator (DS login)?", hint: "Person who validates the fix.", placeholder: "Trigram", type: "text" },
      { key: "owner", label: "Owner", prompt: "IR owner (DS login)? Leave blank to use your login.", hint: "Defaults to your account if skipped.", placeholder: "Optional", type: "text", optional: true },
    ],
  },
];

function buildLabelsFromSections(sections) {
  const labels = {
    title: "Title",
    description: "Description",
    severity: "Severity",
    detected_environment: "Detected environment",
    env: "Environment URL",
    aura: "AURA version",
    swym: "SWYM version",
    swymUi: "SWYM UI version",
  };
  sections.forEach((section) => {
    section.fields.forEach((field) => {
      labels[field.key] = field.label;
    });
  });
  return labels;
}

function flattenFormSections(sections, defaults = {}) {
  const questions = [];
  sections.forEach((section) => {
    const visibleFields = section.fields.filter((field) => !field.internal);
    visibleFields.forEach((field, fieldIndex) => {
      questions.push({
        ...field,
        type: field.type || "text",
        sectionId: section.id,
        sectionLabel: section.label,
        sectionIntro: section.intro,
        isFirstInSection: fieldIndex === 0,
        default: defaults[field.key] || "",
      });
    });
  });
  return questions;
}

function modifyOptionsFromSections(sections) {
  return sections.map((section) => ({
    id: section.id,
    label: section.label,
    keys: section.fields.filter((field) => !field.internal).map((f) => f.key),
  }));
}

const DETECTION_FIELD_KEYS = new Set(["detection_level_name", "detection_level_title"]);

function isDetectionFieldKey(key) {
  return DETECTION_FIELD_KEYS.has(key);
}

function detectionQueryFromMerged(merged) {
  const name = String(merged.detection_level_name ?? "").trim();
  const title = String(merged.detection_level_title ?? "").trim();
  return { name: name || title, title: title || name };
}

function normalizeDetectionSection(sections) {
  if (!Array.isArray(sections)) return FALLBACK_FORM_SECTIONS;
  const fallbackDetection = FALLBACK_FORM_SECTIONS.find((s) => s.id === "detection");
  return sections.map((section) => {
    if (section.id !== "detection" || !fallbackDetection) return section;
    const enoField = section.fields?.find((f) => f.key === "detection_level_eno_id") || {
      key: "detection_level_eno_id",
      label: "Detection program reference",
      internal: true,
    };
    return {
      ...section,
      label: fallbackDetection.label,
      intro: fallbackDetection.intro,
      fields: [
        ...fallbackDetection.fields,
        { key: "detection_level_title", label: "Detection level title", internal: true },
        { ...enoField, internal: true },
      ],
    };
  });
}

function formatQuestionMessage(question) {
  if (!question) return "";
  const parts = [];
  if (question.isFirstInSection && question.sectionIntro) {
    parts.push(`${question.sectionLabel}\n${question.sectionIntro}`);
  }
  parts.push(question.prompt);
  if (question.hint) parts.push(`Tip: ${question.hint}`);
  return parts.join("\n\n");
}

const USER_REQUIRED_KEYS = [
  "title",
  "description",
  "severity",
  "detected_environment",
  "default_clarifier",
  "default_corrector",
  "default_validator",
  "rel_name",
  "rel_title",
  "rel_level_id",
  "feature_name",
  "detection_level_name",
];

const INTERNAL_FIELD_KEYS = new Set(["rel_eno_id", "feature_eno_id", "detection_level_eno_id"]);

function mainMenuGreeting(user) {
  const name = user ? `Hello, **${user}**!` : "Hello!";
  return (
    `${name} I'm here to help you create an **Incident Report (IR)**.\n\n`
    + "Choose what you'd like to do:\n"
    + "1. **Create IR using a saved form**\n"
    + "2. **Create IR without a saved form**\n"
    + "3. **Set target release** (get the correct release ID / version for filing)"
  );
}

const FLOW_STEPS = [
  { key: "context", label: "Service", match: (p) => ["brand_pick", "service_pick", "program_pick", "release_pick"].includes(p) },
  { key: "setup", label: "Setup", match: (p) => ["dashboard", "saved_form_list", "saved_review", "modify_pick", "manual_form", "release_name_ask"].includes(p) },
  { key: "details", label: "Details", match: (p) => p === "asking" },
  { key: "review", label: "Review", match: (p) => ["duplicates", "review"].includes(p) },
  { key: "filed", label: "Filed", match: (p) => ["creating", "uploading", "done"].includes(p) },
];

function formatDsxProbeLine(entry) {
  const path = entry.path || "?";
  let paramNote = "";
  if (entry.param) {
    paramNote = `?${entry.param}=${entry.param_value ?? ""}`;
  } else {
    const params = entry.params || {};
    const keys = Object.keys(params);
    paramNote =
      keys.length === 1 ? `?${keys[0]}=${params[keys[0]]}` : keys.length > 1 ? `?${keys.join("&")}` : "";
  }
  const detail = entry.detail ? ` — ${String(entry.detail).replace(/\s+/g, " ").slice(0, 80)}` : "";
  return `${path}${paramNote}→${entry.status}${entry.count ? `(${entry.count})` : ""}${detail}`;
}

function formSectionsWithoutRelease(formSections, hasReleaseContext) {
  if (!hasReleaseContext) return formSections;
  return formSections.filter((section) => section.id !== "release");
}

function modifyOptionsForContext(formSections, hasReleaseContext) {
  const options = allModifyOptions(formSections);
  if (!hasReleaseContext) return options;
  return options.filter((opt) => opt.id !== "release");
}

function activeStepIndex(phase) {
  const idx = FLOW_STEPS.findIndex((s) => s.match(phase));
  return idx >= 0 ? idx : 0;
}

function computeDraftProgress(payload) {
  const filled = USER_REQUIRED_KEYS.filter((k) => payload[k] && String(payload[k]).trim()).length;
  return Math.round((filled / USER_REQUIRED_KEYS.length) * 100);
}

function getMissingPayloadFields(payload, fieldLabels) {
  return USER_REQUIRED_KEYS
    .filter((key) => !payload[key] || String(payload[key]).trim() === "")
    .map((key) => fieldLabels[key] || key);
}

function buildSavedFormSummary(form, fieldLabels) {
  if (!form?.fields) return "No saved details found.";
  const lines = Object.entries(form.fields)
    .filter(([key, value]) => value && !INTERNAL_FIELD_KEYS.has(key))
    .map(([key, value]) => `• ${fieldLabels[key] || key}: ${value}`);
  return lines.length ? lines.join("\n") : "No saved details found.";
}

function keywordOverlapScore(a, b) {
  const norm = (s) => s.toLowerCase().replace(/[^a-z0-9 ]/g, "").split(" ").filter((w) => w.length > 3);
  const wa = new Set(norm(a));
  const wb = new Set(norm(b));
  if (wa.size === 0 || wb.size === 0) return 0;
  let hits = 0;
  wa.forEach((w) => { if (wb.has(w)) hits++; });
  return hits / Math.min(wa.size, wb.size);
}

function descriptionHasSection(text, markers) {
  const lower = text.toLowerCase();
  return markers.some((m) => lower.includes(m.toLowerCase()));
}

function buildDescription(answers) {
  const parts = [];
  const base = answers.description?.trim();
  if (base) parts.push(base);

  const envLines = [
    answers.env && answers.env.toLowerCase() !== "skip" && `ENV: ${answers.env}`,
    answers.aura && `AURA: ${answers.aura}`,
    answers.swym && `SWYM: ${answers.swym}`,
    answers.swymUi && `SWYM UI: ${answers.swymUi}`,
  ].filter(Boolean);

  const combined = parts.join("\n\n");
  if (envLines.length && !descriptionHasSection(combined, ["environment:", "env:"])) {
    parts.push(`Environment:\n${envLines.join("\n")}`);
  }
  if (answers.steps && !descriptionHasSection(combined, ["steps to reproduce"])) {
    parts.push(`**Steps to Reproduce:**\n${answers.steps}`);
  }
  if (answers.expected && !descriptionHasSection(combined, ["expected result"])) {
    parts.push(`**Expected Result:**\n${answers.expected}`);
  }
  if (answers.actual && !descriptionHasSection(combined, ["actual result"])) {
    parts.push(`**Actual Result:**\n${answers.actual}`);
  }
  if (answers.attachment && !descriptionHasSection(combined, ["pfa attach"])) {
    parts.push(`PFA attach ${answers.attachment.type?.startsWith("video") ? "video" : "screenshot"} for reference`);
  }
  return parts.join("\n\n");
}

const LINKED_FIELD_GROUPS = [
  { sectionId: "release", displayKeys: ["rel_name", "rel_title", "rel_level_id"], enoKey: "rel_eno_id" },
  { sectionId: "feature", displayKeys: ["feature_name"], enoKey: "feature_eno_id" },
  { sectionId: "detection", displayKeys: ["detection_level_name", "detection_level_title"], enoKey: "detection_level_eno_id" },
];

const LINKED_FIELD_LABELS = {
  rel_eno_id: "Target release",
  feature_eno_id: "Feature",
  detection_level_eno_id: "Detection program",
};

function applyLinkedFieldUpdate(fields, key, value) {
  const next = { ...fields, [key]: value };
  for (const { displayKeys, enoKey } of LINKED_FIELD_GROUPS) {
    if (displayKeys.includes(key)) {
      delete next[enoKey];
    }
  }
  return next;
}

function mergeFieldUpdates(fields, updates) {
  let next = { ...fields };
  for (const [key, value] of Object.entries(updates)) {
    next = applyLinkedFieldUpdate(next, key, value);
  }
  return next;
}

function displayChangedFromBaseline(merged, baseline, displayKeys) {
  return displayKeys.some((k) => {
    const cur = String(merged[k] ?? "").trim();
    const base = String(baseline[k] ?? "").trim();
    return cur && base && cur !== base;
  });
}

const INCIDENT_DESCRIPTION_MARKERS = {
  env: ["environment:", "env:", "http://", "https://"],
  aura: ["aura"],
  swym: ["swym"],
  swymUi: ["swym ui"],
  steps: ["steps to reproduce", "steps:"],
  expected: ["expected result", "expected:"],
  actual: ["actual result", "actual:"],
};

function incidentDetailCovered(key, prefill) {
  const value = prefill[key];
  if (value && String(value).trim() && String(value).trim() !== ".") return true;
  const desc = prefill.description?.trim() || "";
  const markers = INCIDENT_DESCRIPTION_MARKERS[key];
  return markers ? descriptionHasSection(desc, markers) : false;
}

function getMissingOidIssues(merged) {
  const issues = [];
  if (!merged.rel_eno_id && (merged.rel_name || merged.rel_title)) {
    issues.push("Target release");
  }
  if (!merged.feature_eno_id && merged.feature_name) {
    issues.push("Feature");
  }
  if (!merged.detection_level_eno_id && (merged.detection_level_name || merged.detection_level_title)) {
    issues.push("Detection program");
  }
  return issues;
}

function getStaleOidIssues(merged, baseline, editedSections = null) {
  const issues = [];
  for (const { sectionId, displayKeys, enoKey } of LINKED_FIELD_GROUPS) {
    if (editedSections && sectionId && !editedSections.has(sectionId)) continue;
    if (!merged[enoKey]) continue;
    if (!displayChangedFromBaseline(merged, baseline, displayKeys)) continue;
    if (String(merged[enoKey]) === String(baseline[enoKey] ?? "")) {
      issues.push(LINKED_FIELD_LABELS[enoKey] || enoKey);
    }
  }
  return issues;
}

const RELEASE_DISPLAY_KEYS = ["rel_name", "rel_title", "rel_level_id"];
const DETECTION_DISPLAY_KEYS = ["detection_level_name", "detection_level_title"];

function detectionChangedFromBaseline(fields, baseline) {
  return displayChangedFromBaseline(fields, baseline, DETECTION_DISPLAY_KEYS);
}

function releaseChangedFromBaseline(fields, baseline) {
  return displayChangedFromBaseline(fields, baseline, RELEASE_DISPLAY_KEYS);
}

function featureChangedFromBaseline(fields, baseline) {
  return displayChangedFromBaseline(fields, baseline, ["feature_name"]);
}

function getOidMismatchIssues(merged, baseline, editedSections = null) {
  const issues = [];
  if (
    (!editedSections || editedSections.has("detection"))
    && detectionChangedFromBaseline(merged, baseline)
    && merged.detection_level_eno_id
    && String(merged.detection_level_eno_id) === String(baseline.detection_level_eno_id ?? "")
  ) {
    issues.push("Detection program");
  }
  if (
    (!editedSections || editedSections.has("release"))
    && releaseChangedFromBaseline(merged, baseline)
    && merged.rel_eno_id
    && String(merged.rel_eno_id) === String(baseline.rel_eno_id ?? "")
  ) {
    issues.push("Target release");
  }
  if (
    (!editedSections || editedSections.has("feature"))
    && featureChangedFromBaseline(merged, baseline)
    && merged.feature_eno_id
    && String(merged.feature_eno_id) === String(baseline.feature_eno_id ?? "")
  ) {
    issues.push("Feature");
  }
  return issues;
}

function applyBaselineOidsForUneditedSections(payload, baseline, editedSections) {
  if (!editedSections?.has("release") && !payload.rel_eno_id && baseline.rel_eno_id) {
    payload.rel_eno_id = baseline.rel_eno_id;
  }
  if (!editedSections?.has("feature") && !payload.feature_eno_id && baseline.feature_eno_id) {
    payload.feature_eno_id = baseline.feature_eno_id;
  }
}

function buildLinkErrorMessage(parts) {
  const unique = [...new Set(parts)];
  let hint = "Check those fields in the review panel.";
  const needsDashboard = unique.some(
    (part) => part === "Target release" || part === "Detection program",
  );
  if (needsDashboard) {
    hint += " Use Set target release on the dashboard to pick release/program from a list.";
  }
  return `Could not link ${unique.join(", ")}. ${hint}`;
}

function detectionFieldsFromProgram(program) {
  const enoId = program.physicalid || program.id || "";
  const name = program.name || program.title || "";
  const title = program.title || program.name || name;
  return {
    detection_level_eno_id: enoId,
    detection_level_name: name,
    detection_level_title: title,
  };
}

const ALWAYS_ASK_INCIDENT_KEYS = new Set(["description", "title"]);

const ISSUE_MODIFY_OPTIONS = [
  { id: "title", label: "Title", keys: ["title"] },
  { id: "description", label: "Description", keys: ["description"] },
];

function buildIssueEditQuestions(defaults = {}) {
  return [
    {
      key: "description",
      label: "Description",
      prompt: "What is the issue?",
      hint: "Describe what happened.",
      type: "textarea",
      default: defaults.description || "",
    },
    {
      key: "title",
      label: "Title",
      prompt: "What is the title of this issue?",
      hint: "A short summary of the issue.",
      type: "text",
      default: defaults.title || "",
    },
  ];
}

function allModifyOptions(formSections) {
  return [...ISSUE_MODIFY_OPTIONS, ...modifyOptionsFromSections(formSections)];
}

function buildIncidentQuestions(prefill = {}) {
  return [
    {
      key: "description",
      prompt: "What is the issue?",
      hint: "Describe what happened.",
      type: "textarea",
      default: prefill.description || "",
    },
    {
      key: "title",
      prompt: "What is the title of this issue?",
      hint: "A short summary of the issue.",
      type: "text",
      default: "",
    },
    { key: "env", prompt: "Environment URL where you saw this? (type 'skip' if not relevant)", type: "text", optional: true },
    { key: "aura", prompt: "Which AURA version were you on?", type: "text", optional: true },
    { key: "swym", prompt: "Which SWYM version?", type: "text", optional: true },
    { key: "swymUi", prompt: "Which SWYM UI version?", type: "text", optional: true },
    { key: "steps", prompt: "What are the steps to reproduce?", type: "textarea", optional: true },
    { key: "expected", prompt: "What did you expect to happen?", type: "textarea", optional: true },
    {
      key: "actual",
      prompt: "What actually happened?",
      type: "textarea",
      optional: incidentDetailCovered("actual", prefill),
    },
    { key: "attachment", prompt: "Attach a screenshot or recording (optional).", type: "file", optional: true },
    { key: "severity", prompt: "How severe is this issue?", type: "quickreply", options: SEVERITIES },
    { key: "detected_environment", prompt: "Which platform/environment did this occur in?", type: "quickreply", options: ENVIRONMENTS },
  ];
}

function questionsForModifiedFields(selectedIds, savedFields, formSections, { useDetectionPicker = false } = {}) {
  const allQuestions = [
    ...buildIssueEditQuestions(savedFields),
    ...flattenFormSections(formSections, savedFields),
  ];
  const keys = new Set();
  allModifyOptions(formSections).forEach((opt) => {
    if (selectedIds.includes(opt.id)) opt.keys.forEach((k) => keys.add(k));
  });

  if (selectedIds.includes("detection")) {
    keys.delete("detection_level_title");
    if (useDetectionPicker) {
      keys.delete("detection_level_name");
      keys.add("_detection_program_pick");
    }
  }

  return allQuestions
    .filter((q) => keys.has(q.key))
    .concat(
      useDetectionPicker && selectedIds.includes("detection")
        ? [{
            key: "_detection_program_pick",
            type: "program_picker",
            sectionId: "detection",
            sectionLabel: "Detection program / version",
            sectionIntro: "Pick the program where you detected this issue.",
            prompt: "Which detection program did you find this in?",
            isFirstInSection: true,
          }]
        : [],
    );
}

export default function IRChatbot() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem("ir_session_id") || "");
  const [username, setUsername] = useState(() => localStorage.getItem("ir_username") || "");
  const [loginForm, setLoginForm] = useState({ username: "", password: "" });
  const [loginError, setLoginError] = useState("");
  const [authBootstrapping, setAuthBootstrapping] = useState(true);
  const [envLoginAvailable, setEnvLoginAvailable] = useState(false);
  const [serverHealth, setServerHealth] = useState(null);
  const [phase, setPhase] = useState("login");
  const [messages, setMessages] = useState([]);
  const [typing, setTyping] = useState(false);
  const [inputValue, setInputValue] = useState("");

  const [savedForms, setSavedForms] = useState([]);
  const [savedFormsHint, setSavedFormsHint] = useState("");
  const [savedFormsProbe, setSavedFormsProbe] = useState([]);
  const [formSections, setFormSections] = useState(FALLBACK_FORM_SECTIONS);
  const [myIncidents, setMyIncidents] = useState([]);
  const [selectedSavedForm, setSelectedSavedForm] = useState(null);
  const [savedFields, setSavedFields] = useState({});
  const [baselineFields, setBaselineFields] = useState({});
  const [userEditedSections, setUserEditedSections] = useState(() => new Set());

  const [questions, setQuestions] = useState([]);
  const [qIndex, setQIndex] = useState(-1);
  const [answers, setAnswers] = useState({});
  const [modifySelection, setModifySelection] = useState([]);
  const [flowMode, setFlowMode] = useState(null); // "form" | "incident"
  const [created, setCreated] = useState(null);
  const [duplicates, setDuplicates] = useState([]);

  const [serviceContext, setServiceContext] = useState(null);
  const [parentBrandName, setParentBrandName] = useState("");
  const [dsxBrands, setDsxBrands] = useState([]);
  const [dsxBrandsHint, setDsxBrandsHint] = useState("");
  const [dsxServices, setDsxServices] = useState([]);
  const [dsxServicesProbe, setDsxServicesProbe] = useState([]);
  const [dsxServicesHint, setDsxServicesHint] = useState("");
  const [dsxPrograms, setDsxPrograms] = useState([]);
  const [dsxProgramsProbe, setDsxProgramsProbe] = useState([]);
  const [dsxProgramsHint, setDsxProgramsHint] = useState("");
  const [programsLoading, setProgramsLoading] = useState(false);
  const [dsxReleases, setDsxReleases] = useState([]);
  const [detectionPrograms, setDetectionPrograms] = useState([]);
  const [detectionProgramsLoading, setDetectionProgramsLoading] = useState(false);
  const [pendingService, setPendingService] = useState(null);
  const [pendingProgram, setPendingProgram] = useState(null);
  const [currentBrand, setCurrentBrand] = useState(null);
  const [detectionResolving, setDetectionResolving] = useState(false);
  const [detectionResolveError, setDetectionResolveError] = useState("");
  const [releaseNameInput, setReleaseNameInput] = useState("");
  const [releaseNameResolving, setReleaseNameResolving] = useState(false);

  const scrollRef = useRef(null);
  const inputRef = useRef(null);
  const currentQuestion = qIndex >= 0 ? questions[qIndex] : null;
  const fieldLabels = buildLabelsFromSections(formSections);
  const hasReleaseContext = Boolean(serviceContext?.releaseFields?.rel_eno_id);
  const visibleFormSections = formSectionsWithoutRelease(formSections, hasReleaseContext);
  const modifyFieldOptions = modifyOptionsForContext(formSections, hasReleaseContext);

  function mergeReleaseContext(fields = {}, { force = false } = {}) {
    let merged = { ...fields };
    const detectionEdited = userEditedSections.has("detection")
      || detectionChangedFromBaseline(fields, baselineFields);

    if (serviceContext?.releaseFields) {
      merged = { ...merged, ...serviceContext.releaseFields };
    }
    if (serviceContext?.detectionFields && (force || !detectionEdited)) {
      merged = { ...merged, ...serviceContext.detectionFields };
    }
    return merged;
  }

  function hasFilingReleaseContext() {
    return Boolean(serviceContext?.releaseFields?.rel_eno_id);
  }

  function navigatorUrlForPhysicalId(physicalId) {
    return buildDsxNavigatorUrl(serverHealth, physicalId);
  }

  async function ensureReleaseContextBeforeFiling() {
    if (hasFilingReleaseContext()) return true;
    await addBot(
      "Set your **target release** first (main menu → **Set target release**). "
      + "That resolves **rel_eno_id** and version fields used when the IR is filed.",
      200,
    );
    setPhase("dashboard");
    return false;
  }

  function returnToMainMenu() {
    setPhase("dashboard");
  }

  function menuChooseSavedForm() {
    addUser("Create IR using saved form");
    setPhase("saved_form_list");
  }

  async function menuChooseWithoutForm() {
    addUser("Create IR without saved form");
    await startWithoutForm({ fromMenu: true });
  }

  async function menuChooseTargetRelease() {
    addUser("Set target release");
    setReleaseNameInput("");
    setTyping(true);
    await addBot(
      "Enter the **release name or code** (fix version), for example `1.9x`, `REL002009`, "
      + "or a composite like `MyStream-1.9x`. I'll resolve **rel_eno_id** for IR filing — "
      + "no brand or program picker needed.",
      200,
    );
    setTyping(false);
    setPhase("release_name_ask");
  }

  async function submitReleaseNameResolve() {
    const query = releaseNameInput.trim();
    if (!query || releaseNameResolving) return;
    addUser(query);
    setReleaseNameResolving(true);
    setTyping(true);
    try {
      const resolved = await resolveDsxRelease(sessionId, { query });
      const releaseFields = resolved.release_fields || {};
      const relLabel = resolved.release?.title
        || resolved.release?.name
        || releaseFields.rel_title
        || releaseFields.rel_name
        || query;
      const relId = releaseFields.rel_eno_id || "";
      const context = {
        releaseFields,
        releaseLabel: relLabel,
        releaseResolvedByName: true,
      };
      setServiceContext((prev) => ({ ...(prev || {}), ...context }));
      const contextFields = { ...releaseFields };
      setSavedFields((prev) => ({ ...prev, ...contextFields }));
      setBaselineFields((prev) => ({ ...prev, ...contextFields }));

      const navUrl = resolved.navigator_url || navigatorUrlForPhysicalId(relId);
      const navLine = navUrl
        ? `\n\n[Open in 3DEXPERIENCE Navigator](${navUrl})`
        : "";
      const successMessage = (
        `Target release **${relLabel}** is set.\n\n`
        + `**Release ID for IR filing:** \`${relId}\`${navLine}\n\n`
        + "You can now create an IR from the main menu (saved form or without saved form)."
      );
      setReleaseNameInput("");
      await loadDashboard(sessionId, username, {
        preserveMessages: true,
        appendMessage: successMessage,
      });
    } catch (err) {
      await addBot(`Could not resolve release: ${err.message}`);
      setPhase("release_name_ask");
    } finally {
      setReleaseNameResolving(false);
      setTyping(false);
    }
  }

  function markSectionEdited(sectionId) {
    if (!sectionId) return;
    setUserEditedSections((prev) => new Set([...prev, sectionId]));
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, typing]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrapAuth() {
      setAuthBootstrapping(true);
      setLoginError("");

      try {
        const health = await fetchHealth();
        if (cancelled) return;

        setServerHealth(health);
        setEnvLoginAvailable(Boolean(health.env_login_available));
        if (health.configured_username) {
          setLoginForm((prev) => (
            prev.username ? prev : { ...prev, username: health.configured_username }
          ));
        }

        const storedSessionId = localStorage.getItem("ir_session_id") || "";
        const storedUsername = localStorage.getItem("ir_username") || "";

        if (storedSessionId && storedUsername) {
          setSessionId(storedSessionId);
          setUsername(storedUsername);
          const ok = await loadDashboard(storedSessionId, storedUsername, { returnStatus: true });
          if (cancelled) return;
          if (ok) {
            setAuthBootstrapping(false);
            return;
          }
          localStorage.removeItem("ir_session_id");
          localStorage.removeItem("ir_username");
          setSessionId("");
          setUsername("");
        }

        if (health.env_login_available) {
          const ok = await completeEnvLogin();
          if (cancelled) return;
          if (ok) {
            setAuthBootstrapping(false);
            return;
          }
        }
      } catch (err) {
        if (!cancelled) {
          setLoginError(err.message || "Could not connect to the IR Assistant server.");
        }
      } finally {
        if (!cancelled) {
          setAuthBootstrapping(false);
        }
      }
    }

    bootstrapAuth();
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (currentQuestion?.type === "text" || currentQuestion?.type === "textarea") {
      setInputValue(currentQuestion.default || answers[currentQuestion.key] || "");
      inputRef.current?.focus();
    }
  }, [qIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  function addBot(text, delay = 450) {
    setTyping(true);
    return new Promise((resolve) => {
      setTimeout(() => {
        setMessages((m) => [...m, { from: "bot", text }]);
        setTyping(false);
        resolve();
      }, delay);
    });
  }

  function addUser(text) {
    setMessages((m) => [...m, { from: "user", text }]);
  }

  function applySession(result) {
    localStorage.setItem("ir_session_id", result.session_id);
    localStorage.setItem("ir_username", result.username);
    setSessionId(result.session_id);
    setUsername(result.username);
  }

  async function completeEnvLogin() {
    const result = await loginFromEnv();
    applySession(result);
    return loadDashboard(result.session_id, result.username, { returnStatus: true });
  }

  async function handleLogin(e) {
    e.preventDefault();
    setLoginError("");
    setTyping(true);
    try {
      const result = await login(loginForm.username, loginForm.password);
      applySession(result);
      setServerHealth(await fetchHealth());
      setTyping(false);
      await loadDashboard(result.session_id, result.username);
    } catch (err) {
      setTyping(false);
      setLoginError(err.message);
    }
  }

  async function handleEnvLogin() {
    setLoginError("");
    setTyping(true);
    try {
      const ok = await completeEnvLogin();
      setTyping(false);
      if (!ok) {
        setLoginError("Signed in, but could not load dashboard data from DSX.");
        setPhase("login");
      }
    } catch (err) {
      setTyping(false);
      setLoginError(err.message);
    }
  }

  async function handleLogout() {
    try {
      if (sessionId) await logout(sessionId);
    } catch {
      // ignore
    }
    localStorage.removeItem("ir_session_id");
    localStorage.removeItem("ir_username");
    setSessionId("");
    setUsername("");
    setPhase("login");
    setMessages([]);
    setSavedForms([]);
    setSavedFormsHint("");
    setSavedFormsProbe([]);
    setMyIncidents([]);
    setServiceContext(null);
    setParentBrandName("");
    setDsxBrands([]);
    setDsxBrandsHint("");
    setDsxServices([]);
    setDsxServicesProbe([]);
    setDsxServicesHint("");
    setDsxPrograms([]);
    setDsxProgramsProbe([]);
    setDsxProgramsHint("");
    setProgramsLoading(false);
    setDsxReleases([]);
    setPendingService(null);
    setPendingProgram(null);
    resetFlow();
  }

  function syntheticServiceFromBrand(brand) {
    const label = brand?.title || brand?.name || brand?.id || "brand";
    return {
      id: brand.id,
      title: `${label} (brand)`,
      name: brand.name || label,
      _synthetic: true,
    };
  }

  async function loadServicesForBrand(sid, brandId = "") {
    const body = await fetchDsxServices(sid, brandId);
    const parentLabel =
      body.parent_brand?.title || body.parent_brand?.name || parentBrandName || "3DEXPERIENCE Platform";
    setParentBrandName(parentLabel);
    if (body.parent_brand) {
      setCurrentBrand(body.parent_brand);
    }
    setDsxServices(body.items || []);
    setDsxServicesHint(body.hint || "");
    setDsxServicesProbe(body.probe_summary || body.probe || []);
    return body;
  }

  async function loadProgramsForBrand(brand, { announce = true } = {}) {
    if (!brand?.id) return { items: [] };
    const label = brand.title || brand.name || brand.id;
    setPendingService(syntheticServiceFromBrand(brand));
    setPendingProgram(null);
    setDsxPrograms([]);
    setDsxProgramsProbe([]);
    setDsxProgramsHint("");
    setProgramsLoading(true);
    setPhase("program_pick");
    setTyping(true);
    try {
      if (announce) {
        await addBot(
          `Product services aren't exposed on this DSX server — pick a program directly under **${label}**.`,
          200,
        );
      }
      const body = await fetchDsxPrograms(sessionId, { brandId: brand.id });
      setDsxPrograms(body.items || []);
      setDsxProgramsProbe(body.probe_summary || body.probe || []);
      setDsxProgramsHint(body.hint || "");
      if ((body.items || []).length === 0) {
        await addBot(
          (body.hint || "No programs found for this brand on DSX.") +
            " Set target release is not available on this server — use a **saved IR form** (e.g. Translate_IR); release and detection OIDs are pre-filled from the template.",
          200,
        );
        setPhase("program_pick");
      }
      return body;
    } catch (err) {
      await addBot(
        `Could not load programs: ${err.message}. Use a **saved IR form** on the dashboard instead.`,
      );
      setPhase("program_pick");
      return { items: [] };
    } finally {
      setProgramsLoading(false);
      setTyping(false);
    }
  }

  async function beginBrandPick(sid = sessionId) {
    setPhase("brand_pick");
    setDsxBrands([]);
    setDsxBrandsHint("");
    setDsxServices([]);
    setDsxServicesProbe([]);
    setDsxServicesHint("");
    setTyping(true);
    try {
      const body = await fetchDsxBrands(sid);
      setDsxBrands(body.items || []);
      setDsxBrandsHint(body.hint || "");
      if ((body.items || []).length === 0) {
        await addBot(body.hint || "No brands were returned from DSX GET /brands.", 200);
      } else {
        await addBot(
          "DSX could not auto-select a parent brand (check **DSX_PARENT_BRAND_NAME** in `.env`). "
          + "Pick a **brand** only to scope the list — your target release is still chosen as "
          + "**product service → program → release** (e.g. 3DEXPERIENCEAIAssistantInfra, then 1.9x).",
          200,
        );
        if (body.hint) {
          await addBot(body.hint, 150);
        }
      }
    } catch (err) {
      await addBot(`Could not load brands: ${err.message}`);
    }
    setTyping(false);
  }

  async function selectBrand(brand) {
    addUser(brand.title || brand.name || brand.id);
    setCurrentBrand(brand);
    setTyping(true);
    try {
      const body = await loadServicesForBrand(sessionId, brand.id);
      const parentLabel =
        body.parent_brand?.title || body.parent_brand?.name || brand.title || brand.name;
      setParentBrandName(parentLabel);
      if ((body.items || []).length === 0 && body.parent_brand && body.services_unavailable) {
        setTyping(false);
        await loadProgramsForBrand(body.parent_brand, { announce: true });
        return;
      }
      setPhase("service_pick");
      if ((body.items || []).length === 0) {
        await addBot(
          (body.hint || "No product services were returned for this brand.") +
            " Try **Pick program directly** or pick another brand.",
          200,
        );
      } else {
        await addBot(
          `Which **product service** is this IR for? (under **${parentLabel}**) `
          + "Pick the line that matches your product (e.g. **3DEXPERIENCEAIAssistantInfra**), then program and release (e.g. **1.9x**).",
          200,
        );
      }
    } catch (err) {
      await addBot(`Could not load services: ${err.message}`);
      setPhase("brand_pick");
    }
    setTyping(false);
  }

  async function beginServiceContextFlow(sid = sessionId, user = username, optional = true) {
    setPendingService(null);
    setPendingProgram(null);
    setDsxPrograms([]);
    setDsxProgramsProbe([]);
    setDsxProgramsHint("");
    setDsxReleases([]);
    setDsxServices([]);
    setDsxServicesProbe([]);
    setDsxServicesHint("");
    setTyping(true);
    try {
      const body = await loadServicesForBrand(sid);

      if ((body.items || []).length === 0 && !body.parent_brand) {
        setTyping(false);
        setMessages([]);
        await beginBrandPick(sid);
        return;
      }

      if ((body.items || []).length === 0 && body.parent_brand && body.services_unavailable) {
        setTyping(false);
        const parentLabel =
          body.parent_brand?.title || body.parent_brand?.name || "3DEXPERIENCE Platform";
        setMessages([
          {
            from: "bot",
            text: optional
              ? `Setting target release under **${parentLabel}**…`
              : `Welcome back, ${user}! Setting target release under **${parentLabel}**…`,
          },
        ]);
        await loadProgramsForBrand(body.parent_brand, { announce: true });
        return;
      }

      const parentLabel =
        body.parent_brand?.title || body.parent_brand?.name || "3DEXPERIENCE Platform";
      const intro = optional
        ? `Set target release: pick **product service** → **program** → **release** (under **${parentLabel}**). `
          + "Example: service **3DEXPERIENCEAIAssistantInfra**, then release level **1.9x**."
        : `Welcome back, ${user}! Set target release: **product service** → **program** → **release** (under **${parentLabel}**).`;
      setMessages([{ from: "bot", text: intro }]);
      setPhase("service_pick");
      if (body.hint) {
        setDsxServicesHint(body.hint);
      }

      if ((body.items || []).length === 0) {
        await addBot(
          (body.hint || "No product services were returned from DSX.") +
            " Use **Pick program directly** or **Pick a different brand**.",
          200,
        );
      }
    } catch (err) {
      setMessages((m) => [...m, { from: "bot", text: `Could not load services: ${err.message}` }]);
      setPhase("service_pick");
    }
    setTyping(false);
  }

  function skipToDashboard() {
    setPendingService(null);
    setPendingProgram(null);
    setDsxPrograms([]);
    setDsxProgramsProbe([]);
    setDsxProgramsHint("");
    setDsxReleases([]);
    setDsxServicesProbe([]);
    setDsxServicesHint("");
    loadDashboard();
  }

  function startReleaseContextFlow() {
    setServiceContext(null);
    setPendingService(null);
    setPendingProgram(null);
    beginServiceContextFlow(sessionId, username, true);
  }

  async function selectService(service) {
    setPendingService(service);
    addUser(service.title || service.name || service.id);
    setPhase("program_pick");
    setDsxPrograms([]);
    setDsxProgramsProbe([]);
    setDsxProgramsHint("");
    setProgramsLoading(true);
    setTyping(true);
    await addBot(`Loading programs for **${service.title || service.name}**…`, 250);
    setTyping(true);
    try {
      const body = await fetchDsxPrograms(sessionId, { serviceId: service.id });
      setDsxPrograms(body.items || []);
      setDsxProgramsProbe(body.probe_summary || body.probe || []);
      setDsxProgramsHint(body.hint || "");
      if ((body.items || []).length === 0) {
        const probeNote = body.probe?.length
          ? ` Probe: ${body.probe.map(formatDsxProbeLine).join(", ")}.`
          : "";
        await addBot((body.hint || "No programs found for this service.") + probeNote, 200);
        setPhase("service_pick");
        setPendingService(null);
        setDsxProgramsProbe([]);
        setDsxProgramsHint("");
      }
    } catch (err) {
      await addBot(`Could not load programs: ${err.message}`);
      setPhase("service_pick");
      setPendingService(null);
      setDsxProgramsProbe([]);
      setDsxProgramsHint("");
    }
    setProgramsLoading(false);
    setTyping(false);
  }

  async function selectProgram(program) {
    setPendingProgram(program);
    addUser(program.title || program.name || program.id);
    setTyping(true);
    await addBot("Resolving the target release for this program…", 200);
    try {
      const releasesBody = await fetchDsxReleases(sessionId, program.id);
      const releases = releasesBody.items || [];
      setDsxReleases(releases);

      if (releases.length === 0) {
        await addBot("No release found for this program. Check DSX_RELEASES_PATH or pick another program.");
        setPhase("program_pick");
        setPendingProgram(null);
        setTyping(false);
        return;
      }

      if (releases.length === 1) {
        await completeReleaseContext(releases[0], {
          service: pendingService,
          program,
        });
        setTyping(false);
        return;
      }

      setPhase("release_pick");
      await addBot(`Found ${releases.length} releases. Which one is the target fix version?`, 200);
    } catch (err) {
      await addBot(`Could not resolve release: ${err.message}`);
      setPhase("program_pick");
      setPendingProgram(null);
    }
    setTyping(false);
  }

  async function selectRelease(releaseItem) {
    addUser(releaseItem.title || releaseItem.name || releaseItem.id);
    setTyping(true);
    await completeReleaseContext(releaseItem, {
      service: pendingService,
      program: pendingProgram,
    });
    setTyping(false);
  }

  async function completeReleaseContext(releaseItem, { service = null, program = null } = {}) {
    const activeService = service || pendingService;
    const activeProgram = program || pendingProgram;
    if (!activeService || !activeProgram) {
      await addBot("Service/program context was lost. Returning to the dashboard.");
      skipToDashboard();
      return;
    }

    try {
      const resolved = await resolveReleaseContext(sessionId, {
        service_id: activeService.id,
        service_name: activeService.title || activeService.name,
        program_id: activeProgram.id,
        program_name: activeProgram.title || activeProgram.name,
        release_id: releaseItem.id,
      });

      const context = {
        parentBrandName: parentBrandName || undefined,
        serviceId: resolved.service_id,
        serviceName: resolved.service_name || activeService.title || activeService.name,
        programId: resolved.program_id,
        programName: resolved.program_name || activeProgram.title || activeProgram.name,
        brandId: currentBrand?.id,
        syntheticService: Boolean(activeService._synthetic),
        releaseFields: resolved.release_fields,
        detectionFields: resolved.detection_fields || detectionFieldsFromProgram(activeProgram),
        releaseLabel: resolved.release?.title || resolved.release?.name || resolved.release_fields?.rel_title,
      };
      setServiceContext(context);
      const contextFields = {
        ...(context.releaseFields || {}),
        ...(context.detectionFields || {}),
      };
      setSavedFields((prev) => ({ ...prev, ...contextFields }));
      setBaselineFields((prev) => ({ ...prev, ...contextFields }));

      const relLabel = context.releaseLabel || context.releaseFields.rel_title || context.releaseFields.rel_name;
      const relId = context.releaseFields?.rel_eno_id || "";
      const successMessage = (
        `Target release **${relLabel}** is set for program **${context.programName}** (service **${context.serviceName}**).\n\n`
        + `**Release ID for IR filing:** \`${relId}\`\n\n`
        + "You can now create an IR from the main menu (saved form or without saved form)."
      );
      await loadDashboard(sessionId, username, {
        preserveMessages: true,
        appendMessage: successMessage,
      });
    } catch (err) {
      await addBot(`Could not finalize release context: ${err.message}`);
      if (dsxReleases.length > 1) {
        setPhase("release_pick");
      } else {
        setPhase("program_pick");
        setPendingProgram(null);
      }
    }
  }

  async function loadDashboard(
    sid = sessionId,
    user = username,
    { returnStatus = false, preserveMessages = false, appendMessage = "" } = {},
  ) {
    if (!returnStatus) {
      setPhase("dashboard");
      setSavedFields((prev) => mergeReleaseContext(prev, { force: true }));
      if (!preserveMessages) {
        setMessages([{ from: "bot", text: mainMenuGreeting(user) }]);
      } else if (appendMessage) {
        setMessages((prev) => [...prev, { from: "bot", text: appendMessage }]);
      }
    }
    setTyping(true);
    try {
      const [formsBody, incidentsBody, schemaBody] = await Promise.all([
        fetchSavedIrForms(sid),
        fetchMyIncidents(sid),
        fetchIrFieldSchema().catch(() => ({ sections: FALLBACK_FORM_SECTIONS })),
      ]);
      if (schemaBody.sections?.length) {
        setFormSections(normalizeDetectionSection(schemaBody.sections));
      }
      setSavedForms(formsBody.items || []);
      setSavedFormsHint(formsBody.hint || "");
      setSavedFormsProbe(formsBody.probe || []);
      setMyIncidents(incidentsBody.items || []);
      if (returnStatus) {
        setPhase("dashboard");
        setSavedFields((prev) => mergeReleaseContext(prev, { force: true }));
        setMessages([{ from: "bot", text: mainMenuGreeting(user) }]);
      }
      return true;
    } catch (err) {
      if (!returnStatus) {
        setMessages((m) => [...m, { from: "bot", text: `Could not load DSX data: ${err.message}` }]);
      }
      return false;
    } finally {
      setTyping(false);
    }
  }

  function resetFlow() {
    setQuestions([]);
    setQIndex(-1);
    setAnswers({});
    setSelectedSavedForm(null);
    setSavedFields({});
    setBaselineFields({});
    setUserEditedSections(new Set());
    setModifySelection([]);
    setDuplicates([]);
    setFlowMode(null);
    setCreated(null);
    setInputValue("");
    setDetectionResolving(false);
    setDetectionResolveError("");
  }

  function detectionBrandId() {
    return serviceContext?.brandId || currentBrand?.id || "";
  }

  function detectionResolveFailureMessage() {
    const parts = [
      "Could not find a DSX program for this version.",
      "Try a program code (e.g. PRG044546), pick from the program list when available, or use **Set target release** on the dashboard.",
    ];
    if (!serviceContext?.serviceId) {
      parts.push(
        "The hidden detection program ID was **not** updated — filing may still use the saved template value unless you fix this.",
      );
    }
    return parts.join(" ");
  }

  async function ensureParentBrandForDetection() {
    if (detectionBrandId()) {
      return detectionBrandId();
    }
    try {
      const body = await loadServicesForBrand(sessionId);
      return body.parent_brand?.id || currentBrand?.id || "";
    } catch {
      return "";
    }
  }

  async function ensureDetectionLinked(merged, { announce = false } = {}) {
    const query = detectionQueryFromMerged(merged);
    if (!query.name && !query.title) {
      return { ok: true, merged };
    }

    const brandId = detectionBrandId() || (await ensureParentBrandForDetection());
    const useSyntheticService = Boolean(serviceContext?.syntheticService);
    const serviceId = useSyntheticService ? "" : (serviceContext?.serviceId || "");

    setDetectionResolving(true);
    setDetectionResolveError("");
    try {
      const body = await resolveDetectionLevel(sessionId, {
        detection_level_name: query.name,
        detection_level_title: query.title,
        service_id: serviceId,
        brand_id: serviceId ? "" : brandId,
      });
      const fields = body.fields || {};
      const nextMerged = { ...merged, ...fields };
      setSavedFields((prev) => ({ ...prev, ...fields }));
      setAnswers((prev) => ({ ...prev, ...fields }));

      if (!body.resolved) {
        const message = detectionResolveFailureMessage();
        setDetectionResolveError(message);
        if (announce) {
          await addBot(message);
        }
        return { ok: false, merged: nextMerged };
      }

      setDetectionResolveError("");
      if (announce) {
        const label = fields.detection_level_name || fields.detection_level_title || query.name || "program";
        await addBot(`Issue detected version linked: ${label}`);
      }
      return { ok: true, merged: nextMerged };
    } catch (err) {
      setDetectionResolveError(err.message);
      if (announce) {
        await addBot(`Could not link issue detected version — ${err.message}`);
      }
      return { ok: false, merged };
    } finally {
      setDetectionResolving(false);
    }
  }

  async function startWithForm(form) {
    if (!(await ensureReleaseContextBeforeFiling())) return;
    resetFlow();
    setSelectedSavedForm(form);
    setBaselineFields({ ...form.fields });
    setUserEditedSections(new Set());
    setSavedFields(mergeReleaseContext({ ...form.fields }, { force: true }));
    addUser(`Saved form: ${form.title}`);
    const summary = buildSavedFormSummary(form, fieldLabels);
    await addBot(
      `Loaded saved form **"${form.title}"**. Template values are merged with your target release.\n\n${summary}\n\nDo you want to **change** anything (title, description, detection version, feature, people, ...) or keep these details? I'll still ask for severity and other issue details afterwards.`,
      300,
    );
    setPhase("saved_review");
  }

  async function startWithoutForm({ fromMenu = false } = {}) {
    if (!(await ensureReleaseContextBeforeFiling())) return;
    resetFlow();
    setSelectedSavedForm(null);
    setBaselineFields({});
    setSavedFields(mergeReleaseContext({}));
    if (!fromMenu) {
      addUser("Start without saved form");
    }
    await addBot(
      "Let's fill in the IR details step by step. Target release is already set from your selection — I'll ask for feature, people, detection version, and the issue description.",
      200,
    );

    let flow = flattenFormSections(visibleFormSections);
    const programs = await loadDetectionProgramsForPicker();
    if (programs.length > 0) {
      flow = flow.filter(
        (q) => q.sectionId !== "detection" || !["detection_level_name", "detection_level_title"].includes(q.key),
      );
      const detectionIdx = flow.findIndex((q) => q.sectionId === "detection");
      const pickerQ = {
        key: "_detection_program_pick",
        type: "program_picker",
        sectionId: "detection",
        sectionLabel: "Detection program / version",
        sectionIntro: "Pick the program where you detected this issue.",
        prompt: "Which detection program did you find this in?",
        isFirstInSection: true,
      };
      if (detectionIdx >= 0) {
        flow = [...flow.slice(0, detectionIdx), pickerQ, ...flow.slice(detectionIdx)];
      } else {
        flow = [...flow, pickerQ];
      }
    }

    setQuestions(flow);
    setAnswers({});
    setFlowMode("form");
    setPhase("asking");
    setQIndex(0);
    await addBot(formatQuestionMessage(flow[0]));
  }

  async function resolveSavedFormChoice(keepAll) {
    if (keepAll) {
      addUser("Keep saved details");
      await beginIncidentQuestions(savedFields);
      return;
    }
    addUser("Change something");
    setPhase("modify_pick");
    await addBot("What do you want to change? Select one or more (title, description, detection version, feature, people, ...).");
  }

  async function loadDetectionProgramsForPicker() {
    let brandId = detectionBrandId();
    const serviceId = serviceContext?.syntheticService ? "" : (serviceContext?.serviceId || "");
    if (!brandId && !serviceId) {
      brandId = await ensureParentBrandForDetection();
    }
    if (!brandId && !serviceId) return [];
    setDetectionProgramsLoading(true);
    try {
      const body = await fetchDsxPrograms(sessionId, {
        brandId: serviceId ? "" : brandId,
        serviceId: serviceId || "",
      });
      const items = body.items || [];
      setDetectionPrograms(items);
      return items;
    } catch {
      setDetectionPrograms([]);
      return [];
    } finally {
      setDetectionProgramsLoading(false);
    }
  }

  async function confirmModifySelection() {
    if (modifySelection.length === 0) {
      addUser("Nothing to change");
      await beginIncidentQuestions(savedFields);
      return;
    }
    addUser(`Update: ${modifySelection.join(", ")}`);

    let useDetectionPicker = false;
    if (modifySelection.includes("detection")) {
      const programs = detectionPrograms.length > 0
        ? detectionPrograms
        : await loadDetectionProgramsForPicker();
      useDetectionPicker = programs.length > 0;
      if (useDetectionPicker) {
        await addBot(
          serviceContext?.serviceId
            ? "Pick a detection program from the list below, or type a program code in the next step if you prefer."
            : "Pick a detection program from the list below (loaded from your platform brand). You can also type a program code like PRG043173.",
          200,
        );
      } else if (!serviceContext?.serviceId) {
        await addBot(
          "Could not load a program list from DSX. I'll still try to link program codes like PRG043173 automatically. For full version titles, use **Set target release** on the dashboard first or keep the saved template detection value.",
          200,
        );
      }
    }

    const flow = questionsForModifiedFields(modifySelection, savedFields, visibleFormSections, { useDetectionPicker });
    if (flow.length === 0) {
      await beginIncidentQuestions(savedFields);
      return;
    }
    setQuestions(flow);
    setAnswers({ ...savedFields });
    setFlowMode("form");
    setPhase("asking");
    setQIndex(0);
    await addBot(`Let's update those fields.\n\n${formatQuestionMessage(flow[0])}`);
  }

  async function tryResolveDraftReferences(merged) {
    const draftPayload = {
      title: merged.title,
      description: buildDescription(merged),
      severity: merged.severity,
      detected_environment: merged.detected_environment,
    };
    DETAIL_KEYS.forEach((k) => {
      if (merged[k]) draftPayload[k] = merged[k];
    });
    const baselineName = String(baselineFields.detection_level_name ?? "").trim();
    const currentName = String(merged.detection_level_name ?? "").trim();
    const baselineTitle = String(baselineFields.detection_level_title ?? "").trim();
    const currentTitle = String(merged.detection_level_title ?? "").trim();
    if (
      (currentName && baselineName && currentName !== baselineName)
      || (currentTitle && baselineTitle && currentTitle !== baselineTitle)
    ) {
      delete draftPayload.detection_level_eno_id;
    }
    applyBaselineOidsForUneditedSections(draftPayload, baselineFields, userEditedSections);
    if (!draftPayload.owner) draftPayload.owner = username;

    try {
      const brandId = detectionBrandId() || (await ensureParentBrandForDetection());
      const useSyntheticService = Boolean(serviceContext?.syntheticService);
      const serviceId = useSyntheticService ? "" : (serviceContext?.serviceId || "");
      const body = await resolveIrReferences(sessionId, {
        ...draftPayload,
        service_id: serviceId,
        brand_id: serviceId ? "" : brandId,
      });
      const fields = body.fields || {};
      const nextMerged = { ...merged, ...fields };
      setSavedFields((prev) => ({ ...prev, ...fields }));
      setAnswers((prev) => ({ ...prev, ...fields }));

      const missingOid = getMissingOidIssues(nextMerged);
      const staleOid = getStaleOidIssues(nextMerged, baselineFields, userEditedSections);
      const oidMismatch = getOidMismatchIssues(nextMerged, baselineFields, userEditedSections);
      if (missingOid.length > 0 || staleOid.length > 0 || oidMismatch.length > 0) {
        const parts = [...new Set([...missingOid, ...staleOid, ...oidMismatch])];
        return {
          ok: false,
          merged: nextMerged,
          message: buildLinkErrorMessage(parts),
        };
      }
      return { ok: true, merged: nextMerged };
    } catch (err) {
      return { ok: false, merged, message: err.message };
    }
  }

  function needsDetectionConfirm() {
    return Boolean(selectedSavedForm) && !userEditedSections.has("detection");
  }

  async function beginDetectionConfirmQuestion(baseFields) {
    const defaultName = baseFields.detection_level_name || baseFields.detection_level_title || "";
    const detectionQ = {
      key: "detection_level_name",
      type: "text",
      sectionId: "detection",
      sectionLabel: "Issue detected version",
      sectionIntro: "Confirm or update the version where you actually found this issue.",
      prompt: "Which version did you actually detect this issue in?",
      hint: "Program code or version label (e.g. PRG044546). The ID is looked up automatically.",
      placeholder: "e.g. PRG044546",
      default: defaultName,
      isFirstInSection: true,
    };
    setQuestions([detectionQ]);
    setAnswers({ ...baseFields });
    setSavedFields((prev) => ({ ...prev, ...baseFields }));
    setFlowMode("detection_confirm");
    setPhase("asking");
    setQIndex(0);
    await addBot(formatQuestionMessage(detectionQ));
  }

  async function finishIncidentFlow(nextAnswers) {
    const merged = mergeReleaseContext({ ...savedFields, ...nextAnswers });
    setSavedFields(merged);
    if (needsDetectionConfirm()) {
      await beginDetectionConfirmQuestion(merged);
      return;
    }
    await runDuplicateCheck(merged);
  }

  async function beginIncidentQuestions(baseFields, { skipIssuePrompts = false } = {}) {
    const mergedBase = mergeReleaseContext(baseFields);
    setSavedFields((prev) => ({ ...prev, ...mergedBase }));
    const incidentFlow = buildIncidentQuestions(mergedBase).filter((q) => {
      if (!skipIssuePrompts && ALWAYS_ASK_INCIDENT_KEYS.has(q.key)) return true;
      if (INCIDENT_DESCRIPTION_MARKERS[q.key] && incidentDetailCovered(q.key, mergedBase)) return false;
      const existing = mergedBase[q.key];
      return !(existing && String(existing).trim());
    });
    setQuestions(incidentFlow);
    setAnswers((prev) => ({ ...prev, ...mergedBase }));
    setFlowMode("incident");
    setPhase("asking");
    if (incidentFlow.length === 0) {
      setQIndex(-1);
      await finishIncidentFlow(mergedBase);
      return;
    }
    setQIndex(0);
    await addBot(`Let's capture this issue.\n\n${formatQuestionMessage(incidentFlow[0])}`);
  }

  async function updateDraftField(key, value) {
    const apply = (prev) => applyLinkedFieldUpdate(prev, key, value);
    const merged = apply({ ...savedFields, ...answers });
    setSavedFields(apply);
    setAnswers(apply);
    if (isDetectionFieldKey(key)) {
      markSectionEdited("detection");
      setDetectionResolveError("");
      await ensureDetectionLinked(merged, { announce: false });
    }
  }

  async function answerDetectionProgram(program) {
    addUser(program.title || program.name || program.id);
    markSectionEdited("detection");
    const detectionUpdate = detectionFieldsFromProgram(program);
    const nextAnswers = mergeFieldUpdates(answers, detectionUpdate);
    setAnswers(nextAnswers);
    setSavedFields((prev) => mergeFieldUpdates(prev, detectionUpdate));
    const merged = mergeFieldUpdates({ ...savedFields, ...nextAnswers }, {});
    await ensureDetectionLinked(merged, { announce: true });
    await advanceQuestion(nextAnswers);
  }

  async function advanceQuestion(nextAnswers) {
    const nextIndex = qIndex + 1;

    // After manual/saved-form field questions, transition to incident questions.
    if (nextIndex >= questions.length && flowMode === "form") {
      let merged = mergeFieldUpdates({ ...savedFields, ...nextAnswers }, {});
      const editedSections = new Set(
        questions.map((q) => q.sectionId).filter(Boolean),
      );
      if (editedSections.size > 0) {
        setUserEditedSections((prev) => new Set([...prev, ...editedSections]));
      }
      setSavedFields(merged);
      const editedIssueInFlow = questions.some((q) => ALWAYS_ASK_INCIDENT_KEYS.has(q.key));
      await beginIncidentQuestions(merged, { skipIssuePrompts: editedIssueInFlow });
      return;
    }

    if (nextIndex < questions.length) {
      setQIndex(nextIndex);
      await addBot(formatQuestionMessage(questions[nextIndex]));
      return;
    }

    setQIndex(-1);
    await finishIncidentFlow(nextAnswers);
  }

  async function answerText() {
    const q = currentQuestion;
    const value = inputValue.trim();
    if (!value && !q.optional && !q.default) return;

    const finalValue = value || q.default || "";
    if (q.key === "description") {
      addUser(finalValue);
    } else {
      addUser(finalValue || "(skipped)");
    }

    const nextAnswers = mergeFieldUpdates(answers, { [q.key]: finalValue });
    setAnswers(nextAnswers);
    setSavedFields((prev) => mergeFieldUpdates(prev, { [q.key]: finalValue }));
    setInputValue("");

    if (isDetectionFieldKey(q.key)) {
      markSectionEdited("detection");
      const merged = mergeFieldUpdates({ ...savedFields, ...nextAnswers }, {});
      const remainingDetection = questions
        .slice(qIndex + 1)
        .filter((item) => item.sectionId === "detection" && isDetectionFieldKey(item.key));
      if (remainingDetection.length === 0) {
        await ensureDetectionLinked(merged, { announce: true });
      }
    }

    await advanceQuestion(nextAnswers);
  }

  async function answerQuickreply(q, optionValue, optionLabel) {
    addUser(optionLabel);
    const nextAnswers = { ...answers, [q.key]: optionValue };
    setAnswers(nextAnswers);
    await advanceQuestion(nextAnswers);
  }

  async function answerFile(file) {
    addUser(file ? file.name : "Skipped");
    const nextAnswers = { ...answers, attachment: file || null };
    setAnswers(nextAnswers);
    await advanceQuestion(nextAnswers);
  }

  async function runDuplicateCheck(finalAnswers) {
    setPhase("duplicates");
    await addBot("Checking for similar existing reports…", 300);

    let candidates = [];
    try {
      candidates = await searchIncidentReports(sessionId, finalAnswers.title, finalAnswers.feature_eno_id);
    } catch {
      candidates = [];
    }

    const scored = candidates
      .map((ir) => ({ ...ir, score: keywordOverlapScore(ir.title, finalAnswers.title) }))
      .filter((ir) => ir.score > 0.25)
      .sort((a, b) => b.score - a.score);

    if (scored.length > 0) {
      setDuplicates(scored);
      await addBot(
        `I found ${scored.length} similar report${scored.length > 1 ? "s" : ""}. Is your issue one of these, or something new?`,
        200,
      );
    } else {
      setDuplicates([]);
      setPhase("review");
      await addBot("No similar reports found. Review your draft on the right, then file when ready.", 200);
    }
  }

  async function resolveDuplicate(isDup, ir) {
    if (isDup) {
      addUser(`Same as ${ir.id}`);
      await addBot(`I won't create a duplicate. Track your issue on ${ir.id}.`);
      setPhase("done");
      setCreated({ existing: true, id: ir.id, title: ir.title });
    } else {
      addUser("It's a new issue");
      setDuplicates([]);
      setPhase("review");
      await addBot("Got it. Review the draft and confirm when ready.");
    }
  }

  function buildPayloadFromMerged(merged, baseline = baselineFields) {
    const payload = {
      title: merged.title,
      description: buildDescription(merged),
      severity: merged.severity,
      detected_environment: merged.detected_environment,
    };
    DETAIL_KEYS.forEach((k) => {
      if (merged[k]) payload[k] = merged[k];
    });
    if (payload.detection_level_name && !payload.detection_level_title) {
      payload.detection_level_title = payload.detection_level_name;
    }
    const baselineName = String(baseline.detection_level_name ?? "").trim();
    const currentName = String(merged.detection_level_name ?? "").trim();
    const baselineTitle = String(baseline.detection_level_title ?? "").trim();
    const currentTitle = String(merged.detection_level_title ?? "").trim();
    if (
      (currentName && baselineName && currentName !== baselineName)
      || (currentTitle && baselineTitle && currentTitle !== baselineTitle)
    ) {
      delete payload.detection_level_eno_id;
    }
    if (!payload.owner) payload.owner = username;
    return payload;
  }

  function buildPayloadFromMergedWithRecovery(merged, baseline = baselineFields) {
    const payload = buildPayloadFromMerged(merged, baseline);
    applyBaselineOidsForUneditedSections(payload, baseline, userEditedSections);
    return payload;
  }

  function buildPayload() {
    const merged = mergeReleaseContext({ ...savedFields, ...answers });
    return { payload: buildPayloadFromMergedWithRecovery(merged, baselineFields), merged };
  }

  async function confirmCreate() {
    if (!hasFilingReleaseContext()) {
      addUser("File it");
      await addBot(
        "Cannot file without a dashboard **target release**. Use **Set target release** on the dashboard, then start again.",
      );
      return;
    }

    const { merged } = buildPayload();
    const missing = getMissingPayloadFields(buildPayloadFromMergedWithRecovery(merged), fieldLabels);
    if (missing.length > 0) {
      addUser("File it");
      await addBot(
        `Cannot file yet:\n• ${missing.map((label) => `Missing: ${label}`).join("\n• ")}\n\nUse a saved form template or complete the remaining fields in the chat.`,
      );
      return;
    }

    setPhase("creating");
    addUser("File it");
    setTyping(true);

    const detectionResult = await ensureDetectionLinked(merged, { announce: false });
    const resolveResult = await tryResolveDraftReferences(detectionResult.merged);
    if (!resolveResult.ok) {
      setTyping(false);
      setPhase("review");
      await addBot(resolveResult.message);
      return;
    }

    const brandId = detectionBrandId() || (await ensureParentBrandForDetection());
    const useSyntheticService = Boolean(serviceContext?.syntheticService);
    const serviceId = useSyntheticService ? "" : (serviceContext?.serviceId || "");
    const resolvedPayload = {
      ...buildPayloadFromMergedWithRecovery(resolveResult.merged),
      service_id: serviceId,
      brand_id: serviceId ? "" : brandId,
    };
    const pendingAttachment = answers.attachment;
    try {
      const result = await createIncidentReport(sessionId, resolvedPayload);
      const irRef = result.id || result.name || result.object_id || result.physicalid;
      const uploadIrId = result.object_id || result.physicalid || result.id;
      let uploadNote = "";
      if (pendingAttachment && uploadIrId) {
        setPhase("uploading");
        try {
          await uploadIrMedia(sessionId, uploadIrId, pendingAttachment, result.document_id);
          uploadNote = ` Media "${pendingAttachment.name}" uploaded.`;
        } catch (uploadErr) {
          uploadNote = ` IR filed, but media upload failed — ${uploadErr.message}. You can attach it manually in 3DEXPERIENCE on ${irRef}.`;
        }
      }
      setTyping(false);
      setCreated({ ...result, id: irRef });
      setPhase("done");
      if (result.url) {
        setMessages((m) => [
          ...m,
          {
            from: "bot",
            textPrefix: "Filed as ",
            link: result.url,
            linkLabel: irRef,
            textSuffix: `.${uploadNote}`,
          },
        ]);
      } else {
        setMessages((m) => [...m, { from: "bot", text: `Filed as ${irRef}.${uploadNote}` }]);
      }
    } catch (err) {
      setTyping(false);
      setPhase("review");
      setMessages((m) => [...m, { from: "bot", text: `Couldn't file it — ${err.message}. Fix and try again?` }]);
    }
  }

  function backToDashboard() {
    resetFlow();
    setSavedFields((prev) => mergeReleaseContext(prev, { force: true }));
    setPhase("dashboard");
    setMessages([{ from: "bot", text: mainMenuGreeting(username) }]);
  }

  const { payload: draftPayload, merged: draftMerged } = buildPayload();
  const draftSeverity = SEVERITIES.find((s) => s.value === draftMerged.severity);
  const missingDraftFields = getMissingPayloadFields(draftPayload, fieldLabels);
  const staleOidIssues = getStaleOidIssues(draftMerged, baselineFields, userEditedSections);
  const missingOidIssues = getMissingOidIssues(draftMerged);
  const oidMismatchIssues = getOidMismatchIssues(draftMerged, baselineFields, userEditedSections);
  const draftProgress = computeDraftProgress(draftPayload);
  const stepIndex = activeStepIndex(phase);

  if (phase === "login" || !sessionId) {
    return (
      <div className="ir-app ir-grid-bg flex items-center justify-center p-6 min-h-screen">
        <div className="glass-panel w-full max-w-md rounded-3xl p-8 relative overflow-hidden">
          <div className="absolute -top-24 -right-24 w-48 h-48 rounded-full opacity-50" style={{ background: "radial-gradient(circle, rgba(6,182,212,0.5), transparent 70%)" }} />
          <div className="relative">
            <div className="flex flex-col items-center text-center mb-8">
              <div className="login-orb avatar-glow w-16 h-16 rounded-2xl flex items-center justify-center mb-4">
                <Bot size={32} color="#0891B2" />
              </div>
              <h1 className="text-2xl font-bold tracking-tight shimmer-text">IR Assistant</h1>
              <p className="text-sm mt-2 font-medium" style={{ color: "var(--text-muted)" }}>
                File incident reports through a guided conversation
              </p>
            </div>
            <form onSubmit={handleLogin} className="space-y-4">
              {authBootstrapping ? (
                <div className="flex flex-col items-center justify-center py-8 gap-3">
                  <Loader2 size={28} className="animate-spin" style={{ color: "var(--accent)" }} />
                  <p className="text-sm font-medium" style={{ color: "var(--text-muted)" }}>Connecting to DSX…</p>
                </div>
              ) : (
                <>
              <div>
                <label className="text-[10px] uppercase tracking-widest mb-1.5 block font-bold section-title">DS login</label>
                <input
                  value={loginForm.username}
                  onChange={(e) => setLoginForm((f) => ({ ...f, username: e.target.value }))}
                  placeholder="e.g. nmn38"
                  required
                  className="ir-input w-full rounded-xl px-4 py-3 text-sm"
                />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-widest mb-1.5 block font-bold section-title">API key</label>
                <input
                  type="password"
                  value={loginForm.password}
                  onChange={(e) => setLoginForm((f) => ({ ...f, password: e.target.value }))}
                  placeholder="Your DSX service key"
                  required
                  className="ir-input w-full rounded-xl px-4 py-3 text-sm"
                />
              </div>
              {loginError && (
                <div className="alert-error flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium">
                  <AlertTriangle size={14} /> {loginError}
                </div>
              )}
              <button type="submit" disabled={typing} className="btn-primary w-full text-sm py-3 rounded-xl disabled:opacity-50 flex items-center justify-center gap-2">
                {typing ? <><Loader2 size={16} className="animate-spin" /> Signing in…</> : <>Sign in <ChevronRight size={16} /></>}
              </button>
              {envLoginAvailable && (
                <>
                  <div className="flex items-center gap-3 py-1">
                    <div className="h-px flex-1" style={{ background: "var(--border)" }} />
                    <span className="text-[10px] uppercase tracking-widest font-bold" style={{ color: "var(--text-dim)" }}>or</span>
                    <div className="h-px flex-1" style={{ background: "var(--border)" }} />
                  </div>
                  <button
                    type="button"
                    onClick={handleEnvLogin}
                    disabled={typing}
                    className="w-full text-sm py-3 rounded-xl disabled:opacity-50 flex items-center justify-center gap-2 border"
                    style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
                  >
                    {typing ? <><Loader2 size={16} className="animate-spin" /> Signing in…</> : "Sign in with server credentials (.env)"}
                  </button>
                </>
              )}
                </>
              )}
            </form>
            <p className="text-[11px] text-center mt-6 font-medium" style={{ color: "var(--text-dim)" }}>
              Connects to 3DEXPERIENCE DSX REST APIs
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="ir-app ir-grid-bg min-h-screen p-4 md:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Top bar */}
        <div className="flex items-center justify-between mb-5 px-1">
          <div className="flex items-center gap-3">
            <div className="avatar-glow w-10 h-10 rounded-xl flex items-center justify-center">
              <Bot size={20} color="#0891B2" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight app-title">IR Assistant</h1>
              <p className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>Signed in as <span className="font-bold" style={{ color: "var(--accent-bright)" }}>{username}</span></p>
            </div>
          </div>
          <button onClick={handleLogout} className="btn-ghost flex items-center gap-1.5 text-xs px-3 py-2 rounded-xl transition">
            <LogOut size={14} /> Sign out
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr] gap-5">
          {/* Chat panel */}
          <div className="glass-panel rounded-3xl flex flex-col overflow-hidden h-[min(720px,calc(100vh-140px))]">
            {/* Stepper */}
            <div className="px-5 pt-4 pb-3" style={{ borderBottom: "2px solid rgba(6,182,212,0.15)", background: "linear-gradient(180deg, #f0f9ff, #ffffff)" }}>
              <FlowStepper steps={FLOW_STEPS} activeIndex={stepIndex} />
            </div>

            {/* Messages */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto ir-scroll px-5 py-4 space-y-3">
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={`message-enter flex ${m.from === "user" ? "justify-end" : "justify-start"}`}
                  style={{ animationDelay: `${Math.min(i * 0.04, 0.3)}s` }}
                >
                  <div className={`flex items-end gap-2.5 max-w-[88%] ${m.from === "user" ? "flex-row-reverse" : ""}`}>
                    <div
                      className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 ${m.from === "user" ? "avatar-user" : "avatar-glow"}`}
                    >
                      {m.from === "user" ? <User size={13} color="#ffffff" /> : <Bot size={13} color="#0891B2" />}
                    </div>
                    <div className={`rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap ${m.from === "user" ? "bubble-user" : "bubble-bot"}`}>
                      {m.link ? (
                        <span>
                          {m.textPrefix || ""}
                          <a href={m.link} target="_blank" rel="noreferrer" className="ir-link inline-flex items-center gap-1 font-bold underline decoration-2 underline-offset-2">
                            {m.linkLabel || m.link}
                            <ExternalLink size={12} />
                          </a>
                          {m.textSuffix || ""}
                        </span>
                      ) : (
                        m.text
                      )}
                    </div>
                  </div>
                </div>
              ))}

              {typing && <TypingIndicator phase={phase} />}

            {!typing && phase === "brand_pick" && (
              <div className="space-y-3">
                {dsxBrandsHint && (
                  <p className="pl-9 text-[11px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
                    {dsxBrandsHint}
                  </p>
                )}
                <DsxPickerList
                  title="Select brand (scopes product services only)"
                  items={dsxBrands}
                  emptyText="No brands available from DSX."
                  onSelect={selectBrand}
                  onBack={() => {
                    setPhase("dashboard");
                    setDsxBrands([]);
                    setDsxBrandsHint("");
                  }}
                />
              </div>
            )}

            {!typing && phase === "service_pick" && (
              <div className="space-y-3">
                <p className="pl-9 text-[11px]" style={{ color: "var(--text-muted)" }}>
                  Target release is not the brand — pick your product service, then program, then release level.
                </p>
                <DsxPickerList
                  title="Select a product service"
                  items={dsxServices}
                  emptyText={
                    parentBrandName
                      ? `No product services available under ${parentBrandName}. Try another brand or set DSX_PARENT_BRAND_NAME in .env.`
                      : "No product services available. Pick a brand or set DSX_PARENT_BRAND_NAME in .env."
                  }
                  hint={dsxServicesHint}
                  probe={dsxServicesProbe}
                  onSelect={selectService}
                  onBack={() => {
                    if (currentBrand?.id || parentBrandName) {
                      setPhase("dashboard");
                    } else {
                      beginBrandPick(sessionId);
                    }
                  }}
                />
                <div className="pl-9 flex flex-wrap gap-2">
                  {dsxServices.length === 0 && currentBrand && (
                    <button
                      type="button"
                      onClick={() => loadProgramsForBrand(currentBrand)}
                      className="text-xs font-bold px-4 py-2.5 rounded-xl transition"
                      style={{ background: "rgba(6,182,212,0.12)", color: "#0891B2" }}
                    >
                      Pick program directly
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => beginBrandPick(sessionId)}
                    className="text-xs font-bold px-3 py-2 rounded-lg btn-ghost"
                  >
                    Pick a different brand
                  </button>
                </div>
              </div>
            )}

            {!typing && phase === "program_pick" && programsLoading && (
              <div className="pl-9 pt-2 message-enter">
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                  Loading programs for this product service…
                </p>
              </div>
            )}

            {!typing && !programsLoading && phase === "program_pick" && (
              <DsxPickerList
                title="Select a program"
                items={dsxPrograms}
                emptyText={
                  dsxPrograms.length === 0
                    ? "No programs available on this DSX server. Check DSX_PROGRAMS_PATH or pick another brand/service."
                    : "No programs available for this service."
                }
                hint={dsxProgramsHint}
                probe={dsxProgramsProbe}
                onSelect={selectProgram}
                onBack={() => {
                  if (pendingService?._synthetic || currentBrand) {
                    beginBrandPick(sessionId);
                  } else {
                    setPhase("service_pick");
                  }
                  setPendingService(null);
                  setDsxPrograms([]);
                  setDsxProgramsProbe([]);
                  setDsxProgramsHint("");
                }}
              />
            )}

            {!typing && phase === "release_pick" && (
              <DsxPickerList
                title="Select target release (fix version)"
                items={dsxReleases}
                emptyText="No releases available for this program."
                onSelect={selectRelease}
                onBack={() => {
                  setPhase("program_pick");
                  setPendingProgram(null);
                  setDsxReleases([]);
                }}
              />
            )}

            {!typing && phase === "release_name_ask" && (
              <div className="pl-9 space-y-3 pt-2 message-enter max-w-md">
                <label className="block text-[11px] font-bold uppercase tracking-widest section-title">
                  Release name or code
                </label>
                <input
                  type="text"
                  value={releaseNameInput}
                  onChange={(e) => setReleaseNameInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitReleaseNameResolve();
                  }}
                  placeholder="e.g. 1.9x, REL002009, StreamName-1.9x"
                  className="w-full text-sm px-4 py-3 rounded-xl border"
                  style={{ borderColor: "rgba(6,182,212,0.35)", background: "#f8fafc" }}
                  disabled={releaseNameResolving}
                  autoFocus
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={submitReleaseNameResolve}
                    disabled={!releaseNameInput.trim() || releaseNameResolving}
                    className="btn-primary text-xs px-5 py-2.5 rounded-xl flex items-center gap-1.5"
                  >
                    {releaseNameResolving ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
                    Resolve release
                  </button>
                  <button
                    type="button"
                    onClick={returnToMainMenu}
                    disabled={releaseNameResolving}
                    className="btn-ghost text-xs px-4 py-2.5 rounded-xl"
                  >
                    Back to main menu
                  </button>
                </div>
              </div>
            )}

            {!typing && phase === "dashboard" && (
              <div className="pl-9 space-y-3 pt-2 message-enter">
                {hasFilingReleaseContext() && (
                  <div className="glass-card rounded-2xl p-3 space-y-2">
                    <div className="text-[10px] uppercase tracking-widest font-bold section-title">Current target release</div>
                    <div className="flex flex-wrap gap-2 items-center">
                      <span className="chip chip-accent">{serviceContext.releaseLabel || serviceContext.releaseFields?.rel_title}</span>
                      {(() => {
                        const relId = serviceContext.releaseFields?.rel_eno_id;
                        const navUrl = navigatorUrlForPhysicalId(relId);
                        return navUrl ? (
                          <a
                            href={navUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="chip font-mono text-[10px] inline-flex items-center gap-1 hover:underline"
                            title="Open release in 3DEXPERIENCE Navigator (rel_eno_id)"
                          >
                            {relId}
                            <ExternalLink size={10} />
                          </a>
                        ) : (
                          <span className="chip font-mono text-[10px]" title="rel_eno_id">{relId}</span>
                        );
                      })()}
                    </div>
                  </div>
                )}
                <div className="flex flex-col gap-2 max-w-md">
                  <button type="button" onClick={menuChooseSavedForm} className="btn-primary text-xs px-5 py-3 rounded-xl flex items-center gap-2 justify-center">
                    <FileText size={14} /> Create IR using saved form
                  </button>
                  <button type="button" onClick={menuChooseWithoutForm} className="btn-secondary text-xs px-5 py-3 rounded-xl flex items-center gap-2 justify-center">
                    <Plus size={14} /> Create IR without saved form
                  </button>
                  <button type="button" onClick={menuChooseTargetRelease} className="btn-secondary text-xs px-5 py-3 rounded-xl flex items-center gap-2 justify-center">
                    <Layers size={14} /> Set target release
                  </button>
                </div>
                {myIncidents.length > 0 && (
                  <details className="text-[11px] pt-2" style={{ color: "var(--text-dim)" }}>
                    <summary className="cursor-pointer font-bold" style={{ color: "#4f46e5" }}>Recent IRs ({myIncidents.length})</summary>
                    <div className="space-y-1.5 mt-2 max-h-32 overflow-y-auto ir-scroll">
                      {myIncidents.slice(0, 6).map((ir) => (
                        <div key={ir.id} className="glass-card rounded-xl px-3 py-2">
                          {ir.url ? (
                            <a href={ir.url} target="_blank" rel="noreferrer" className="ir-link text-xs font-mono font-bold">{ir.id}</a>
                          ) : (
                            <span className="text-xs font-mono font-bold" style={{ color: "#0891B2" }}>{ir.id}</span>
                          )}
                          <div className="text-[11px] truncate">{ir.title}</div>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}

            {!typing && phase === "saved_form_list" && (
              <div className="pl-9 space-y-3 pt-2 message-enter">
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                  Pick a saved IR form template from DSX. Set target release first if you have not already.
                </p>
                {savedForms.length === 0 ? (
                  <div className="glass-card rounded-2xl p-4 space-y-2 max-w-md">
                    <p className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
                      No saved forms were returned for your account.
                    </p>
                    {savedFormsHint && <p className="text-[11px]" style={{ color: "var(--text-dim)" }}>{savedFormsHint}</p>}
                  </div>
                ) : (
                  <div className="grid gap-2 max-w-md">
                    {savedForms.map((form) => (
                      <button
                        key={form.id}
                        type="button"
                        onClick={() => startWithForm(form)}
                        className="glass-card w-full text-left px-4 py-3.5 rounded-2xl flex items-center gap-3 group"
                      >
                        <div className="icon-box-cyan w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0">
                          <FileText size={16} color="#0891B2" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-bold group-hover:text-[#0891B2] transition-colors">{form.title}</div>
                          {form.description && <div className="text-[11px] truncate mt-0.5" style={{ color: "var(--text-muted)" }}>{form.description}</div>}
                        </div>
                        <ChevronRight size={16} color="#0891B2" className="opacity-40 group-hover:opacity-100" />
                      </button>
                    ))}
                  </div>
                )}
                <button type="button" onClick={returnToMainMenu} className="btn-ghost text-xs px-4 py-2.5 rounded-xl">
                  Back to main menu
                </button>
              </div>
            )}

            {!typing && phase === "saved_review" && selectedSavedForm && (
              <div className="pl-9 flex flex-wrap gap-2 pt-2 message-enter">
                <button onClick={() => resolveSavedFormChoice(true)} className="btn-primary text-xs px-5 py-2.5 rounded-xl flex items-center gap-1.5">
                  <Sparkles size={13} /> Keep saved details
                </button>
                <button onClick={() => resolveSavedFormChoice(false)} className="btn-secondary text-xs px-5 py-2.5 rounded-xl">
                  Change something
                </button>
                <button onClick={backToDashboard} className="btn-ghost text-xs px-4 py-2.5">Back</button>
              </div>
            )}

            {!typing && phase === "modify_pick" && (
              <div className="pl-9 space-y-2 pt-2 max-w-md message-enter">
                {modifyFieldOptions.map((opt) => {
                  const selected = modifySelection.includes(opt.id);
                  return (
                    <label
                      key={opt.id}
                      className="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer text-xs transition-all"
                      style={{
                        background: selected ? "rgba(6,182,212,0.12)" : "#f8fafc",
                        border: `1.5px solid ${selected ? "#06b6d4" : "rgba(6,182,212,0.2)"}`,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        className="accent-cyan-400"
                        onChange={(e) => {
                          setModifySelection((prev) =>
                            e.target.checked ? [...prev, opt.id] : prev.filter((id) => id !== opt.id),
                          );
                        }}
                      />
                      <span className="font-medium">{opt.label}</span>
                    </label>
                  );
                })}
                <button onClick={confirmModifySelection} className="btn-primary text-xs px-5 py-2.5 rounded-xl mt-1">
                  Continue <ChevronRight size={14} className="inline ml-1" />
                </button>
              </div>
            )}

            {!typing && phase === "asking" && currentQuestion?.type === "program_picker" && (
              <DsxPickerList
                title="Detection programs"
                items={detectionPrograms}
                emptyText={detectionProgramsLoading ? "Loading programs…" : "No programs available — use Set target release on the dashboard first."}
                onSelect={answerDetectionProgram}
              />
            )}

            {!typing && phase === "asking" && currentQuestion?.type === "quickreply" && (
              <div className="flex flex-wrap gap-2 pl-9 pt-2 message-enter">
                {currentQuestion.options.map((opt) =>
                  typeof opt === "string" ? (
                    <button key={opt} onClick={() => answerQuickreply(currentQuestion, opt, opt)} className="btn-secondary text-xs px-4 py-2 rounded-xl">
                      {opt}
                    </button>
                  ) : (
                    <button
                      key={opt.value}
                      onClick={() => answerQuickreply(currentQuestion, opt.value, opt.label)}
                      style={{ border: `1px solid ${opt.color}44`, color: opt.color, background: `${opt.color}12` }}
                      className="text-xs font-semibold px-4 py-2 rounded-xl hover:brightness-125 transition"
                    >
                      {opt.label} <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>· {opt.desc}</span>
                    </button>
                  ),
                )}
              </div>
            )}

            {!typing && phase === "asking" && currentQuestion?.type === "file" && (
              <div className="pl-9 pt-2 flex items-center gap-2 max-w-[400px] message-enter">
                <label className="flex-1 flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-[12px] font-medium cursor-pointer border-2 border-dashed transition hover:border-cyan-400 hover:bg-cyan-50" style={{ borderColor: "rgba(6,182,212,0.35)", color: "#0891B2" }}>
                  <Paperclip size={14} />
                  Attach screenshot or video
                  <input type="file" accept="image/*,video/*" className="hidden" onChange={(e) => answerFile(e.target.files?.[0])} />
                </label>
                <button onClick={() => answerFile(null)} className="btn-secondary text-xs px-4 py-3 rounded-xl">Skip</button>
              </div>
            )}

            {!typing && phase === "duplicates" && duplicates.length > 0 && (
              <div className="pl-9 space-y-2 pt-2 message-enter">
                {duplicates.map((ir) => (
                  <div key={ir.id} className="glass-card rounded-2xl p-4 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-mono font-bold ir-link" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{ir.id}</div>
                      <div className="text-[13px] mt-1 leading-snug">{ir.title}</div>
                    </div>
                    <button onClick={() => resolveDuplicate(true, ir)} className="btn-secondary text-[11px] px-3 py-1.5 rounded-lg whitespace-nowrap flex-shrink-0">
                      This is it
                    </button>
                  </div>
                ))}
                <button onClick={() => resolveDuplicate(false)} className="text-xs font-bold px-4 py-2 rounded-xl transition hover:bg-cyan-50" style={{ color: "#0891B2" }}>
                  None of these — it's new
                </button>
              </div>
            )}

            {!typing && phase === "review" && (
              <div className="pl-9 pt-2 flex flex-wrap gap-2 message-enter">
                <button onClick={confirmCreate} className="btn-primary text-xs px-5 py-2.5 rounded-xl flex items-center gap-1.5">
                  <Zap size={13} /> Confirm &amp; file report
                </button>
                <button onClick={backToDashboard} className="btn-secondary text-xs px-4 py-2.5 rounded-xl">Cancel</button>
              </div>
            )}

            {!typing && phase === "done" && (
              <div className="pl-9 pt-2 message-enter">
                <button onClick={backToDashboard} className="btn-secondary text-xs px-4 py-2 rounded-xl flex items-center gap-1.5">
                  <Plus size={13} /> File another report
                </button>
              </div>
            )}
            </div>

            {!typing && phase === "asking" && (currentQuestion?.type === "text" || currentQuestion?.type === "textarea") && (
              <div className="input-bar p-4 flex flex-col gap-2">
                <div className="flex items-end gap-2">
                  {currentQuestion.type === "textarea" ? (
                    <textarea
                      ref={inputRef}
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder={currentQuestion.placeholder || (currentQuestion.optional ? "Type here, or leave blank to skip" : "Type your answer…")}
                      rows={3}
                      className="ir-input flex-1 rounded-2xl px-4 py-3 text-[13px] resize-none"
                    />
                  ) : (
                    <input
                      ref={inputRef}
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && answerText()}
                      placeholder={currentQuestion.placeholder || (currentQuestion.optional ? "Type or skip" : "Type your answer…")}
                      className="ir-input flex-1 rounded-full px-4 py-3 text-[13px]"
                    />
                  )}
                  <button onClick={answerText} className="btn-primary w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0">
                    <Send size={16} />
                  </button>
                </div>
                {currentQuestion.hint && (
                  <p className="text-[11px] leading-relaxed px-1 flex items-start gap-1.5 font-medium" style={{ color: "#0891B2" }}>
                    <Sparkles size={11} className="mt-0.5 flex-shrink-0" color="#6366F1" />
                    {currentQuestion.hint}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Draft panel */}
          <div className="glass-panel rounded-3xl p-5 h-[min(720px,calc(100vh-140px))] overflow-y-auto ir-scroll flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <ShieldAlert size={16} color="#0891B2" />
                <span className="text-xs font-bold tracking-widest uppercase section-title">
                  {created && !created.existing ? "Filed report" : phase === "review" ? "Review draft" : "Live draft"}
                </span>
              </div>
              {!created && (
                <span className="chip chip-accent">{draftProgress}%</span>
              )}
            </div>

            {!created && (
              <div className="mb-5">
                <div className="progress-track h-1.5">
                  <div className="progress-fill" style={{ width: `${draftProgress}%` }} />
                </div>
              </div>
            )}

          {created && !created.existing ? (
            <div className="space-y-4">
              <div className="success-card glass-card rounded-2xl p-5 flex items-center gap-4">
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center" style={{ background: "rgba(16,185,129,0.2)", border: "2px solid rgba(16,185,129,0.4)" }}>
                  <CheckCircle2 size={26} color="#10B981" />
                </div>
                <div>
                  <div className="text-sm font-bold" style={{ color: "#059669" }}>Created successfully</div>
                  {created.url ? (
                    <a href={created.url} target="_blank" rel="noreferrer" className="ir-link text-xs font-mono inline-flex items-center gap-1.5 mt-1 hover:underline font-bold" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                      {created.id} <ExternalLink size={11} />
                    </a>
                  ) : (
                    <div className="text-xs font-mono mt-1" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>{created.id}</div>
                  )}
                </div>
              </div>
              <Field label="Title" value={created.title} />
              <Field label="Description" value={created.description} multiline />
            </div>
          ) : created?.existing ? (
            <div className="alert-warning glass-card rounded-2xl p-5 flex items-start gap-3">
              <AlertTriangle size={20} color="#F59E0B" className="mt-0.5 flex-shrink-0" />
              <div>
                <div className="text-sm font-bold">Not created — duplicate</div>
                <div className="text-xs mt-1 font-medium" style={{ color: "var(--text-muted)" }}>Linked to {created.id}</div>
              </div>
            </div>
          ) : (
            <div className="space-y-4 flex-1">
              {serviceContext && (
                <div className="glass-card rounded-2xl p-3.5 space-y-2">
                  <div className="text-[10px] uppercase tracking-widest font-bold section-title">Service context</div>
                  <div className="flex flex-wrap gap-2">
                    {serviceContext.parentBrandName && (
                      <span className="chip">{serviceContext.parentBrandName}</span>
                    )}
                    <span className="chip chip-accent">{serviceContext.serviceName}</span>
                    <span className="chip chip-accent">{serviceContext.programName}</span>
                    <span className="chip chip-accent">{serviceContext.releaseLabel || serviceContext.releaseFields?.rel_title}</span>
                  </div>
                </div>
              )}
              {selectedSavedForm && (
                <div className="glass-card rounded-2xl p-3.5 flex items-center gap-3">
                  <div className="icon-box-cyan w-8 h-8 rounded-lg flex items-center justify-center">
                    <FileText size={14} color="#0891B2" />
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-widest font-bold section-title">Saved form</div>
                    <div className="text-[13px] font-bold" style={{ color: "var(--text-primary)" }}>{selectedSavedForm.title}</div>
                  </div>
                </div>
              )}
              <Field label="Title" value={draftMerged.title} placeholder="not set yet" editable={phase === "review"} onChange={(value) => updateDraftField("title", value)} />
              <Field label="Description" value={draftPayload.description} placeholder="not set yet" multiline editable={phase === "review"} onChange={(value) => updateDraftField("description", value)} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Severity" value={draftSeverity?.label} placeholder="not set" color={draftSeverity?.color} badge />
                <Field label="Environment" value={draftMerged.detected_environment} placeholder="not set" />
              </div>
              <Field label="Versions" value={[draftMerged.aura, draftMerged.swym, draftMerged.swymUi].filter(Boolean).join(" · ")} placeholder="not set yet" />
              <Field
                label="Release"
                value={
                  serviceContext?.releaseLabel
                  || draftMerged.rel_title
                  || (hasFilingReleaseContext() ? draftMerged.rel_name : "")
                }
                placeholder="not set yet"
                mono
              />
              <Field label="Feature" value={draftMerged.feature_name} placeholder="not set yet" mono />
              <div className="glass-card rounded-2xl p-3.5 space-y-2">
                <div className="text-[10px] uppercase tracking-widest font-bold section-title">Issue detected version</div>
                <Field
                  label="Version"
                  value={draftMerged.detection_level_name}
                  placeholder="not set yet"
                  mono
                  editable={phase === "review"}
                  onChange={(value) => updateDraftField("detection_level_name", value)}
                />
                <div>
                  <div className="text-[10px] uppercase tracking-widest font-bold mb-1.5" style={{ color: "var(--text-dim)" }}>Linked ID</div>
                  {detectionResolving ? (
                    <div className="flex items-center gap-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
                      <Loader2 size={14} className="animate-spin" />
                      Looking up version…
                    </div>
                  ) : draftMerged.detection_level_eno_id ? (
                    <span className="chip font-mono text-[10px]" title="Detection program physical ID">
                      {draftMerged.detection_level_eno_id}
                    </span>
                  ) : draftMerged.detection_level_name ? (
                    <span className="text-xs font-medium" style={{ color: "#D97706" }}>Not linked yet — will resolve when you file</span>
                  ) : (
                    <span className="text-xs font-medium" style={{ color: "var(--text-dim)" }}>not set yet</span>
                  )}
                </div>
                {detectionResolveError && (
                  <p className="text-[11px] font-medium" style={{ color: "#D97706" }}>
                    {detectionResolveError}
                  </p>
                )}
              </div>
              {(draftMerged.rel_eno_id || draftMerged.feature_eno_id) && (
                <div className="glass-card rounded-2xl p-3.5 space-y-2">
                  <div className="text-[10px] uppercase tracking-widest font-bold section-title">Linked object references</div>
                  <div className="flex flex-wrap gap-2">
                    {draftMerged.rel_eno_id && (() => {
                      const navUrl = navigatorUrlForPhysicalId(draftMerged.rel_eno_id);
                      return navUrl ? (
                        <a
                          href={navUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="chip font-mono text-[10px] inline-flex items-center gap-1"
                          title="Open release in Navigator"
                        >
                          REL: {draftMerged.rel_eno_id}
                          <ExternalLink size={10} />
                        </a>
                      ) : (
                        <span className="chip font-mono text-[10px]" title="Target release">REL: {draftMerged.rel_eno_id}</span>
                      );
                    })()}
                    {draftMerged.feature_eno_id && <span className="chip font-mono text-[10px]" title="Feature">FEAT: {draftMerged.feature_eno_id}</span>}
                  </div>
                </div>
              )}
              <Field label="Attachment" value={draftMerged.attachment?.name} placeholder="none" />
              {staleOidIssues.length > 0 && (
                <div className="alert-missing rounded-2xl p-4">
                  <div className="text-xs font-bold mb-2 flex items-center gap-1.5" style={{ color: "#DC2626" }}>
                    <AlertTriangle size={13} /> Re-link required
                  </div>
                  <p className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
                    {staleOidIssues.join(", ")} {staleOidIssues.length > 1 ? "were" : "was"} edited but still points to the saved template object. Pick a program from the list or revert the name.
                  </p>
                </div>
              )}
              {missingOidIssues.length > 0 && staleOidIssues.length === 0 && (
                <div className="alert-missing rounded-2xl p-4">
                  <div className="text-xs font-bold mb-2 flex items-center gap-1.5" style={{ color: "#DC2626" }}>
                    <AlertTriangle size={13} /> Object link missing
                  </div>
                  <p className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
                    {missingOidIssues.join(", ")} {missingOidIssues.length > 1 ? "need" : "needs"} a 3DEXPERIENCE object reference. Use Set target release and pick from the program list.
                  </p>
                </div>
              )}
              {oidMismatchIssues.length > 0 && (
                <div className="alert-missing rounded-2xl p-4">
                  <div className="text-xs font-bold mb-2 flex items-center gap-1.5" style={{ color: "#DC2626" }}>
                    <AlertTriangle size={13} /> Detection not re-linked
                  </div>
                  <p className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
                    {oidMismatchIssues.join(", ")} name was changed but still points to the saved template object. Pick from the program list before filing.
                  </p>
                </div>
              )}
              {(missingDraftFields.length > 0) && (
                <div className="alert-missing rounded-2xl p-4">
                  <div className="text-xs font-bold mb-2 flex items-center gap-1.5" style={{ color: "#DC2626" }}>
                    <AlertTriangle size={13} /> Fix before filing
                  </div>
                  <ul className="text-[11px] space-y-1 font-medium" style={{ color: "var(--text-muted)" }}>
                    {missingDraftFields.map((label) => (
                      <li key={`missing-${label}`} className="flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: "#EF4444" }} />
                        {label}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {duplicates.length > 0 && (
                <div className="alert-warning glass-card rounded-2xl p-3.5 flex items-center gap-2">
                  <AlertTriangle size={14} color="#F59E0B" />
                  <span className="text-xs font-bold">{duplicates.length} possible duplicate{duplicates.length > 1 ? "s" : ""}</span>
                </div>
              )}
            </div>
          )}
          </div>
        </div>
      </div>
    </div>
  );
}

function DsxPickerList({ title, items, emptyText, hint, probe, onSelect, onBack }) {
  return (
    <div className="pl-9 space-y-3 pt-2 message-enter">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-widest font-bold section-title">{title}</span>
        {onBack && (
          <button type="button" onClick={onBack} className="btn-ghost text-[11px] px-2 py-1 rounded-lg">
            Back
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <div className="space-y-2">
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>{emptyText}</p>
          {hint && <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-dim)" }}>{hint}</p>}
          {probe?.length > 0 && (
            <details className="text-[10px]" style={{ color: "var(--text-dim)" }}>
              <summary className="cursor-pointer hover:text-white transition">API probe details</summary>
              <ul className="mt-2 space-y-0.5 max-h-28 overflow-y-auto ir-scroll">
                {probe.slice(0, 12).map((entry, i) => (
                  <li key={i}>
                    {entry.truncated != null
                      ? `…and ${entry.truncated} more attempts`
                      : formatDsxProbeLine(entry)}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ) : (
        <div className="grid gap-2 max-h-72 overflow-y-auto ir-scroll pr-1">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item)}
              className="glass-card w-full text-left px-4 py-3 rounded-2xl flex items-center gap-3 group"
            >
              <div className="icon-box-violet w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0">
                <Layers size={14} color="#6366F1" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-bold truncate">{item.title || item.name || item.id}</div>
                {item.name && item.title && item.name !== item.title && (
                  <div className="text-[11px] truncate mt-0.5 font-mono" style={{ color: "var(--text-muted)" }}>{item.name}</div>
                )}
              </div>
              <ChevronRight size={16} className="opacity-40 group-hover:opacity-100 transition-opacity" color="#0891B2" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FlowStepper({ steps, activeIndex }) {
  return (
    <div className="flex items-center gap-2">
      {steps.map((step, i) => (
        <React.Fragment key={step.key}>
          <div className="flex items-center gap-1.5 min-w-0">
            <div className={`step-dot ${i < activeIndex ? "done" : ""} ${i === activeIndex ? "active" : ""}`} />
            <span
              className="text-[10px] font-bold uppercase tracking-wider truncate hidden sm:inline"
              style={{ color: i <= activeIndex ? "#0369a1" : "#94a3b8" }}
            >
              {step.label}
            </span>
          </div>
          {i < steps.length - 1 && <div className={`step-line ${i < activeIndex ? "done" : ""}`} />}
        </React.Fragment>
      ))}
    </div>
  );
}

function TypingIndicator({ phase }) {
  const label = phase === "uploading" ? "Uploading media" : phase === "creating" ? "Filing report" : "Thinking";
  return (
    <div className="flex items-center gap-3 pl-9 message-enter">
      <div className="bubble-bot rounded-2xl px-4 py-3 flex items-center gap-3">
        <div className="flex gap-1">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </div>
        <span className="text-xs font-medium" style={{ color: "#0891B2" }}>{label}…</span>
      </div>
    </div>
  );
}

function Field({ label, value, placeholder, multiline, mono, color, editable, onChange, badge }) {
  return (
    <div className="glass-card rounded-xl p-3">
      <div className="text-[10px] uppercase tracking-widest mb-1.5 font-bold section-title">{label}</div>
      {editable ? (
        multiline ? (
          <textarea
            value={value || ""}
            onChange={(e) => onChange?.(e.target.value)}
            placeholder={placeholder}
            rows={5}
            className="ir-input w-full rounded-lg px-3 py-2 text-[13px] leading-relaxed resize-y"
          />
        ) : (
          <input
            value={value || ""}
            onChange={(e) => onChange?.(e.target.value)}
            placeholder={placeholder}
            className="ir-input w-full rounded-lg px-3 py-2 text-[13px]"
          />
        )
      ) : badge && value ? (
        <span
          className="inline-block text-xs font-bold px-2.5 py-1 rounded-lg"
          style={{ color: color || "#0891B2", background: `${color || "#06B6D4"}20`, border: `1.5px solid ${color || "#06B6D4"}50` }}
        >
          {value}
        </span>
      ) : (
        <div
          style={{
            color: value ? (color || "var(--text-primary)") : "#94a3b8",
            fontFamily: mono ? "'JetBrains Mono', monospace" : "inherit",
            fontStyle: value ? "normal" : "italic",
            fontWeight: value ? 500 : 400,
            whiteSpace: multiline ? "pre-wrap" : "normal",
          }}
          className={`text-[13px] leading-relaxed ${multiline ? "" : "truncate"}`}
        >
          {value || placeholder}
        </div>
      )}
    </div>
  );
}
