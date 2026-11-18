"use strict";

const stages = [
  ["intake", "Intake"],
  ["plan", "Scene plan"],
  ["art", "Artwork"],
  ["composition", "Composition"],
  ["review", "Preview & QC"],
  ["export", "Export"],
];

const audioFormats = ".mp3,.flac,.wav,audio/mpeg,audio/flac,audio/wav,audio/x-flac,audio/x-wav";
const newStyleOption = "__new_style__";
const maxStyleArchiveBytes = 512 * 1024 * 1024;
const connectedProbeInterval = 3000;
const disconnectedProbeInterval = 10000;

const state = {
  config: null,
  closed: false,
  connection: "connecting",
  connectionPoll: null,
  checkingConnection: false,
  view: "project",
  settings: null,
  library: null,
  libraryIssues: [],
  libraryError: "",
  libraryStyle: "",
  libraryQuery: "",
  libraryId: "",
  libraryVersion: null,
  libraryProject: "",
  libraryAlias: "",
  libraryAliasPreferred: "",
  projects: [],
  project: null,
  stage: "intake",
  pending: false,
  newTitle: "",
  newStyleId: "",
  newRecording: null,
  styles: [],
  stylesError: "",
  styleEditor: null,
  styleProposal: "",
  interruptedStyleDrafts: new Map(),
  returnToKickoff: false,
  inputDraft: null,
  planDraft: null,
  mixDraft: null,
  timingWord: 1,
  timingModel: null,
  timingModelError: "",
  timingModelPending: false,
  timingModelQuery: 0,
  timingModelDirectory: "",
  timingModelPoll: null,
  assetProofId: "",
  animationReviewId: "",
  previewPosition: null,
  poll: null,
  stopPreview: null,
};

const content = document.querySelector("#stage-content");
const message = document.querySelector("#message");
const connectionStatus = document.querySelector("#connection-status");
const picker = document.querySelector("#projects");
const navigation = document.querySelector("#stages");
const studioNavigation = document.querySelector("#studio-nav");
const workspaceName = document.querySelector("#workspace-name");
const projectName = document.querySelector("#project-name");
const packName = document.querySelector("#pack-name");
const newProject = document.querySelector(".sidebar-new");
const quitButton = document.querySelector("#quit-bacchus");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function localMedia(value) {
  const path = String(value ?? "");
  return path.startsWith("/api/") ? escapeHtml(path) : "";
}

function projectPath(suffix = "", project = state.project) {
  return `/api/projects/${encodeURIComponent(project.id)}${suffix}`;
}

async function apiResponse(path, options) {
  let response;
  try {
    response = await fetch(path, { cache: "no-store", ...options });
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    if (state.connection === "online") probeConnection();
    throw new Error("Bacchus could not reach the local studio. Reopen Bacchus if it has stopped.");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const error = new Error(data?.error || `The studio returned HTTP ${response.status}.`);
    error.status = response.status;
    throw error;
  }
  return response;
}

async function api(path, options) {
  const response = await apiResponse(path, options);
  const data = await response.json().catch(() => null);
  if (!data) throw new Error("The studio returned an unreadable response.");
  return data;
}

function post(path, value) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
}

function mutate(suffix, value = {}) {
  return post(projectPath(suffix), { revision: state.project.revision, ...value });
}

function upload(suffix, file, project = state.project) {
  const query = new URLSearchParams({ revision: project.revision, name: file.name });
  return api(`${projectPath(suffix, project)}?${query}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
}

function setMessage(text = "", type = "") {
  message.textContent = text;
  message.className = `notice ${type}`;
  message.hidden = !text;
}

function setConnection(status) {
  if (state.connection === status) return;
  const labels = {
    online: "Local studio connected",
    offline: state.config ? "Local studio disconnected" : "Studio unavailable",
    closed: "Studio closed",
  };
  state.connection = status;
  document.querySelector(".sidebar").dataset.connection = status;
  connectionStatus.textContent = labels[status];
}

function scheduleConnectionProbe() {
  clearTimeout(state.connectionPoll);
  if (state.closed) return;
  const interval = state.connection === "offline" || document.hidden ? disconnectedProbeInterval : connectedProbeInterval;
  state.connectionPoll = setTimeout(probeConnection, interval);
}

async function probeConnection() {
  if (state.closed || state.checkingConnection) return;
  clearTimeout(state.connectionPoll);
  state.checkingConnection = true;
  let connected = false;
  try {
    const response = await fetch("/api/instance", { cache: "no-store", signal: AbortSignal.timeout(2000) });
    if (response.ok) {
      const { instance } = await response.json();
      connected = typeof instance === "string" && instance.length > 0;
    }
  } catch {
    // The loopback app may have quit, restarted, or become temporarily unresponsive.
  }
  try {
    if (state.closed) return;
    if (!connected) {
      setConnection("offline");
      clearTimeout(state.poll);
      clearTimeout(state.timingModelPoll);
    } else if (!state.config) {
      await initialize();
    } else if (state.connection !== "online") {
      setConnection("online");
      schedulePoll();
      scheduleTimingModelPoll();
    }
  } finally {
    state.checkingConnection = false;
    scheduleConnectionProbe();
  }
}

function openStage(stage, focus = true) {
  state.view = "project";
  state.stage = stage;
  setMessage();
  render();
  if (focus) {
    content.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
}

async function openStudioView(view, alias = "", styleId = "") {
  state.view = view;
  setMessage();
  if (view === "library") {
    setLibraryStyle(styleId || projectStyleId(state.project) || state.libraryStyle || state.styles[0]?.id || "");
    state.libraryAliasPreferred = alias;
    state.libraryAlias = alias || libraryAlias(state.library?.find(asset => asset.id === state.libraryId));
    state.libraryProject = state.project?.id ?? "";
  }
  render();
  content.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  if (view === "library") {
    if (state.stylesError) await refreshStyles();
    if (!state.libraryStyle && state.styles.length) setLibraryStyle(state.styles[0].id);
    await refreshLibrary();
  }
  if (view === "styles") await refreshStyles();
  if (view === "settings" && !state.settings) await refreshSettings();
}

function sceneArtwork(scene) {
  return [scene, ...(scene.layers ?? []).filter(layer => layer.kind === "art")].filter(layer => layer.artId);
}

function pendingArt(project) {
  const approved = new Set(project.assets.filter(asset => asset.approved && !asset.issue).map(asset => asset.id));
  const required = new Set(project.plan?.scenes.flatMap(sceneArtwork).map(layer => layer.artId));
  return [...required].filter(id => !approved.has(id));
}

function artworkComplete(project) {
  if (!project?.plan?.approved || pendingArt(project).length) return false;
  return project.assetReviewReady ?? !project.plan.scenes.some(scene => sceneArtwork(scene).length);
}

function mixComplete(project) {
  return Boolean(project?.mix?.approved && project.mix.rendered?.sha256);
}

function afterArtwork(project) {
  return mixComplete(project) ? "review" : "composition";
}

function afterArtworkButton(project) {
  const stage = afterArtwork(project);
  return goTo(stage, stage === "review" ? "Open preview" : "Open composition");
}

function stageStates(project) {
  return {
    intake: Boolean(project?.input),
    plan: Boolean(project?.plan?.approved),
    art: artworkComplete(project),
    composition: mixComplete(project),
    review: Boolean(project?.accepted),
    export: project?.export?.status === "complete" || Boolean(project?.accepted && project?.lastDelivery),
  };
}

function nextStage(project) {
  if (project.accepted && project.export?.status === "complete") return "export";
  const complete = stageStates(project);
  return stages.find(([id]) => !complete[id])?.[0] || "export";
}

function readDrafts(project) {
  if (state.inputDraft?.id !== project?.id) state.inputDraft = null;
  if (state.planDraft?.id !== project?.id || state.planDraft?.hash !== project?.plan?.hash) {
    state.planDraft = null;
  }
  if (state.mixDraft?.id !== project?.id || state.mixDraft?.revision !== project?.revision) {
    state.mixDraft = null;
  }
}

function upsertProject(project) {
  const summary = { id: project.id, title: project.title, updatedAt: project.updatedAt, styleId: projectStyleId(project) };
  state.projects = [summary, ...state.projects.filter(item => item.id !== project.id)];
}

function render() {
  if (state.closed) return;
  state.stopPreview?.();
  state.stopPreview = null;
  content.querySelectorAll("audio").forEach(audio => audio.pause());
  const project = state.project;
  readDrafts(project);
  picker.innerHTML = '<option value="">Select a project</option>' + state.projects.map(item =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}</option>`
  ).join("");
  picker.value = project?.id ?? "";
  const toolName = state.view === "library" ? "Art library" : state.view === "styles" ? "Styles" : state.view === "settings" ?
    (state.settings?.completed ? "Settings" : "Setup") : null;
  workspaceName.textContent = toolName ? "Studio" : "Projects";
  projectName.textContent = toolName ?? project?.title ?? "New project";
  const style = project?.styleSnapshot;
  packName.textContent = style ? `${style.name} · v${style.revision}` : "";
  packName.hidden = Boolean(toolName) || !packName.textContent;
  quitButton.disabled = !state.config;
  studioNavigation.querySelectorAll("[data-view]").forEach(control => {
    control.disabled = !state.config;
    if (control.dataset.view === state.view) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  });

  const statuses = stageStates(project);
  navigation.innerHTML = project ? stages.map(([id, label]) => `
    <button class="stage-link" type="button" data-stage="${id}"
      data-state="${statuses[id] ? "complete" : "pending"}"${state.view === "project" && state.stage === id ? ' aria-current="step"' : ""}>
      <span class="dot" aria-hidden="true"></span>
      <span>${label}${statuses[id] ? '<span class="sr-only">, complete</span>' : ""}</span>
    </button>`).join("") : "";

  if (!state.config) {
    content.innerHTML = heading("Bacchus Studio", "Connecting to your local workspace.");
    return;
  }

  const views = {
    intake: intakeView, plan: planView, art: artworkView,
    composition: compositionView, review: reviewView, export: exportView,
  };
  if (state.view === "settings") content.innerHTML = settingsView();
  else if (state.view === "library") content.innerHTML = libraryView();
  else if (state.view === "styles") content.innerHTML = stylesView();
  else content.innerHTML = project ? views[state.stage](project) : startView();
  if (content.querySelector("#preview-image")) state.stopPreview = attachPreview(project);
  schedulePoll();
}

function heading(title, description) {
  return `<header class="page-header"><h1>${title}</h1>${description ? `<p>${description}</p>` : ""}</header>`;
}

function button(label, action, options = {}) {
  const { primary, disabled, name, style = "" } = options;
  return `<button type="button" class="button ${primary ? "primary" : ""} ${style}" data-action="${action}"${name ? ` data-name="${escapeHtml(name)}"` : ""}${disabled ? " disabled" : ""}>${label}</button>`;
}

function badge(text, style = "") {
  return `<span class="tag ${style}">${escapeHtml(text)}</span>`;
}

function goTo(stage, label) {
  return `<div class="actions"><button type="button" class="button primary" data-stage="${stage}">${label}</button></div>`;
}

function prerequisite(title, description, stage, label) {
  return `${heading(title)}<section class="panel slim"><p class="muted">${description}</p>${goTo(stage, label)}</section>`;
}

function mediaSettingsNotice(detail) {
  return `<div class="notice actions">
    <p>${detail}</p>
    <button class="button quiet" type="button" data-view="settings">Open Settings</button>
  </div>`;
}

function providerNotice(detail, label) {
  return `<div class="cluster provider-notice">
    <p class="hint">${detail}</p>
    <button class="button quiet small" type="button" data-view="settings">${label}</button>
  </div>`;
}

function readiness(label, available, waitingLabel, readyLabel = "Ready", waitingStyle = "warm") {
  return `<li><span>${label}</span>${badge(available ? readyLabel : waitingLabel, available ? "good" : waitingStyle)}</li>`;
}

function readinessList(settings) {
  const planner = settings.plannerProvider === "openai" ? "OpenAI" : "Anthropic";
  const imagesEnabled = Boolean(settings.useOpenAIImages);
  return [
    readiness("Audio tools", settings.audioAvailable, "Install FFmpeg and ffprobe"),
    readiness("Local word timings", canDeriveTimings(), state.timingModel?.runtimeAvailable === false ? "Runtime unavailable" :
      timingModelIsWorking() ? "Installing" : state.timingModel ? "Needs a timing model" : "Awaiting check"),
    readiness("Video export", settings.renderAvailable, "Install FFmpeg and ffprobe"),
    readiness(`Scene planning · ${planner}`, state.config.plannerAvailable, `Needs ${planner} key`, "Configured"),
    readiness("OpenAI image generation", state.config.imageAvailable, imagesEnabled ? "Needs OpenAI key" : "Off",
      "Configured", imagesEnabled ? "warm" : ""),
  ].join("");
}

function readinessActions(settings) {
  const refresh = button("Refresh checks", "refresh-settings", { style: "quiet" });
  if (settings.mediaInstallAvailable) {
    return `<p class="hint settings-note">Download about 110 MB to install FFmpeg and ffprobe in Bacchus’s local storage.</p>
      <div class="cluster">${button("Install media tools", "install-media-tools", { primary: true })}${refresh}</div>`;
  }
  const download = (!settings.audioAvailable || !settings.renderAvailable)
    ? '<a class="button quiet" href="https://ffmpeg.org/download.html" target="_blank" rel="noopener noreferrer">FFmpeg downloads</a>' : "";
  return `<div class="cluster actions">${refresh}${download}</div>`;
}

function updateReadiness(settings) {
  const list = content.querySelector("#settings-readiness");
  const actions = content.querySelector("#settings-readiness-actions");
  if (list) list.innerHTML = readinessList(settings);
  if (actions) actions.innerHTML = readinessActions(settings);
}

function timingModelIsWorking(model = state.timingModel) {
  return ["downloading", "verifying"].includes(model?.installation?.state);
}

function canDeriveTimings() {
  return state.timingModel?.ready === true;
}

function timingModelStatus() {
  const model = state.timingModel;
  const job = model?.installation;
  const working = timingModelIsWorking(model);
  const loaded = model !== null;
  const canInstall = loaded && model.runtimeAvailable !== false && !working && (!model.ready || model.source === "existing");
  const status = model?.ready ? badge("Verified", "good") : working ? badge("Installing", "warm") :
    state.timingModelError ? badge("Check failed", "error") : model?.runtimeAvailable === false ? badge("Runtime unavailable", "warm") :
      loaded ? badge("Model needed", "warm") : badge("Checking");
  const readyCopy = model?.source === "existing" ? "Using a verified existing installation." : "Using the verified Bacchus installation.";
  const total = Number(job?.totalBytes);
  const downloaded = Number(job?.downloadedBytes);
  const percent = total > 0 ? Math.min(100, Math.floor(100 * downloaded / total)) : null;
  const progress = job?.state === "downloading" ? (percent === null ? "Downloading the timing model…" : `Downloading the timing model · ${percent}%`) :
    job?.state === "verifying" ? "Checking the downloaded model files…" : "";
  const error = state.timingModelError || model?.error || (job?.state === "failed" ? job.error : "");
  const disabled = state.timingModelPending || state.pending;
  const actions = [
    canInstall ? button(job?.state === "failed" ? "Retry model download" : "Install timing model", "install-timing-model",
      { primary: !model.ready, disabled }) : "",
    state.timingModelError ? button("Refresh timing model status", "refresh-timing-model", { style: "quiet small", disabled }) : "",
    model?.existingDirectory ? button("Clear saved model folder", "clear-timing-model-location", { style: "quiet small", disabled }) : "",
  ].join("");
  return `<div class="panel-header"><h2>Local word timings</h2>${status}</div>
    ${model?.ready ? `<p class="hint">${readyCopy}${model.directory ? ` <code class="break-path">${escapeHtml(model.directory)}</code>` : ""}</p>` :
      '<p class="hint">Bacchus uses Parakeet to derive word timings on this computer. You can also import a timing JSON matched to the recording.</p>'}
    ${model?.runtimeAvailable === false ? `<p class="hint">${state.config.audioAvailable ?
      "The local recognition runtime is unavailable in this installation." : "Install the media tools to enable local recognition."}</p>` : ""}
    ${working ? `<p class="hint" role="status">${progress}</p>
      <progress class="timing-progress" aria-label="Timing model download"${percent !== null && job.state === "downloading" ? ` value="${percent}" max="100"` : ""}></progress>` : ""}
    ${state.timingModelPending ? '<p class="hint" role="status">Updating timing model…</p>' : ""}
    ${error ? `<p class="notice${model?.ready && !state.timingModelError ? "" : " error"} inline">${escapeHtml(error)}</p>` : ""}
    ${actions ? `<div class="cluster">${actions}</div>` : ""}
    ${canInstall ? '<p class="hint">The model download is about 482 MB. Bacchus verifies it before use.</p>' : ""}`;
}

function timingModelCard() {
  return `<section class="panel stack">
    <div class="stack" data-timing-model-status>${timingModelStatus()}</div>
    <details class="timing-panel">
      <summary>Use an existing model folder</summary>
      <form class="stack settings-note" data-form="timing-model-location">
        <div class="field">
          <label for="timing-model-directory">Parakeet model folder</label>
          <input id="timing-model-directory" name="directory" value="${escapeHtml(state.timingModelDirectory)}" spellcheck="false" required>
          <p class="hint">Paste the full folder path. Bacchus verifies the model files and remembers a valid location.</p>
        </div>
        <div><button class="button" data-timing-model-verify${state.timingModelPending ? " disabled" : ""}>Verify and use folder</button></div>
      </form>
    </details>
  </section>`;
}

function updateTimingModel() {
  if (state.closed) return;
  const slot = content.querySelector("[data-timing-model-status]");
  if (slot) {
    const focused = slot.contains(document.activeElement) ? document.activeElement.dataset.action : null;
    slot.innerHTML = timingModelStatus();
    if (focused) slot.querySelector(`[data-action="${focused}"]`)?.focus({ preventScroll: true });
  }
  const verify = content.querySelector("[data-timing-model-verify]");
  if (verify) verify.disabled = state.timingModelPending || state.pending;
  const readiness = content.querySelector("#settings-readiness");
  if (readiness) readiness.innerHTML = readinessList(state.settings);
  const derive = content.querySelector("[data-timing-derive]");
  if (derive) derive.disabled = !canDeriveTimings() || state.pending || Boolean(state.project?.busy);
  const hint = content.querySelector("[data-timing-derive-hint]");
  if (hint) hint.textContent = deriveTimingHint();
}

function settingsModel(id, label, value) {
  return `<div class="field"><label for="${id}">${label}</label>
    <input id="${id}" name="${id}" value="${escapeHtml(value)}" maxlength="100" spellcheck="false">
  </div>`;
}

const workflowOverview = [
  "Import a recording and review exact word timings.",
  "Generate or edit a scene plan and approve it.",
  "Approve the artwork and review the soundtrack.",
  "Preview the cut, run quality checks, and export.",
];

function settingsView() {
  const settings = state.settings;
  if (!settings) return heading("Settings", "Loading studio settings.");
  const setup = !settings.completed;
  const provider = settings.plannerProvider === "openai" ? "openai" : "anthropic";
  const keyStatus = value => badge(value ? "Key available" : "No key set", value ? "good" : "");
  return `
    ${heading(setup ? "Set up Bacchus" : "Settings", "Check the media tools and choose your providers and video encoder.")}
    <div class="columns">
      <form class="panel stack" data-form="settings" autocomplete="off">
        <div><h2>Scene planning</h2>
          <p class="hint">Bacchus uses Claude from Anthropic to draft scene plans by default.</p>
        </div>
        <div class="field">
          <label for="plannerProvider">Scene planner</label>
          <select id="plannerProvider" name="plannerProvider">
            ${[
              ["anthropic", "Anthropic (Claude)"],
              ["openai", "OpenAI"],
            ].map(([value, label]) => `<option value="${value}"${provider === value ? " selected" : ""}>${label}</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label for="anthropicKey">Anthropic API key ${keyStatus(settings.hasAnthropicKey)}</label>
          <input id="anthropicKey" type="password" name="anthropicKey" autocomplete="new-password" spellcheck="false">
          ${settings.hasAnthropicKey ? '<label class="music-option hint"><input type="checkbox" name="clearAnthropicKey"> Clear session key</label>' : ""}
        </div>
        ${settingsModel("claudeModel", "Claude model", settings.claudeModel)}
        <fieldset class="setup-fieldset stack optional-settings">
          <legend>Optional OpenAI features</legend>
          <div class="stack provider-switches">
            <label class="music-option"><input type="checkbox" name="useOpenAIImages"${settings.useOpenAIImages === true ? " checked" : ""}> Generate artwork with OpenAI</label>
          </div>
          <div class="field">
            <label for="openAIKey">OpenAI API key ${keyStatus(settings.hasOpenAIKey)}</label>
            <input id="openAIKey" type="password" name="openAIKey" autocomplete="new-password" spellcheck="false">
            ${settings.hasOpenAIKey ? '<label class="music-option hint"><input type="checkbox" name="clearOpenAIKey"> Clear session key</label>' : ""}
            <p class="hint">An OpenAI key is needed for these features or when you choose OpenAI as the scene planner.</p>
          </div>
          <details class="timing-panel">
            <summary>OpenAI model names</summary>
            <div class="stack settings-note">
              ${settingsModel("openAIModel", "OpenAI scene model", settings.openAIModel)}
              ${settingsModel("imageModel", "Image model", settings.imageModel)}
            </div>
          </details>
        </fieldset>
        <div class="field">
          <label for="videoEncoder">Video encoding</label>
          <select id="videoEncoder" name="videoEncoder">
            <option value="auto"${settings.videoEncoder === "cpu" ? "" : " selected"}>Automatic GPU (CPU fallback)</option>
            <option value="cpu"${settings.videoEncoder === "cpu" ? " selected" : ""}>CPU only</option>
          </select>
          <p class="hint">Automatic uses a supported hardware encoder for MP4 export and falls back to the CPU if needed.</p>
        </div>
        <details class="timing-panel">
          <summary>API logs</summary>
          <div class="field settings-note">
            <label for="apiLogDirectory">API log folder</label>
            <input id="apiLogDirectory" name="apiLogDirectory" value="${escapeHtml(settings.apiLogDirectory)}" spellcheck="false" placeholder="Use the temporary-folder default">
            <p class="hint">Logs contain provider request and response bodies, including media. Bacchus omits API credentials.</p>
            ${settings.apiLogPath ? `<p class="hint">Current location <code>${escapeHtml(settings.apiLogPath)}</code></p>` : ""}
          </div>
        </details>
        <p class="hint settings-key-note">API keys apply to the current Bacchus session.</p>
        <div class="cluster">
          <button class="button primary">${setup ? "Finish setup" : "Save settings"}</button>
          ${setup ? button("Continue offline", "skip-setup", { style: "quiet" }) :
            '<button class="button quiet" type="button" data-view="project">Back to studio</button>'}
        </div>
      </form>
      <div class="stack">
        <section class="panel">
          <div class="panel-header"><h2>Readiness</h2></div>
          <ul class="readiness" id="settings-readiness">${readinessList(settings)}</ul>
          <div id="settings-readiness-actions">${readinessActions(settings)}</div>
        </section>
        ${timingModelCard()}
        ${setup ? `<section class="panel">
          <h2>Production stages</h2>
          <ol class="read-list">${workflowOverview.map(step => `<li>${step}</li>`).join("")}</ol>
        </section>` : ""}
        <section class="panel">
          <h2>Storage</h2>
          <div class="stack storage-paths">
            ${settings.dataPath ? `<div><h3>Project data</h3>
              <p class="hint">Project files, imported media, and exports.</p>
              <code>${escapeHtml(settings.dataPath)}</code></div>` : ""}
            <div><h3>Shared art library</h3>
              <p class="hint">Approved artwork available for reuse across projects.</p>
              <code>${escapeHtml(settings.libraryPath)}</code>
              ${settings.libraryMigrationError ? `<p class="notice">${escapeHtml(settings.libraryMigrationError)}</p>` : ""}</div>
          </div>
        </section>
      </div>
    </div>`;
}

function libraryShortcut(label = "Browse art library", alias = "") {
  return `<button class="button quiet" type="button" data-view="library"${alias ? ` data-name="${escapeHtml(alias)}"` : ""}>${label}</button>`;
}

function libraryMatches() {
  const words = state.libraryQuery.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return (state.library ?? []).filter(asset => {
    const versionText = asset.versions.flatMap(({ name, brief, style }) => [name, brief, style]);
    const searchable = [asset.id, asset.alias, asset.name, asset.brief, asset.style, ...versionText].join(" ").toLocaleLowerCase();
    return words.every(word => searchable.includes(word));
  });
}

function libraryAlias(asset) {
  if (!asset) return "";
  if (asset.alias && /^[a-z0-9][a-z0-9-]{0,63}$/.test(asset.alias)) return asset.alias;
  const alias = asset.name.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
    .replace(/\.(png|jpe?g|webp)$/i, "").replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "").slice(0, 64).replace(/-+$/, "");
  return alias || "library-art";
}

function selectLibraryAsset(asset) {
  state.libraryId = asset?.id ?? "";
  state.libraryVersion = asset?.latestVersion ?? null;
  state.libraryAlias = state.libraryAliasPreferred || libraryAlias(asset);
}

function libraryRows(matches) {
  if (state.library === null) return '<p class="hint actions">Loading the art library…</p>';
  if (!state.library.length) return state.libraryIssues.length || state.libraryError
    ? '<p class="hint actions">No readable artwork is available in this library.</p>'
    : '<p class="hint actions">Artwork appears here after you publish it from a reviewed scene use.</p>';
  if (!matches.length) return '<p class="hint actions">No artwork matches that search.</p>';
  return `<ul class="library-list">${matches.map(asset => {
    const latest = asset.versions.find(version => version.version === asset.latestVersion) ?? asset.versions.at(-1);
    const thumb = localMedia(latest?.url);
    return `<li><button type="button" class="library-entry" data-action="select-library-art" data-name="${escapeHtml(asset.id)}" aria-pressed="${state.libraryId === asset.id}">
      <span class="library-thumb">${thumb ? `<img src="${thumb}" alt="" loading="lazy">` : ""}</span>
      <span><span class="asset-title">${escapeHtml(asset.name || asset.id)}</span>
        <span class="asset-subtitle">${asset.alias ? `${escapeHtml(asset.alias)} · ` : ""}${asset.versions.length} ${asset.versions.length === 1 ? "version" : "versions"}</span></span>
    </button></li>`;
  }).join("")}</ul>`;
}

function libraryDate(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.valueOf()) ?
    new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date) : "";
}

function setLibraryStyle(id) {
  if (state.libraryStyle === id) return;
  state.libraryStyle = id;
  state.library = null;
  state.libraryIssues = [];
  state.libraryError = "";
  state.libraryId = "";
  state.libraryVersion = null;
  state.libraryQuery = "";
}

function libraryInspector() {
  const asset = state.library?.find(item => item.id === state.libraryId);
  if (!asset) return "";
  const version = asset.versions.find(item => item.version === state.libraryVersion) ?? asset.versions.at(-1);
  if (!version) return "";
  const image = localMedia(version.url);
  const name = version.name || asset.name || asset.id;
  const usable = version.qualified === true && !version.issue;
  const created = libraryDate(version.createdAt);
  const eligibleProjects = state.project && projectStyleId(state.project) === state.libraryStyle
    ? state.projects.filter(project => project.styleId === state.libraryStyle || project.id === state.project.id) : [];
  const target = eligibleProjects.some(project => project.id === state.libraryProject)
    ? state.libraryProject : state.project?.id;
  return `<section class="panel stack">
    <div class="panel-header"><h2>${escapeHtml(name)}</h2>
      ${version.issue ? badge("Flagged", "error") : usable ? badge("Qualified", "good") : badge("Awaiting qualification", "warm")}
    </div>
    <div class="field">
      <label for="library-version">Version</label>
      <select id="library-version">
        ${[...asset.versions].sort((left, right) => right.version - left.version).map(item => `<option value="${escapeHtml(item.version)}"${version.version === item.version ? " selected" : ""}>Version ${escapeHtml(item.version)}${item.version === asset.latestVersion ? " (latest)" : ""}${item.issue ? " · flagged" : item.qualified === true ? "" : " · awaiting qualification"}</option>`).join("")}
      </select>
    </div>
    ${image ? `<figure class="library-preview"><img src="${image}" alt="${escapeHtml(name)}"></figure>` : ""}
    ${version.brief ? `<p class="hint">${escapeHtml(version.brief)}</p>` : ""}
    ${version.issue?.reason ? `<p class="notice error inline">${escapeHtml(version.issue.reason)}</p>` : ""}
    <dl class="receipt">
      ${version.style ? `<dt>Style</dt><dd>${escapeHtml(styleById(version.style)?.name ?? version.style)}</dd>` : ""}
      ${version.source ? `<dt>Source</dt><dd>${escapeHtml(version.source)}</dd>` : ""}
      ${created ? `<dt>Saved</dt><dd>${escapeHtml(created)}</dd>` : ""}
      <dt>Fingerprint</dt><dd>${shortHash(version.hash)}</dd>
    </dl>
    ${eligibleProjects.length ? `<form class="stack" data-form="library-pin">
      <div class="field"><label for="library-project">Project</label>
        <select id="library-project" name="project">
          ${eligibleProjects.map(project => `<option value="${escapeHtml(project.id)}"${target === project.id ? " selected" : ""}>${escapeHtml(project.title)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label for="library-alias">Artwork ID in project</label>
        <input id="library-alias" name="alias" value="${escapeHtml(state.libraryAlias)}" pattern="[a-z0-9\\-]+" maxlength="64" required>
        <p class="hint">Enter the ID you’ll reference in the scene plan. Use lowercase letters, digits, and hyphens.</p>
      </div>
      <div class="cluster"><button class="button primary"${usable ? "" : " disabled"}>Use in project</button>
        ${state.project ? '<button class="button quiet" type="button" data-view="project">Back to project</button>' : ""}</div>
      <p id="library-pin-status" class="notice inline" role="status" aria-live="polite" hidden></p>
    </form>` : '<p class="hint">Open a project in this style to pin this artwork.</p>'}
  </section>`;
}

function libraryView() {
  const matches = libraryMatches();
  const currentStyle = state.project?.styleSnapshot;
  const visibleStyles = state.styles.length ? state.styles : currentStyle ? [currentStyle] : [];
  const scope = visibleStyles.find(style => style.id === state.libraryStyle);
  if (!matches.some(item => item.id === state.libraryId)) selectLibraryAsset(matches[0]);
  return `
    ${heading("Art library", "Find approved artwork and pin a specific version to a project.")}
    ${styleCatalogNotice()}
    <div class="columns library-columns">
      <section class="panel stack">
        <div class="field"><label for="library-style">Style library</label>
          <select id="library-style"${visibleStyles.length ? "" : " disabled"}>
            ${visibleStyles.length ? "" : '<option>No style libraries loaded</option>'}
            ${visibleStyles.map(style => `<option value="${escapeHtml(style.id)}"${style.id === state.libraryStyle ? " selected" : ""}>${escapeHtml(style.libraryName)} · ${escapeHtml(styleChoiceLabel(style))}</option>`).join("")}
          </select>
          ${scope ? `<p class="hint">${styleAssetCount(scope)}</p>` : ""}
        </div>
        ${scope ? `<div>${button("Export style and library", "export-style-archive", { style: "quiet small", name: scope.id })}</div>` : ""}
        ${libraryRecovery()}
        <div class="field"><label for="library-search">Search artwork</label>
          <input id="library-search" type="search" value="${escapeHtml(state.libraryQuery)}" placeholder="Search by name, brief, or style">
        </div>
        <div id="library-results">${libraryRows(matches)}</div>
      </section>
      <div id="library-inspector">${libraryInspector()}</div>
    </div>`;
}

function libraryRecovery() {
  const error = state.libraryError;
  const issues = state.libraryIssues;
  if (!error && !issues.length) return "";
  return `<div class="notice inline stack">
    <strong>Library needs attention</strong>
    ${error ? `<p class="hint">${escapeHtml(error)}</p>` : ""}
    ${issues.length ? `<p class="hint">Readable artwork remains available. Move damaged metadata aside to clear it from this library and retain a copy for recovery.</p>
      <ul class="read-list">${issues.map(({ id, message }) => `<li class="stack">
        <span>${escapeHtml(message)} <code>${escapeHtml(id)}</code></span>
        <div>${button("Move damaged entry aside", "quarantine-library-entry", { style: "quiet small", name: id })}</div>
      </li>`).join("")}</ul>` : ""}
    <div>${button("Refresh this library", "refresh-library", { style: "quiet small" })}</div>
  </div>`;
}

function updateLibrarySearch() {
  const matches = libraryMatches();
  if (!matches.some(asset => asset.id === state.libraryId)) {
    selectLibraryAsset(matches[0]);
    content.querySelector("#library-inspector").innerHTML = libraryInspector();
  }
  content.querySelector("#library-results").innerHTML = libraryRows(matches);
}

function projectStyleId(project) {
  return project?.styleSnapshot?.id ?? project?.style;
}

function styleById(id) {
  return state.styles.find(style => style.id === id);
}

function styleChoiceLabel(style) {
  const duplicate = state.styles.some(entry => entry.id !== style.id && entry.name === style.name);
  if (!duplicate) return style.name;
  return `${style.name} · ${style.builtIn ? "Built-in" : style.id.slice(-8)}`;
}

function styleAssetCount(style) {
  const count = style.assetCount;
  if (!Number.isFinite(count)) return "Asset count unavailable";
  return `${count} ${count === 1 ? "asset" : "assets"}`;
}

function styleCatalogNotice() {
  return state.stylesError ? `<div class="notice stack">
    <strong>Style catalog unavailable</strong>
    <p class="hint">${escapeHtml(state.stylesError)} You can open an existing project from the sidebar.</p>
    <div>${button("Retry loading styles", "refresh-styles", { style: "quiet small" })}</div>
  </div>` : "";
}

function styleLibraryNotice(style) {
  if (!style?.libraryWarning && !style?.libraryIssueCount) return "";
  return `<div class="notice inline cluster">
    <span>${escapeHtml(style.libraryWarning || `${style.libraryIssueCount} damaged library ${style.libraryIssueCount === 1 ? "entry" : "entries"}.`)}</span>
    ${button("Open library recovery", "open-library-recovery", { style: "quiet small", name: style.id })}
  </div>`;
}

function kickoffStyleSummary(style) {
  if (!style) return "";
  return `<div class="style-summary">
    <p><strong>Version ${escapeHtml(style.revision)}</strong> · ${escapeHtml(style.libraryName)} · ${styleAssetCount(style)}</p>
    ${style.description ? `<p class="hint">${escapeHtml(style.description)}</p>` : ""}
    ${styleLibraryNotice(style)}
  </div>`;
}

const visualBases = { "warm-editorial": "Warm editorial", "studio-ink": "Studio ink", "night-signal": "Night signal" };
const paletteFields = {
  paper: ["Background", "#f8efe0"], ink: ["Text", "#26372d"], muted: ["Secondary text", "#505c50"],
  rule: ["Rules and borders", "#d8c6ac"], accent: ["Accent", "#a65235"],
};

function emptyStyle() {
  return {
    name: "", libraryName: "", description: "", visualPreset: "warm-editorial", palette: {}, guide: "", referenceImages: [],
    answers: {
      visualNotes: "", colorNotes: "", sceneNotes: "", music: null, musicNotes: "",
      musicPreset: "ambient", soundEffects: null, effectsNotes: "",
    },
  };
}

function styleTextarea(name, label, value, rows = 3) {
  return `<div class="field"><label for="style-${name}">${label}</label>
    <textarea id="style-${name}" name="${name}" rows="${rows}">
${escapeHtml(value)}</textarea></div>`;
}

function styleYesNo(name, question, value) {
  return `<fieldset class="setup-fieldset">
    <legend>${question}</legend>
    <div class="cluster">
      <label class="music-option"><input name="${name}" type="radio" value="yes"${value === true ? " checked" : ""} required> Yes</label>
      <label class="music-option"><input name="${name}" type="radio" value="no"${value === false ? " checked" : ""} required> No</label>
    </div>
  </fieldset>`;
}

function paletteEditor(palette) {
  return `<details class="timing-panel">
    <summary>Color overrides</summary>
    <div class="style-palette settings-note">
      ${Object.entries(paletteFields).map(([key, [label, fallback]]) => `<div class="style-color">
        <label class="music-option"><input type="checkbox" name="override-${key}" data-palette-override="${key}"${palette[key] ? " checked" : ""}> ${label}</label>
        <input type="color" id="palette-${key}" name="palette-${key}" value="${escapeHtml(palette[key] || fallback)}"
          aria-label="${label} color"${palette[key] ? "" : " disabled"}>
      </div>`).join("")}
    </div>
  </details>`;
}

function styleReferences(references) {
  return `<section class="stack">
    <h2>Reference images</h2>
    ${references.length ? `<ul class="style-references">${references.map(image => `
      <li>${proofImage(image.url ?? `/api/style-references/${encodeURIComponent(image.sha256)}`, image.name)}
        ${button("Remove reference", "remove-style-reference", { name: image.sha256, style: "quiet small" })}
      </li>`).join("")}</ul>` : ""}
    <div class="field">
      <label for="style-reference-file">PNG or JPEG reference images</label>
      <input id="style-reference-file" type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" multiple>
    </div>
    <div>${button("Add reference images", "upload-style-references", { style: "quiet" })}</div>
  </section>`;
}

function styleForm(style) {
  const answers = style.answers;
  const existing = Boolean(style.id);
  return `<form class="panel stack" data-form="style">
    <div class="panel-header">
      <h2>${existing ? escapeHtml(style.name) : "Create a style"}</h2>
      ${existing ? badge(`Active version ${style.revision}`, "good") : ""}
    </div>
    ${existing ? `<div class="cluster">
      ${button("Export style and library", "export-style-archive", { style: "quiet", name: style.id })}
      <span class="hint">ZIP includes saved style versions, references, and artwork history.</span>
    </div>
    <p class="hint">Saving a new version makes it available to future projects. Existing projects keep their pinned versions.</p>` : ""}
    <div class="field"><label for="style-name">Style name</label>
      <input id="style-name" name="name" value="${escapeHtml(style.name)}" maxlength="120" required>
    </div>
    <div class="field"><label for="style-library-name">Art library name</label>
      <input id="style-library-name" name="libraryName" value="${escapeHtml(style.libraryName)}" maxlength="120" placeholder="Use the style name">
    </div>
    ${styleTextarea("description", "Short description", style.description, 2)}
    <div class="field"><label for="style-visual-preset">Visual base</label>
      <select id="style-visual-preset" name="visualPreset">
        ${Object.entries(visualBases).map(([id, label]) => `<option value="${id}"${style.visualPreset === id ? " selected" : ""}>${label}</option>`).join("")}
      </select>
    </div>
    ${styleTextarea("visualNotes", "What’s the visual palette like?", answers.visualNotes)}
    ${styleTextarea("colorNotes", "What’s the color palette like?", answers.colorNotes)}
    ${paletteEditor(style.palette)}
    ${styleTextarea("sceneNotes", "Any other considerations about how the scenes are constructed?", answers.sceneNotes)}
    ${styleYesNo("music", "Do you want music?", answers.music)}
    ${styleTextarea("musicNotes", "Music guidance", answers.musicNotes, 2)}
    <div class="field"><label for="style-music-preset">Music preset</label>
      <select id="style-music-preset" name="musicPreset">
        <option value="ambient"${answers.musicPreset === "ambient" ? " selected" : ""}>Ambient</option>
        <option value="pulse"${answers.musicPreset === "pulse" ? " selected" : ""}>Pulse</option>
      </select>
    </div>
    ${styleYesNo("soundEffects", "Do you want sound effects?", answers.soundEffects)}
    ${styleTextarea("effectsNotes", "Sound effects guidance", answers.effectsNotes, 2)}
    ${styleReferences(style.referenceImages)}
    ${styleTextarea("guide", "Style guide (Markdown)", style.guide, 10)}
    <div class="field">
      <label for="style-guide-file">Markdown guide file</label>
      <input id="style-guide-file" type="file" accept=".md,.markdown,text/markdown">
      <div>${button("Import Markdown", "import-style-guide", { style: "quiet" })}</div>
    </div>
    ${state.config.styleDraftAvailable ? `<div class="cluster">
      ${button("Draft guide with Claude", "draft-style-guide", { style: "quiet" })}
      <span class="hint">Sends this questionnaire and its reference images to Claude.</span>
    </div>` : ""}
    ${state.styleProposal ? `<section class="stack style-proposal">
      <h2>Claude’s suggested guide</h2>
      <div class="field"><label for="proposed-style-guide">Review the draft</label>
        <textarea id="proposed-style-guide" rows="10" readonly>${escapeHtml(state.styleProposal)}</textarea>
      </div>
      <div class="cluster">
        ${button("Use this draft", "adopt-style-draft")}
        ${button("Discard draft", "discard-style-draft", { style: "quiet" })}
      </div>
    </section>` : ""}
    <div class="cluster">
      <button class="button primary">${existing ? "Save as new version" : "Create style"}</button>
      ${state.returnToKickoff && existing ? button("Use this saved style", "return-to-kickoff", { style: "quiet", name: style.id }) : ""}
      ${state.returnToKickoff ? button("Back to new project", "return-to-kickoff", { style: "quiet" }) : ""}
    </div>
  </form>`;
}

function styleArchiveImport() {
  return `<details class="timing-panel">
    <summary>Import style and library</summary>
    <form class="stack settings-note" data-form="style-archive">
      <p class="hint">Import a Bacchus ZIP to create a separate style and artwork library.</p>
      <div class="field">
        <label for="style-archive-file">Style and library ZIP</label>
        <input id="style-archive-file" name="archive" type="file" accept=".zip,application/zip,application/x-zip-compressed" required>
      </div>
      <div><button class="button">Import ZIP</button></div>
    </form>
  </details>`;
}

function stylesView() {
  return `${heading("Styles", "Set the guide, scene preferences, and approved artwork library for future projects.")}
    ${styleCatalogNotice()}
    <div class="columns library-columns">
      <section class="panel stack">
        <div class="panel-header"><h2>Available styles</h2>${button(state.interruptedStyleDrafts.has("") ? "Resume new style" : "New style", "new-style", { style: "quiet small" })}</div>
        ${styleArchiveImport()}
        ${state.styles.length ? `<ul class="proof-list" aria-label="Available styles">
          ${state.styles.map(style => `<li><button type="button" class="proof-choice" data-action="select-style"
            data-name="${escapeHtml(style.id)}" aria-pressed="${state.styleEditor?.id === style.id}">
            <span><strong>${escapeHtml(styleChoiceLabel(style))}</strong><small>${escapeHtml(style.libraryName)} · ${styleAssetCount(style)}${style.libraryWarning || style.libraryIssueCount ? " · Library needs attention" : ""}</small></span>
            ${badge(`v${style.revision}`)}
          </button></li>`).join("")}
        </ul>` : '<p class="hint">Create a style to make it available at project kickoff.</p>'}
      </section>
      ${state.styleEditor ? `<div class="stack">${styleLibraryNotice(styleById(state.styleEditor.id))}${styleForm(state.styleEditor)}</div>` :
        '<section class="panel"><p class="hint">Select a style to view its guide and preferences.</p></section>'}
    </div>`;
}

function startView() {
  return `
    ${heading("New project", "Choose a style and add the narration recording here or in Intake.")}
    ${styleCatalogNotice()}
    <div class="columns">
      <form class="panel" data-form="new-project">
        <div class="stack">
          <div class="field">
            <label for="new-title">Project name</label>
            <input id="new-title" name="title" maxlength="120" placeholder="e.g. The shape of a good question" value="${escapeHtml(state.newTitle)}" required>
          </div>
          <div class="field">
            <label for="new-style-id">Project style</label>
            <select id="new-style-id" name="styleId" required>
              <option value="">Choose a style</option>
              ${state.styles.map(style => `<option value="${escapeHtml(style.id)}"${style.id === state.newStyleId ? " selected" : ""}>${escapeHtml(styleChoiceLabel(style))}</option>`).join("")}
              <option value="${newStyleOption}">New style…</option>
            </select>
            <div id="kickoff-style-details" aria-live="polite">${kickoffStyleSummary(styleById(state.newStyleId))}</div>
          </div>
          ${state.config.audioAvailable ? `<div class="field">
            <label for="new-recording">Narration recording (optional)</label>
            <input id="new-recording" type="file" accept="audio/wav,audio/mpeg,audio/flac,.mp3,.flac,.wav">
            <div id="kickoff-recording-details" class="cluster" aria-live="polite">${kickoffRecordingSummary()}</div>
          </div>` : ""}
        </div>
        <div class="actions"><button class="button primary">Create project</button></div>
      </form>
      <section class="panel">
        <h2>Example project</h2>
        <p class="muted"><cite>The City After Dark</cite> is a short sample with an approved plan. Use it to try preview, QC, and export.</p>
        ${button("Create example", "demo", { style: "quiet" })}
      </section>
    </div>`;
}

function kickoffRecordingSummary() {
  if (!state.newRecording) return '<span class="hint">MP3, FLAC, or WAV</span>';
  return `<strong>${escapeHtml(state.newRecording.name)}</strong>
    ${button("Clear recording", "clear-new-recording", { style: "quiet small" })}`;
}

function timecode(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  return `${minutes}:${String(value % 60).padStart(2, "0")}`;
}

function shortHash(hash) {
  return `<code title="${escapeHtml(hash)}">${escapeHtml(String(hash ?? "").slice(0, 12))}</code>`;
}

function proofStatus(status) {
  if (status === "passed") return badge("Approved", "good");
  if (status === "failed") return badge("Rejected", "error");
  if (status === "error") return badge("Proof error", "error");
  if (status === "pending") return badge("Awaiting proof", "warm");
  return badge("Awaiting review", "warm");
}

function proofChoice(proofId, title, detail, status, active, action) {
  return `<li><button class="proof-choice" type="button" data-action="${action}" data-name="${escapeHtml(proofId)}"
    aria-pressed="${active}"><span><strong>${escapeHtml(title)}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</span>
    ${proofStatus(status)}</button></li>`;
}

function proofImage(url, label) {
  const src = localMedia(url);
  return src ? `<figure><img src="${src}" alt="${escapeHtml(label)}"><figcaption>${escapeHtml(label)}</figcaption></figure>` : "";
}

function proofSeconds(time) {
  return `${Number(time).toFixed(2)} s`;
}

function reviewReason(proof) {
  if (!proof.reason) return "";
  return proof.status === "passed" ? `<p class="hint proof-rejection">Approval note ${escapeHtml(proof.reason)}</p>` :
    `<p class="notice error inline proof-rejection">${escapeHtml(proof.reason)}</p>`;
}

function proofReviewActions(proof, group, approveLabel, rejectLabel, busy) {
  if (!["ready", "passed"].includes(proof.status)) return "";
  const requiresDisposition = proof.advice?.findings?.some(finding => ["fail", "unclear"].includes(finding.status));
  return `<div class="stack proof-decisions">
    ${proof.status === "ready" ? `<form data-form="proof-result" data-proof-group="${group}" data-proof-id="${escapeHtml(proof.id)}" data-passed="true">
      ${requiresDisposition ? `<div class="field">
        <label for="${group}-proof-disposition">Explain your decision on Claude’s findings</label>
        <textarea id="${group}-proof-disposition" name="reason" rows="2" maxlength="1000" required></textarea>
      </div>` : ""}
      <button class="button primary"${busy ? " disabled" : ""}>${approveLabel}</button>
    </form>` : ""}
    <details class="proof-review">
      <summary>${rejectLabel}</summary>
      <form data-form="proof-result" data-proof-group="${group}" data-proof-id="${escapeHtml(proof.id)}">
        <div class="field">
          <label for="${group}-proof-reason">Why are you rejecting this proof?</label>
          <textarea id="${group}-proof-reason" name="reason" rows="2" maxlength="1000" required></textarea>
        </div>
        <button class="button danger"${busy ? " disabled" : ""}>Record rejection</button>
      </form>
    </details>
  </div>`;
}

function proofAdvisory(proof, group, busy) {
  const advice = proof.advice;
  const available = state.config.visualReviewerAvailable && proof.proofUrl && proof.status === "ready";
  if (!available && !advice) return "";
  return `<div class="proof-advisory">
    ${advice ? `<details class="timing-panel" open>
      <summary>Claude advisory findings${advice.model ? ` · ${escapeHtml(advice.model)}` : ""}</summary>
      ${advice.findings?.length ? `<ul class="read-list">${advice.findings.map(finding =>
        `<li><strong>${escapeHtml(finding.id)} · ${escapeHtml(String(finding.status).replaceAll("_", " "))}</strong> ${escapeHtml(finding.evidence)}</li>`).join("")}</ul>` : '<p class="hint">No findings returned.</p>'}
    </details>` : ""}
    ${available && !advice ? `<div class="cluster">
      ${button("Ask Claude to review proof", `advise-${group}`, { name: proof.id, style: "small quiet", disabled: busy })}
      <span class="hint">Sends this evidence to Claude for advisory feedback.</span>
    </div>` : ""}
  </div>`;
}

function timingImport() {
  return `<details class="timing-import">
    <summary>Import word timings</summary>
    <form class="stack" data-form="timings">
      <div class="field">
        <label for="timing-file">Word timing file (JSON)</label>
        <input id="timing-file" type="file" name="file" accept=".json,application/json" required>
        <p class="hint">Use a cue sheet with the original recording’s SHA-256, its duration, and a list of words with start and end times in seconds.</p>
      </div>
      <div><button class="button">Import timing file</button></div>
    </form>
  </details>`;
}

function deriveTimingHint() {
  if (!state.config?.audioAvailable) return "Install the media tools before deriving word timings.";
  if (state.timingModel?.runtimeAvailable === false) return "Local recognition is unavailable. Import recording-matched word timings instead.";
  return canDeriveTimings() ? "Uses the verified Parakeet model on this computer." :
    "Install a timing model or verify an existing folder to derive word timings here.";
}

function timingDerive(busy) {
  return `<details class="timing-import">
    <summary>Derive word timings locally</summary>
    <form class="stack" data-form="derive-timings">
      <p class="hint" data-timing-derive-hint>${deriveTimingHint()}</p>
      <div><button class="button" data-timing-derive${busy || !canDeriveTimings() ? " disabled" : ""}>Derive word timings</button></div>
    </form>
  </details>`;
}

function timingWordFields(word, duration) {
  return `<div class="field"><label for="timing-word-text">Word</label>
      <input id="timing-word-text" name="text" value="${escapeHtml(word.text)}" required>
    </div>
    <div class="mix-controls">
      <div class="field"><label for="timing-word-start">Start (seconds)</label>
        <input id="timing-word-start" name="start" type="number" step="any" min="0" max="${escapeHtml(duration)}" value="${escapeHtml(word.start)}" required>
      </div>
      <div class="field"><label for="timing-word-end">End (seconds)</label>
        <input id="timing-word-end" name="end" type="number" step="any" min="0" max="${escapeHtml(duration)}" value="${escapeHtml(word.end)}" required>
      </div>
    </div>`;
}

function timingReview(project, busy) {
  const words = project.transcript.words;
  const index = Math.min(Math.max(1, state.timingWord), words.length);
  state.timingWord = index;
  const word = words[index - 1];
  const lines = project.transcript.lines ?? [];
  return `<div class="stack">
    <section class="panel stack">
      <div class="panel-header"><h2>Timing review</h2>${badge(`${words.length} words`)}</div>
      <p class="hint">Jump to a cue in the recording to verify the timing and wording.</p>
      <ol class="proof-list" aria-label="Narration cues">
        ${lines.map(line => `<li><button class="proof-choice" type="button" data-action="seek-timing" data-name="${escapeHtml(line.start)}">
          <span><strong>${escapeHtml(line.text)}</strong><small>${proofSeconds(line.start)} to ${proofSeconds(line.end)}</small></span><span>${escapeHtml(line.id)}</span>
        </button></li>`).join("")}
      </ol>
    </section>
    <section class="panel">
      <details class="timing-panel">
        <summary>Correct a word or its timing</summary>
        <form class="stack settings-note" data-form="correct-timing">
          <div class="field"><label for="timing-word-index">Word number</label>
            <input id="timing-word-index" name="index" type="number" min="1" max="${words.length}" value="${index}" required>
          </div>
          <div id="timing-word-fields" class="stack">${timingWordFields(word, project.audio.duration)}</div>
          <div><button class="button"${busy ? " disabled" : ""}>Save word correction</button></div>
        </form>
      </details>
    </section>
  </div>`;
}

function intakeView(project) {
  const input = project.input;
  const audio = project.audio;
  const draft = state.inputDraft;
  const busy = Boolean(project.busy);
  const hasSource = Boolean(audio || input || project.requiresAudio === false);
  const style = project.styleSnapshot ?? styleById(projectStyleId(project));
  const sourceTimings = project.transcript?.timing === "source-cues" && Boolean(project.transcript.words?.length);
  return `
    ${heading("Intake", "Import the narration recording, verify its word timings, and add creative direction.")}
    <div class="columns">
      <div class="stack">
      <section class="panel">
        <div class="panel-header">
          <h2>Narration recording</h2>
          ${input ? badge("Locked", "good") : audio ? badge("Imported", "good") :
            project.requiresAudio === false ? badge("Optional for this project") : badge("Required", "warm")}
        </div>
        ${audio ? `<p class="audio-name">${escapeHtml(audio.name)} · ${timecode(audio.duration)}</p>
          <audio id="narration-source" controls preload="none" aria-label="Listen to narration" src="${localMedia(audio.url)}"></audio>` : input ?
          '<p class="hint">This project has no narration recording.</p>' : ""}
        ${input ? "" : `
          <form data-form="audio" class="stack">
            <div class="field">
              <label for="narration-file">MP3, FLAC, or WAV file</label>
              <input id="narration-file" type="file" name="file" accept="${audioFormats}" required>
              <p class="hint">Up to ${timecode(state.config.maxDuration)} per recording.</p>
            </div>
            <div class="cluster">
              <button class="button ${audio ? "" : "primary"}"${busy || state.config.audioAvailable === false ? " disabled" : ""}>${audio ? "Replace recording" : "Import recording"}</button>
              ${audio ? button("Remove recording", "remove-audio", { style: "quiet danger", disabled: busy }) : ""}
            </div>
          </form>
          ${state.config.audioAvailable === false ? mediaSettingsNotice("Set up the media tools to import a recording.") : ""}`}
      </section>
      ${hasSource ? `<section class="panel">
        <div class="panel-header">
          <h2>Word timings and brief</h2>
          ${input ? badge("Locked", "good") : ""}
        </div>
        ${!input ? `<div class="transcription-control">
          ${sourceTimings ? '<p class="hint">Recording-matched word timings are ready to review.</p>' :
            '<p class="hint">Import a timing file or derive word timings from the recording.</p>'}
          ${audio && !busy ? timingImport() + timingDerive(busy) : ""}
        </div>` : ""}
        <form data-form="input" class="stack">
          ${input || sourceTimings ? `<div class="field">
            <label for="script">Narration transcript</label>
            <textarea id="script" name="script" rows="8" readonly>${escapeHtml(input?.script ?? project.transcript?.script)}</textarea>
          </div>` : ""}
          ${style ? `<div class="field"><span>Project style</span><span>${escapeHtml(style.name)} · v${escapeHtml(style.revision)}</span>
            ${style.guide ? `<details class="timing-panel">
              <summary>Pinned style guide</summary>
              <pre class="style-guide-text">${escapeHtml(style.guide)}</pre>
            </details>` : ""}
          </div>` : ""}
          <div class="field">
            <label for="intention">Creative brief <span class="muted">(optional)</span></label>
            <textarea id="intention" name="intention" rows="3" maxlength="2000" ${input ? "readonly" : ""}
              placeholder="Describe the audience, visual preferences, tone, and what the animation should communicate.">${escapeHtml(input?.intention ?? draft?.intention)}</textarea>
          </div>
          ${input ? `
            <div class="cluster">
              <button class="button primary" type="button" data-stage="plan">Open scene plan</button>
              ${button("Unlock intake", "unlock-input", { style: "quiet", disabled: busy })}
            </div>` : `${sourceTimings ? '<label class="music-option"><input type="checkbox" name="confirmTiming" required> I reviewed the word timings against the recording.</label>' : ""}
            <div><button class="button primary"${busy || !sourceTimings ? " disabled" : ""}>Lock recording and timings</button></div>`}
        </form>
      </section>` : ""}
      </div>
      ${input ? `<section class="panel">
          <h2>Locked intake</h2>
          <dl class="receipt">
            <dt>Narration cues</dt><dd>${input.lines.length}</dd>
            <dt>Duration</dt><dd>${timecode(input.duration)}</dd>
            <dt>Cue timing</dt><dd>${input.timing === "source-cues" ? "Recording-matched word timings" : "Estimated"}</dd>
            <dt>Input fingerprint</dt><dd>${shortHash(input.hash)}</dd>
          </dl>
        </section>` : `<div class="stack">${sourceTimings ? timingReview(project, busy) : ""}${timingModelCard()}</div>`}
    </div>`;
}

function options(values, current) {
  return values.map(value => `<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(value.replaceAll("-", " "))}</option>`).join("");
}

function editedPlan(project) {
  return state.planDraft?.scenes || project.plan?.scenes || [];
}

function plannerReceipt(project) {
  const run = project.plannerLastRun;
  if (!run) return "";
  const tokens = run.usage;
  return `<details class="planner-receipt">
    <summary>Latest AI planning request</summary>
    <dl class="receipt">
      <dt>Model</dt><dd>${escapeHtml(run.model)}</dd>
      <dt>Status</dt><dd>${escapeHtml(run.status)}</dd>
      <dt>Request</dt><dd>${shortHash(run.requestHash)}</dd>
      ${tokens?.input_tokens != null ? `<dt>Input tokens</dt><dd>${escapeHtml(tokens.input_tokens)}</dd>` : ""}
      ${tokens?.output_tokens != null ? `<dt>Output tokens</dt><dd>${escapeHtml(tokens.output_tokens)}</dd>` : ""}
    </dl>
    ${run.error ? `<p class="hint">${escapeHtml(run.error)}</p>` : ""}
  </details>`;
}

function newLayer(id) {
  return {
    id, kind: "text", box: { x: 6, y: 8, w: 48, h: 35 }, text: "", artId: "", artBrief: "", items: [], unit: "",
    enter: { line: 0, word: 0, effect: "show", duration: 0 }, leave: { line: 0, word: 0 },
  };
}

function layerInput(prefix, key, label, value, attributes = "") {
  const id = `${prefix}-${key.replace(".", "-")}`;
  return `<div class="field"><label for="${id}">${label}</label>
    <input id="${id}" data-layer-field="${key}" value="${escapeHtml(value)}" ${attributes}>
  </div>`;
}

function layerEditor(layer, sceneIndex, index, count, project) {
  const prefix = `scene-${sceneIndex}-layer-${index}`;
  const reference = `${sceneIndex}/${index}`;
  const kinds = ["text", "art", "bar-chart", "flow"];
  const effects = ["show", "fade", "rise", "wipe"];
  const chart = layer.kind === "bar-chart";
  const diagram = layer.kind === "flow";
  const lineMax = project.input.lines.length;
  const wordMax = project.input.words?.length ?? 0;
  return `<fieldset class="stack scene-layer" data-layer>
    <legend>Layer ${index + 1}</legend>
    <div class="stack scene-fields">
      ${layerInput(prefix, "id", "Layer ID", layer.id, 'maxlength="64" required')}
      <div class="field"><label for="${prefix}-kind">Layer type</label>
        <select id="${prefix}-kind" data-layer-field="kind">${options(kinds, layer.kind)}</select>
      </div>
    </div>
    <div class="stack scene-fields">
      ${Object.entries({ x: "Left (%)", y: "Top (%)", w: "Width (%)", h: "Height (%)" }).map(([key, label]) =>
        layerInput(prefix, `box.${key}`, label, layer.box[key],
          `type="number" min="${key === "w" || key === "h" ? 1 : 0}" max="100" required`)).join("")}
    </div>
    ${layer.kind === "art" ? `<div class="stack">
      ${layerInput(prefix, "artId", "Artwork ID", layer.artId, 'list="project-art-ids" maxlength="64" required')}
      <div class="field"><label for="${prefix}-art-brief">Artwork brief</label>
        <textarea id="${prefix}-art-brief" data-layer-field="artBrief" maxlength="380" rows="2">${escapeHtml(layer.artBrief)}</textarea>
      </div>
    </div>` : `<div class="field"><label for="${prefix}-text">${chart || diagram ? "Heading" : "Layer text"}</label>
      <textarea id="${prefix}-text" data-layer-field="text" maxlength="220" rows="2"${layer.kind === "text" ? " required" : ""}>${escapeHtml(layer.text)}</textarea>
    </div>`}
    ${chart || diagram ? `<div class="field"><label for="${prefix}-items">${chart ? "Bar labels and values" : "Flow steps"}</label>
      <textarea id="${prefix}-items" data-layer-field="items" rows="3" required>${escapeHtml(layer.items.map(item => chart ? `${item.label} | ${item.value}` : item.label).join("\n"))}</textarea>
      <p class="hint">${chart ? "Enter 1–6 lines in the form Label | Value." : "Enter 2–5 steps, one per line."}</p>
    </div>` : ""}
    ${chart ? layerInput(prefix, "unit", "Chart unit", layer.unit, 'maxlength="24"') : ""}
    <div class="stack scene-fields">
      ${layerInput(prefix, "enter.line", "Entrance cue", layer.enter.line, `type="number" min="0" max="${lineMax}" required`)}
      ${layerInput(prefix, "enter.word", "Entrance word ID", layer.enter.word, `type="number" min="0" max="${wordMax}" required`)}
      <div class="field"><label for="${prefix}-effect">Entrance effect</label>
        <select id="${prefix}-effect" data-layer-field="enter.effect">${options(effects, layer.enter.effect)}</select>
      </div>
      ${layer.enter.effect === "show" ? "" : layerInput(prefix, "enter.duration", "Effect length (seconds)", layer.enter.duration,
        'type="number" min=".001" max="3" step="any" required')}
      ${layerInput(prefix, "leave.line", "Exit cue", layer.leave.line, `type="number" min="0" max="${lineMax}" required`)}
      ${layerInput(prefix, "leave.word", "Exit word ID", layer.leave.word, `type="number" min="0" max="${wordMax}" required`)}
    </div>
    <div class="cluster">
      ${button("Move back", "layer-back", { name: reference, style: "quiet small", disabled: index === 0 })}
      ${button("Move forward", "layer-forward", { name: reference, style: "quiet small", disabled: index === count - 1 })}
      ${button("Remove layer", "remove-layer", { name: reference, style: "quiet danger small" })}
    </div>
  </fieldset>`;
}

function sceneWords(scene, source) {
  if (!source.words?.length) return "";
  const start = scene.startLine === 1 ? 0 : source.lines[scene.startLine - 1]?.start ?? 0;
  const end = source.lines[scene.endLine]?.start ?? source.duration;
  const words = source.words.filter(word => word.start >= start && word.start < end);
  if (!words.length) return "";
  return `<details class="timing-panel">
    <summary>Show word IDs for this scene</summary>
    <ol class="layer-word-list">${words.map(word => `<li><strong>${word.id}</strong> ${escapeHtml(word.text)} <small>${proofSeconds(word.start)}</small></li>`).join("")}</ol>
  </details>`;
}

function sceneEditor(scene, index, project, expanded) {
  const prefix = `scene-${index}`;
  const lines = project.input.lines.length;
  const title = scene.title || `Scene ${index + 1}`;
  const layered = scene.layout === "layered";
  const layers = scene.layers ?? [];
  return `
    <details class="scene-editor"${expanded ? " open" : ""} data-scene="${index}" data-layout="${escapeHtml(scene.layout)}">
      <summary>${escapeHtml(title)}<span class="scene-meta">Cues ${escapeHtml(scene.startLine)}–${escapeHtml(scene.endLine)} · ${escapeHtml(scene.layout)}</span></summary>
      <div class="stack">
        <div class="stack scene-fields">
          <div class="field"><label for="${prefix}-start">Start cue</label><input id="${prefix}-start" data-field="startLine" type="number" min="1" max="${lines}" value="${escapeHtml(scene.startLine)}" required></div>
          <div class="field"><label for="${prefix}-end">End cue</label><input id="${prefix}-end" data-field="endLine" type="number" min="1" max="${lines}" value="${escapeHtml(scene.endLine)}" required></div>
          <div class="field"><label for="${prefix}-layout">Layout</label><select id="${prefix}-layout" data-field="layout">${options(state.config.layouts, scene.layout)}</select></div>
          ${layered ? '<input type="hidden" data-field="motion" value="still">' :
            `<div class="field"><label for="${prefix}-motion">Motion</label><select id="${prefix}-motion" data-field="motion">${options(state.config.motions, scene.motion)}</select></div>`}
        </div>
        <div class="field"><label for="${prefix}-title">Title</label><input id="${prefix}-title" data-field="title" value="${escapeHtml(scene.title)}"></div>
        <div class="field"><label for="${prefix}-caption">On-screen caption</label><textarea id="${prefix}-caption" data-field="caption" rows="2">${escapeHtml(scene.caption)}</textarea></div>
        ${layered ? `<div class="stack">
          <h3>Layers in back-to-front order</h3>
          <p class="hint">Place layers using percentages of the content area. Use either a cue or a word ID for each entrance or exit. Two zeroes select the scene edge.</p>
          ${sceneWords(scene, project.input)}
          ${layers.map((layer, layerIndex) => layerEditor(layer, index, layerIndex, layers.length, project)).join("")}
          <div>${button("Add layer", "add-layer", { name: index.toString(), disabled: layers.length >= (state.config.maxLayers ?? 12) })}</div>
        </div>` : `<div class="field"><label for="${prefix}-art">Artwork ID</label><input id="${prefix}-art" data-field="artId" list="project-art-ids" pattern="[a-z0-9\\-]*" value="${escapeHtml(scene.artId)}" placeholder="e.g. opening-collage"></div>
        <div class="field"><label for="${prefix}-brief">Artwork brief</label><textarea id="${prefix}-brief" data-field="artBrief" rows="2">${escapeHtml(scene.artBrief)}</textarea></div>`}
        <div>${button("Remove scene", "remove-scene", { name: index.toString(), style: "quiet danger small" })}</div>
      </div>
    </details>`;
}

function planView(project) {
  if (!project.input) return prerequisite("Scene plan", "Import a recording and lock the transcript to plan the animation.", "intake", "Open intake");
  const plan = project.plan;
  const scenes = editedPlan(project);
  const busy = Boolean(project.busy);
  const remaining = state.config.maxPlannerCalls == null ? null :
    Math.max(0, state.config.maxPlannerCalls - (project.plannerCalls || 0));
  const provider = plannerProvider();
  const planStatus = plan?.approved ? badge("Approved", "good") : plan ? badge("Ready for review", "warm") : "";
  const next = artworkComplete(project) ? afterArtworkButton(project) : goTo("art", "Open artwork");
  return `
    ${heading("Scene plan", "Review every scene against the narration, then approve the saved plan.")}
    <div class="columns">
      <section class="panel">
        <div class="panel-header"><h2>Scenes</h2>${planStatus}</div>
        ${plan ? `
          <p class="hint">Source: ${escapeHtml(plan.source)} · ${shortHash(plan.hash)}</p>
          <form data-form="plan">
            <datalist id="project-art-ids">${project.assets.map(asset => `<option value="${escapeHtml(asset.id)}"></option>`).join("")}</datalist>
            ${scenes.length ? scenes.map((scene, index) => sceneEditor(scene, index, project, index === 0)).join("") :
            '<p class="muted">Add a scene to continue.</p>'}
            <div class="cluster actions">
              ${button("Add scene", "add-scene", { style: "quiet", disabled: busy || scenes.length >= state.config.maxScenes })}
              <button class="button" data-plan-save${!state.planDraft || busy || !scenes.length ? " disabled" : ""}>Save changes</button>
              ${!plan.approved || state.planDraft ? button("Approve saved plan", "approve-plan", {
                primary: true,
                disabled: Boolean(state.planDraft || busy),
              }) : ""}
            </div>
            <p class="hint actions" data-plan-dirty${state.planDraft ? "" : " hidden"}>Save your scene edits before approving this draft.</p>
          </form>
          ${plan.approved ? `<div data-plan-next${state.planDraft ? " hidden" : ""}>${next}</div>` : ""}
        ` : '<p class="muted">Generate a draft to assign layouts, motion, and artwork to the narration cues.</p>'}
        <div class="cluster actions">
          ${button(plan ? "Regenerate with AI" : "Generate with AI", "generate-plan", {
            primary: !plan,
            disabled: !state.config.plannerAvailable || remaining === 0 || busy,
          })}
          ${button(plan ? "Replace with starter plan" : "Start with a basic plan", "starter-plan", { disabled: busy })}
          ${libraryShortcut("Browse reusable artwork")}
        </div>
        ${!state.config.plannerAvailable
          ? providerNotice(`Add an ${provider} key to draft scene plans, or use a basic plan locally.`, "Planner settings")
          : `<p class="hint actions">${remaining === null ? "" : `${remaining} AI ${remaining === 1 ? "request remains" : "requests remain"} for this project. `}Generation sends the transcript and brief to ${escapeHtml(provider)}.</p>`}
        ${plannerReceipt(project)}
      </section>
      <section class="panel">
        <div class="panel-header"><h2>Narration cues</h2>${badge(`${project.input.lines.length} cues`)}</div>
        <ol class="read-list">${project.input.lines.map(cue => `<li>${escapeHtml(cue.text)}</li>`).join("")}</ol>
      </section>
    </div>`;
}

function plannerProvider() {
  return state.config.plannerProvider === "openai" ? "OpenAI" : "Anthropic";
}

function assetUseReview(project) {
  const records = project.assetChecks ?? [];
  if (!records.length) return artworkComplete(project) ? afterArtworkButton(project) : "";
  const selected = records.find(record => record.id === state.assetProofId)
    ?? records.find(record => record.status !== "passed") ?? records[0];
  state.assetProofId = selected.id;
  const busy = Boolean(project.busy);
  const pendingImages = pendingArt(project).length;
  const approved = records.filter(record => record.status === "passed").length;
  const needsProof = records.some(record => ["pending", "error"].includes(record.status));
  const original = project.assets.find(asset => asset.id === selected.assetId);
  return `<section class="proof-section" id="artwork-review">
    <div class="columns">
      <section class="panel stack">
        <div class="panel-header">
          <h2>Artwork use review</h2>
          ${badge(`${approved} / ${records.length} approved`, approved === records.length ? "good" : "warm")}
        </div>
        <p class="hint">Check each planned use against the source image and rendered scene.</p>
        ${pendingImages ? '<p class="hint">Resolve artwork issues and approve the required source images before preparing proofs.</p>' : ""}
        ${needsProof && !pendingImages ? `<div>${button("Prepare artwork proofs", "prepare-asset-proofs", { primary: true, disabled: busy })}</div>` : ""}
        <ul class="proof-list" aria-label="Planned artwork uses">
          ${records.map(record => proofChoice(record.id, record.title, [record.assetId, record.layerId].filter(Boolean).join(" · "), record.status, record.id === selected.id,
            "select-asset-proof")).join("")}
        </ul>
        ${artworkComplete(project) ? afterArtworkButton(project) : ""}
      </section>
      <section class="panel stack" id="asset-proof-detail" tabindex="-1">
        <div class="panel-header"><h2>${escapeHtml(selected.title)}</h2>${proofStatus(selected.status)}</div>
        ${selected.proofUrl ? `<div class="proof-comparison">
          ${proofImage(original?.url, "Original artwork")}
          ${proofImage(selected.proofUrl, "Rendered scene proof")}
        </div><p class="hint">Check framing, legibility, and the image’s fit with the approved scene plan.</p>` :
          selected.status === "error" ? "" : '<p class="hint">Prepare the scene proof to review this use.</p>'}
        ${reviewReason(selected)}
        ${selected.status === "failed" ? '<p class="hint">Fix the artwork or update the scene plan, then prepare a new proof.</p>' : ""}
        ${proofAdvisory(selected, "assets", busy)}
        ${selected.proofUrl ? proofReviewActions(selected, "assets", "Approve this use", "Reject this use", busy) : ""}
        ${selected.proofUrl && original && !original.issue ? artworkIssueForm(original, "proof", busy) : ""}
      </section>
    </div>
  </section>`;
}

function artworkIssueForm(asset, section, busy) {
  const prefix = `${section}-issue-${asset.id}`;
  return `<details class="proof-review">
    <summary>Report artwork issue</summary>
    <form class="stack" data-form="art-issue" data-asset-id="${escapeHtml(asset.id)}">
      <div class="field"><label for="${prefix}-kind">Issue type</label>
        <select id="${prefix}-kind" name="kind"><option value="defective">Defective artwork</option><option value="incomplete">Incomplete artwork</option></select>
      </div>
      <div class="field"><label for="${prefix}-reason">What needs to change?</label>
        <textarea id="${prefix}-reason" name="reason" rows="2" maxlength="1000" required></textarea>
      </div>
      <div><button class="button danger"${busy ? " disabled" : ""}>Record artwork issue</button></div>
    </form>
  </details>`;
}

function artworkAssetStatus(asset) {
  if (asset?.issue) return badge(asset.issue.kind === "incomplete" ? "Incomplete" : "Defective", "error");
  if (!asset) return badge("Missing", "warm");
  if (!asset.approved) return badge("Awaiting source approval", "warm");
  return badge("Source approved", "good");
}

function artworkView(project) {
  if (!project.plan?.approved) return prerequisite("Artwork", "Approve a scene plan to see its complete artwork request.", "plan", "Open scene plan");
  const required = new Map(project.plan.scenes.flatMap(sceneArtwork).map(layer => [layer.artId, layer.artBrief]));
  const assets = new Map(project.assets.map(asset => [asset.id, asset]));
  const entries = [...new Set([...required.keys(), ...assets.keys()])];
  const pending = pendingArt(project).length;
  const callsRemaining = Math.max(0, (state.config.maxArtGenerations ?? 0) - (project.artGenerations ?? 0));
  return `
    ${heading("Artwork", "Approve the source images, then review how each image appears in its planned scene.")}
    ${styleLibraryNotice(styleById(projectStyleId(project)))}
    <div class="columns">
      <section class="panel">
        <div class="panel-header">
          <h2>Project artwork</h2>
          ${badge(pending ? `${pending} pending` : "Ready", pending ? "warm" : "good")}
        </div>
        ${entries.length ? `<ul class="art-list">${entries.map(id => {
          const asset = assets.get(id);
          const url = localMedia(asset?.url);
          const canPublish = asset?.publishEligible;
          return `<li class="art-item">
            <div>
              <h3>${escapeHtml(id)}</h3>
              ${required.get(id) ? `<p class="art-copy">${escapeHtml(required.get(id))}</p>` : ""}
              ${asset?.name ? `<p class="art-copy">${escapeHtml(asset.name)}</p>` : ""}
              ${asset?.libraryVersion ? `<p class="art-copy">Library version ${escapeHtml(asset.libraryVersion)}</p>` : ""}
              ${url ? `<img src="${url}" alt="${escapeHtml(id)}" loading="lazy">` : ""}
              ${asset?.libraryWarning ? `<p class="hint">Library: ${escapeHtml(asset.libraryWarning)} The project copy is available.</p>` : ""}
              ${asset?.issue ? `<p class="notice error actions">${escapeHtml(asset.issue.reason)}</p>
                <p class="hint">${required.has(id) ? "Replace the image or update the scene plan to resolve this use." : "Unused in the current scene plan."}</p>` : ""}
            </div>
            <div class="stack">
              ${artworkAssetStatus(asset)}
              ${asset && !asset.approved && !asset.issue ? button("Approve source image", "approve-art", { primary: true, name: id, disabled: Boolean(project.busy) }) : ""}
              ${canPublish ? button("Publish to art library", "publish-art", { name: id, disabled: Boolean(project.busy) }) : ""}
              ${!asset && state.config.imageAvailable ? button("Generate image", "generate-art", {
                name: id,
                disabled: Boolean(project.busy) || !callsRemaining,
                style: "small",
              }) : ""}
              ${button(asset ? "Replace file" : "Select for upload", "select-art", { name: id, style: "quiet small" })}
              ${asset?.issue ? libraryShortcut("Replace from library", id) : asset ? "" : libraryShortcut("Use library artwork", id)}
              ${asset && !asset.issue ? artworkIssueForm(asset, "source", Boolean(project.busy)) : ""}
              ${asset?.issue && required.has(id) ? goTo("plan", "Edit scene use") : ""}
            </div>
          </li>`;
        }).join("")}</ul>` : '<p class="hint">This plan uses the built-in text layouts.</p>'}
        <div class="cluster actions">
          ${libraryShortcut()}
          <span class="hint">You can publish an image after its planned uses pass review.</span>
        </div>
        ${project.missing.length ? state.config.imageAvailable
          ? `<p class="hint actions">${callsRemaining ? "Image generation sends one art brief at a time to OpenAI." : "This project has used its image generation requests."}</p>`
          : providerNotice(state.settings?.useOpenAIImages
            ? "Import your images or add an OpenAI key to generate them in Bacchus."
            : "Copy the combined request into your image tool, then import the files here.", "Image settings") : ""}
      </section>
      <div class="stack">
        ${project.artPrompt ? `<section class="panel">
          <div class="panel-header"><h2>Combined art request</h2></div>
          <div class="field"><label class="sr-only" for="art-request">Combined art request</label>
            <textarea id="art-request" class="request" rows="9" readonly>${escapeHtml(project.artPrompt)}</textarea>
          </div>
          <div class="actions">${button("Copy request", "copy-request")}</div>
        </section>` : ""}
        <form class="panel stack" data-form="art">
          <h2>Import artwork</h2>
          <div class="field">
            <label for="asset-id">Artwork ID</label>
            <input id="asset-id" name="id" list="pending-art-ids" pattern="[a-z0-9\\-]+" placeholder="Use the ID from the plan" required>
            <datalist id="pending-art-ids">${[...required.keys()].filter(id => !assets.has(id)).map(id => `<option value="${escapeHtml(id)}"></option>`).join("")}</datalist>
          </div>
          <div class="field">
            <label for="art-file">PNG or JPEG image</label>
            <input id="art-file" type="file" name="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" required>
          </div>
          <div><button class="button"${project.busy ? " disabled" : ""}>Import artwork</button></div>
        </form>
      </div>
    </div>
    ${assetUseReview(project)}`;
}

function mixSettings(mix) {
  return {
    musicMode: mix.musicMode ?? "",
    musicGainDb: mix.music?.gainDb ?? -24,
    effects: mix.effects.map(({ id, start, gainDb }) => ({ id, start, gainDb })),
    musicRegions: mix.musicRegions ?? [],
  };
}

function mixPreviewUrl(project) {
  const url = project.mix?.rendered?.url ?? projectPath("/mix/preview");
  return `${url}?revision=${encodeURIComponent(project.revision)}`;
}

function hasAudibleTracks(project) {
  return Boolean(project.audio || project.mix?.musicMode === "track" || project.mix?.effects.length);
}

function previewAudio(url, label) {
  const src = localMedia(url);
  return src ? `<audio controls preload="none" aria-label="${escapeHtml(label)}" src="${src}"></audio>` : "";
}

function effectEditor(effect, settings, duration, index, busy) {
  const prefix = `fx-${index}`;
  const lastStart = Math.max(0, duration - .001).toFixed(3);
  return `<li class="mix-item" data-effect="${escapeHtml(effect.id)}">
    <div class="panel-header">
      <h3>${escapeHtml(effect.name)}</h3>
      ${button("Remove effect", "remove-effect", { style: "small quiet danger", name: effect.id, disabled: busy })}
    </div>
    ${previewAudio(effect.url, `Listen to ${effect.name}`)}
    <div class="mix-track"><progress value="${escapeHtml(settings.start)}" max="${escapeHtml(duration)}" aria-label="Cue start on timeline"></progress></div>
    <div class="mix-controls">
      <div class="field">
        <label for="${prefix}-start">Start time (seconds)</label>
        <input id="${prefix}-start" data-fx-start data-mix-setting type="number" min="0" max="${lastStart}" step=".001" value="${escapeHtml(settings.start)}" required>
      </div>
      <div class="field">
        <label for="${prefix}-gain">Level (dB)</label>
        <input id="${prefix}-gain" data-fx-gain data-mix-setting type="number" min="-48" max="0" step=".1" value="${escapeHtml(settings.gainDb)}" required>
      </div>
    </div>
  </li>`;
}

function sceneTiming(project) {
  return `<details class="panel timing-panel">
    <summary>Scene timings</summary>
    <ol class="read-list">${project.plan.scenes.map((scene, index) => `<li><span class="timing-seconds">${Number(sceneStart(project, scene)).toFixed(2)} s</span>
      <span>${escapeHtml(scene.title || `Scene ${index + 1}`)}</span></li>`).join("")}</ol>
  </details>`;
}

function musicRegionEditor(region, index, lineCount) {
  const prefix = `music-region-${index}`;
  return `<li class="mix-item" data-music-region>
    <div class="panel-header"><h3>Region ${index + 1}</h3>
      ${button("Remove region", "remove-music-region", { name: index.toString(), style: "quiet danger small" })}
    </div>
    <div class="mix-controls">
      <div class="field"><label for="${prefix}-start">First cue</label>
        <input id="${prefix}-start" data-region-start data-mix-setting type="number" min="1" max="${lineCount}" value="${escapeHtml(region.startLine)}" required>
      </div>
      <div class="field"><label for="${prefix}-end">Last cue</label>
        <input id="${prefix}-end" data-region-end data-mix-setting type="number" min="1" max="${lineCount}" value="${escapeHtml(region.endLine)}" required>
      </div>
      <div class="field"><label for="${prefix}-gain">Level (dB)</label>
        <input id="${prefix}-gain" data-region-gain data-mix-setting type="number" min="-48" max="0" step="any" value="${escapeHtml(region.gainDb ?? -24)}"${region.gainDb === null ? " disabled" : ""} required>
      </div>
    </div>
    <label class="music-option"><input type="checkbox" data-region-mute data-mix-setting${region.gainDb === null ? " checked" : ""}> Mute music in this region</label>
  </li>`;
}

function measuredValue(value, unit) {
  return Number.isFinite(value) ? `${value.toFixed(2)} ${unit}` : "—";
}

function mixEvidence(rendered) {
  if (!rendered) return "";
  return `<div class="stack" data-mix-evidence>
    <dl class="receipt">
      <dt>Verified WAV</dt><dd>${shortHash(rendered.sha256)}</dd>
      <dt>Duration</dt><dd>${proofSeconds(rendered.duration)}</dd>
      <dt>Format</dt><dd>${escapeHtml(rendered.sampleRate)} Hz · ${escapeHtml(rendered.channels)} channels</dd>
      <dt>Integrated loudness</dt><dd>${measuredValue(rendered.levels?.integratedLufs, "LUFS")}</dd>
      <dt>True peak</dt><dd>${measuredValue(rendered.levels?.truePeakDbtp, "dBTP")}</dd>
    </dl>
    ${rendered.warnings?.length ? `<ul class="read-list">${rendered.warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  </div>`;
}

function styleSoundGuidance(project) {
  const style = project.styleSnapshot;
  if (!style?.answers) return "";
  const { music, musicNotes, soundEffects, effectsNotes } = style.answers;
  return `<section class="panel stack">
    <h2>${escapeHtml(style.name)} sound guidance</h2>
    <dl class="receipt">
      <dt>Music</dt><dd>${music ? "Yes" : "No"}${musicNotes ? `<div class="style-guide-text">${escapeHtml(musicNotes)}</div>` : ""}</dd>
      <dt>Sound effects</dt><dd>${soundEffects ? "Yes" : "No"}${effectsNotes ? `<div class="style-guide-text">${escapeHtml(effectsNotes)}</div>` : ""}</dd>
    </dl>
  </section>`;
}

function compositionView(project) {
  if (!project.input) return prerequisite("Composition", "Lock the narration recording and transcript before composing the soundtrack.", "intake", "Open intake");
  if (!project.plan?.approved) return prerequisite("Composition", "Approve the scene plan before composing the soundtrack.", "plan", "Open scene plan");
  if (!artworkComplete(project)) return prerequisite("Composition", "Approve the planned artwork uses before composing the soundtrack.", "art", "Open artwork");
  const mix = project.mix ?? { musicMode: null, music: null, effects: [], approved: false };
  const rendered = mix.rendered;
  const approved = mixComplete(project);
  const values = state.mixDraft?.settings ?? mixSettings(mix);
  const dirty = Boolean(state.mixDraft);
  const busy = Boolean(project.busy);
  const audioDisabled = busy || state.config.audioAvailable === false;
  const effects = mix.effects;
  const duration = project.input.duration;
  const hasDecision = mix.musicMode === "none" || (mix.musicMode === "track" && mix.music);
  const canListen = Boolean(rendered?.sha256 && hasAudibleTracks(project));
  const full = effects.length >= (state.config.maxEffects ?? 12);
  return `
    ${heading("Composition", "Choose the music, time any sound effects, and listen to the mix before approving it.")}
    <div class="columns">
      <form class="stack" data-form="mix">
        <section class="panel">
          <div class="panel-header">
            <h2>Music</h2>
            ${approved ? badge("Approved", "good") : ""}
          </div>
          <div class="stack">
            <div class="cluster">
              <label class="music-option">
                <input data-mix-setting type="radio" name="musicMode" value="track"${values.musicMode === "track" ? " checked" : ""}${!mix.music ? " disabled" : ""}> Use music track
              </label>
              <label class="music-option">
                <input data-mix-setting type="radio" name="musicMode" value="none"${values.musicMode === "none" ? " checked" : ""}> No music
              </label>
            </div>
            ${mix.music ? `<div class="mix-item">
              <h3>${escapeHtml(mix.music.name)}</h3>
              <p class="hint">${timecode(mix.music.duration)} source · looped to fit the narration</p>
              ${previewAudio(mix.music.url, "Listen to the music source")}
              <div class="field">
                <label for="music-gain">Music level (dB)</label>
                <input id="music-gain" data-mix-setting name="musicGainDb" type="number" min="-48" max="0" step=".1" value="${escapeHtml(values.musicGainDb)}" required>
              </div>
            </div>` : '<p class="hint">Import a track or create a soundtrack to add music.</p>'}
            ${mix.music ? `<section class="stack">
              <h3>Music regions</h3>
              <p class="hint">Set a music level or mute it from the first selected cue through the last selected cue.</p>
              ${values.musicRegions.length ? `<ul class="mix-items">${values.musicRegions.map((region, index) =>
                musicRegionEditor(region, index, project.input.lines.length)).join("")}</ul>` : ""}
              <div>${button("Add music region", "add-music-region", { disabled: busy || values.musicRegions.length >= (state.config.maxMusicRegions ?? 24) })}</div>
            </section>` : ""}
          </div>
        </section>
        <section class="panel">
          <div class="panel-header"><h2>Sound effects</h2>${badge(`${effects.length} / ${state.config.maxEffects ?? 12} cues`)}</div>
          ${effects.length ? `<ul class="mix-items">${effects.map((effect, index) =>
            effectEditor(effect, values.effects[index], duration, index, busy)).join("")}</ul>` : '<p class="hint">No sound effects added.</p>'}
        </section>
        <section class="panel">
          <div class="panel-header"><h2>Review mix</h2></div>
          <div class="stack" data-mix-saved-review${dirty ? " hidden" : ""}>
            ${mixEvidence(rendered)}
            ${canListen ? previewAudio(mixPreviewUrl(project), "Listen to the verified mix") : ""}
            ${rendered && !canListen ? '<p class="hint">This cut is silent.</p>' : ""}
            ${!hasDecision ? '<p class="hint">Save a music choice before preparing the mix.</p>' :
              !rendered ? '<p class="hint">Prepare the WAV and measure its audio before approving it for export.</p>' : ""}
            ${rendered && !approved ? `<label class="music-option"><input id="confirm-mix-review" type="checkbox">
              ${canListen ? "I listened to and checked the verified mix." : "I checked the silent deliverable."}</label>` : ""}
          </div>
          <p class="hint" data-mix-unsaved${dirty ? "" : " hidden"}>Save these settings to prepare and review the updated mix.</p>
          <div class="cluster">
            <button class="button" type="submit" data-mix-save${busy || !dirty || !values.musicMode ? " disabled" : ""}>Save composition</button>
            ${!approved ? button(rendered ? "Prepare mix again" : "Prepare verified mix", "prepare-mix", {
              primary: !rendered,
              disabled: busy || state.config.audioAvailable === false || !hasDecision || dirty,
            }) : ""}
            ${!approved && rendered ? button("Approve composition", "approve-mix", { primary: true, disabled: true }) : ""}
          </div>
          <div data-mix-next${dirty || !approved ? " hidden" : ""}>${goTo("review", "Open preview")}</div>
        </section>
      </form>
      <div class="stack">
        ${styleSoundGuidance(project)}
        <form class="panel stack" data-form="mix-preset">
          <h2>Create soundtrack</h2>
          <p class="hint">Create original background music and place effects at scene changes. This replaces the current soundtrack.</p>
          <div class="field"><label for="soundtrack-preset">Soundtrack style</label>
            <select id="soundtrack-preset" name="preset"><option value="ambient">Ambient</option><option value="pulse">Pulse</option></select>
          </div>
          <div><button class="button"${audioDisabled ? " disabled" : ""}>Create soundtrack</button></div>
        </form>
        <form class="panel stack" data-form="mix-music">
          <h2>Import music</h2>
          <div class="field"><label for="music-file">Music file</label>
            <input id="music-file" type="file" name="file" accept="${audioFormats}" required>
            <p class="hint">MP3, FLAC, or WAV.</p>
          </div>
          <div><button class="button"${audioDisabled ? " disabled" : ""}>${mix.music ? "Replace music" : "Import music"}</button></div>
        </form>
        <form class="panel stack" data-form="mix-effects">
          <h2>Add sound effect</h2>
          <div class="field"><label for="effect-file">Sound effect file</label>
            <input id="effect-file" type="file" name="file" accept="${audioFormats}" required>
            <p class="hint">MP3, FLAC, or WAV. Up to ${escapeHtml(state.config.maxFxDuration ?? 60)} seconds per effect.</p>
          </div>
          <div><button class="button"${audioDisabled || full ? " disabled" : ""}>Add sound effect</button></div>
        </form>
        ${sceneTiming(project)}
      </div>
    </div>`;
}

function showFindings(qc) {
  if (!qc) return '<p class="hint">Run the animation checks to generate scene evidence and inspect the technical findings.</p>';
  if (!qc.findings.length) return '<p class="hint">No findings from the automated checks.</p>';
  return `<ul class="read-list">${qc.findings.map(finding =>
    `<li><strong class="finding-${escapeHtml(finding.level)}">${escapeHtml(finding.level)}</strong> ${escapeHtml(finding.message)}</li>`
  ).join("")}</ul>`;
}

function sceneStart(project, scene) {
  return project.input.lines[scene.startLine - 1].start;
}

function reviewRange(proof, duration) {
  if (!proof) return { start: 0, end: duration };
  const times = proof.samples.map(sample => Number(sample.time));
  return {
    start: Math.max(0, Math.min(Number(proof.start), ...times)),
    end: Math.min(duration, Math.max(Number(proof.end), ...times)),
  };
}

function currentAnimationReview(project) {
  const records = project.animationChecks ?? [];
  if (state.animationReviewId === "full") return null;
  const selected = records.find(record => record.id === state.animationReviewId)
    ?? (project.qc ? records.find(record => record.status !== "passed") : null);
  state.animationReviewId = selected?.id ?? "";
  return selected;
}

function animationPlayer(project, proof) {
  const range = reviewRange(proof, project.input.duration);
  return `<section class="panel" id="animation-player" tabindex="-1">
        ${proof ? `<div class="proof-preview-title"><h2>${escapeHtml(proof.title)}</h2>
          <span class="proof-range">${proofSeconds(range.start)} to ${proofSeconds(range.end)}</span></div>` : ""}
        <div class="preview-frame">
          <img id="preview-image" alt="Current animation frame">
        </div>
        <div class="player-controls">
          <button class="button small" type="button" id="play-preview" aria-pressed="false">${proof ? "Play review range" : "Play silent preview"}</button>
          <input id="preview-time" type="range" min="${escapeHtml(range.start)}" max="${escapeHtml(range.end)}" step=".001" value="${escapeHtml(range.start)}" aria-label="Preview position">
          <span class="timecode"><span id="current-time">${timecode(range.start)}</span> / ${timecode(range.end)}</span>
        </div>
        ${hasAudibleTracks(project) ? `<div class="narration-preview">
          <audio id="preview-narration" preload="none" src="${localMedia(mixPreviewUrl(project))}"></audio>
          <button class="button small quiet" type="button" id="play-narration" aria-pressed="false">${proof ? "Play range with mix" : "Play with mix"}</button>
        </div>` : ""}
        <p id="preview-feedback" class="hint player-feedback" role="status" hidden></p>
        ${proof ? `<ul class="shot-list" aria-label="Jump to a proof sample">
          ${proof.samples.map(sample => `<li><button class="button small" type="button" data-review-sample="${escapeHtml(sample.time)}">
            ${escapeHtml(sample.label)} · ${proofSeconds(sample.time)}</button></li>`).join("")}
        </ul>` : `<ul class="shot-list" aria-label="Jump to scene">
          ${project.plan.scenes.map((scene, index) => `<li><button class="button small" type="button"
            data-preview-time="${escapeHtml(sceneStart(project, scene))}"
            title="${escapeHtml(scene.title || `Scene ${index + 1}`)}"
            aria-label="Scene ${index + 1}${scene.title ? `, ${escapeHtml(scene.title)}` : ""}">${index + 1}</button></li>`).join("")}
        </ul>`}
      </section>`;
}

function animationProof(proof, busy) {
  return `<section class="panel stack" id="animation-proof-detail">
    <div class="panel-header"><h2>Scene and transition proof</h2>${proofStatus(proof.status)}</div>
    ${proof.proofUrl ? `<div class="proof-stills">${proofImage(proof.proofUrl, "Rendered frame samples")}</div>
      <p class="hint">Play the review range or jump to exact samples to check motion, timing, readability, and the cuts on either side.</p>` :
      proof.status === "error" ? "" : '<p class="hint">Run the animation checks to create this proof.</p>'}
    ${reviewReason(proof)}
    ${proof.status === "failed" ? '<p class="hint">Fix the scene or its adjoining transition, then prepare and check the revised animation.</p>' : ""}
    ${proofAdvisory(proof, "animation", busy)}
    ${proof.proofUrl ? proofReviewActions(proof, "animation", "Approve scene and transitions", "Reject scene or transition", busy) : ""}
  </section>`;
}

function captionReviewSummary(review) {
  const total = `${review.cueCount} ${review.cueCount === 1 ? "cue" : "cues"}`;
  return review.method === "timed-words-v1" || review.flaggedCount ? `${total} · ${review.flaggedCount} flagged` : total;
}

function captionTime(value) {
  const milliseconds = Math.round(value * 1000);
  const minutes = Math.floor(milliseconds / 60000);
  const seconds = String(Math.floor(milliseconds / 1000) % 60).padStart(2, "0");
  return `${minutes}:${seconds}.${String(milliseconds % 1000).padStart(3, "0")}`;
}

function captionReviewPanel(review, error) {
  if (!review && !error) return "";
  const issues = review?.issues ?? [];
  const hint = review?.method === "timed-words-v1"
    ? "Review the wording, speaker changes, and meaningful sounds before publishing."
    : "These captions follow the saved line timings.";
  return `<section class="panel slim caption-review" aria-labelledby="caption-review-title">
    <div class="panel-header"><h2 id="caption-review-title">Caption review</h2>
      ${review ? badge(captionReviewSummary(review), review.flaggedCount ? "warm" : "") : ""}
    </div>
    ${error ? `<p class="notice inline">Caption review unavailable. ${escapeHtml(error)}</p>` : `<p class="hint">${hint}</p>`}
    ${review?.flaggedCount > issues.length ? `<p class="hint">Showing ${escapeHtml(issues.length)} of ${escapeHtml(review.flaggedCount)} flagged cues.</p>` : ""}
    ${issues.length ? `<details class="timing-panel">
      <summary>Show flagged cues</summary>
      <ol class="caption-issues" aria-label="Flagged caption cues">
        ${issues.map(issue => `<li>
          <div class="cluster"><strong>Cue ${escapeHtml(issue.cue)}</strong>
            <span class="proof-range">${captionTime(issue.start)} to ${captionTime(issue.end)}</span>
          </div>
          <p class="caption-cue-text">${escapeHtml(issue.text)}</p>
          <ul class="read-list hint">${issue.messages.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
        </li>`).join("")}
      </ol>
    </details>` : ""}
  </section>`;
}

function animationChecksPanel(project, records, proof, captions) {
  const qc = project.qc;
  const busy = Boolean(project.busy);
  const approved = records.filter(record => record.status === "passed").length;
  return `<div class="stack"><section class="panel">
        <div class="panel-header">
          <h2>Animation checks</h2>
          ${project.accepted ? badge("Candidate accepted", "good") :
            qc?.status === "passed" ? badge("Technical checks passed", "good") : qc ? badge("Technical checks failed", "error") : badge("Awaiting checks", "warm")}
        </div>
        ${showFindings(qc)}
        ${qc?.candidateHash ? `<dl class="receipt actions"><dt>Candidate</dt><dd>${shortHash(qc.candidateHash)}</dd></dl>` : ""}
        ${qc?.status === "passed" && !project.animationReviewReady && !project.accepted
          ? '<p class="hint actions">Approve each scene and its transitions to unlock candidate acceptance.</p>' : ""}
        <div class="actions cluster">
          ${project.accepted ? '<button type="button" class="button primary" data-stage="export">Open export</button>' : ""}
          ${!project.accepted && qc?.status === "passed" && project.animationReviewReady ? button("Accept candidate", "accept-candidate", {
            primary: true,
            disabled: busy,
          }) : ""}
          ${!project.accepted ? button(qc ? "Run animation checks again" : "Run animation checks", "qc", {
            primary: !qc,
            disabled: busy,
          }) : ""}
        </div>
      </section>
      ${records.length ? `<section class="panel stack">
        <div class="panel-header"><h2>Scenes and transitions</h2>${badge(`${approved} / ${records.length} approved`, approved === records.length ? "good" : "warm")}</div>
        <ul class="proof-list" aria-label="Scene reviews">
          ${records.map(record => proofChoice(record.id, record.title, `${proofSeconds(record.start)} to ${proofSeconds(record.end)}`,
            record.status, proof?.id === record.id, "select-animation-proof")).join("")}
        </ul>
        ${proof ? `<div>${button("Full animation preview", "full-animation-preview", { style: "quiet small" })}</div>` : ""}
      </section>` : ""}${captions}</div>`;
}

function reviewView(project) {
  const captions = captionReviewPanel(project.captionReview, project.captionReviewError);
  if (!project.animationPrepared && project.accepted && project.export?.status === "complete") {
    return prerequisite("Preview & QC", "Your accepted video is ready to download.", "export", "Open export") + captions;
  }
  if (!project.plan?.approved) return prerequisite("Preview & QC", "Approve a scene plan to prepare animation.", "plan", "Open scene plan") + captions;
  if (!artworkComplete(project)) return prerequisite("Preview & QC", "Approve the planned artwork uses to prepare animation.", "art", "Open artwork") + captions;
  if (!mixComplete(project)) return prerequisite("Preview & QC", "Review and approve the verified mix to prepare animation.", "composition", "Open composition") + captions;
  if (!project.animationPrepared) return `
    ${heading("Preview & QC")}
    <section class="panel slim">
      <h2>Prepare animation</h2>
      <p class="hint">Prepare the preview using the approved scene plan, artwork uses, and audio mix.</p>
      <div class="actions">${button("Prepare animation", "prepare-animation", { primary: true, disabled: Boolean(project.busy) })}</div>
    </section>${captions}`;
  const records = project.animationChecks ?? [];
  const proof = currentAnimationReview(project);
  return `
    ${heading("Preview & QC", "Run the animation checks, then review each scene and its cuts before accepting a candidate.")}
    <div class="columns">
      <div class="stack">${animationPlayer(project, proof)}${proof ? animationProof(proof, Boolean(project.busy)) : ""}</div>
      ${animationChecksPanel(project, records, proof, captions)}
    </div>`;
}

function videoFilename(title) {
  let stem = String(title ?? "").replace(/[<>:"/\\|?*\u0000-\u001f\u007f-\u009f]+/gu, " ")
    .replace(/ {2,}/gu, " ").trim().replace(/[. ]+$/u, "").replace(/\.mp4$/iu, "").replace(/[. ]+$/u, "");
  if (!stem) stem = "Bacchus video";
  const firstPart = stem.split(".", 1)[0].trimEnd();
  if (/^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]|conin\$|conout\$)$/iu.test(firstPart)) stem = `_${stem}`;
  const utf8 = new TextEncoder();
  let bytes = 0;
  let length = 0;
  for (const character of stem) {
    bytes += utf8.encode(character).length;
    if (bytes > 255 - ".mp4".length) break;
    length += character.length;
  }
  return `${stem.slice(0, length).replace(/[. ]+$/u, "")}.mp4`;
}

function exportLink(url, label, { primary = false, filename } = {}) {
  const href = localMedia(url);
  const download = filename ? `download="${escapeHtml(filename)}"` : "download";
  return href ? `<a class="button${primary ? " primary" : ""}" href="${href}" ${download}>${label}</a>` : "";
}

function encoderPreference(mode) {
  return mode === "cpu" ? "CPU only" : "Automatic GPU (CPU fallback)";
}

function deliveryDownloads(delivery, title) {
  return `<div class="actions cluster">
    ${exportLink(delivery.url, "Download MP4", { primary: true, filename: videoFilename(title) })}
    ${exportLink(delivery.captionsUrl, "Download captions")}
    ${exportLink(delivery.receiptUrl, "Download receipt")}
  </div>`;
}

function deliveryNotices(delivery) {
  const fallback = delivery?.media?.encoderFallback;
  const note = delivery?.verificationNote;
  const warnings = Array.isArray(delivery?.warnings) ? delivery.warnings.filter(item => typeof item === "string") : [];
  return `${fallback ? `<p class="notice actions"><strong>CPU fallback.</strong> ${escapeHtml(fallback)}</p>` : ""}
    ${note ? `<p class="hint actions">${escapeHtml(note)}</p>` : ""}
    ${warnings.length ? `<ul class="notice read-list actions">${warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}`;
}

function deliveryCaptionSummary(delivery) {
  return delivery?.captionReview ? `<dt>Caption review</dt><dd>${badge(captionReviewSummary(delivery.captionReview), delivery.captionReview.flaggedCount ? "warm" : "")}</dd>` : "";
}

function previousDelivery(delivery, title) {
  if (delivery?.status !== "complete" || delivery.verification !== "verified") return "";
  return `<section class="panel slim">
    <div class="panel-header"><h2>Previous delivery</h2>${badge("Verified", "good")}</div>
    <p class="hint">This verified copy is still available.</p>
    <dl class="receipt">
      ${delivery.candidateHash ? `<dt>Candidate</dt><dd>${shortHash(delivery.candidateHash)}</dd>` : ""}
      ${delivery.media?.encoder ? `<dt>Video encoder</dt><dd><code>${escapeHtml(delivery.media.encoder)}</code></dd>` : ""}
      ${deliveryCaptionSummary(delivery)}
    </dl>
    ${deliveryNotices(delivery)}
    ${deliveryDownloads(delivery, title)}
  </section>`;
}

function exportView(project) {
  if (!project.accepted) return `<div class="stack">
    ${prerequisite("Export", "Run QC and accept the candidate before exporting a video.", "review", "Open preview & QC")}
    ${previousDelivery(project.lastDelivery, project.title)}
  </div>`;
  const output = project.export;
  const status = output?.status;
  const media = output?.media;
  const running = status === "running" || project.busy === "rendering";
  const complete = status === "complete" && output?.verification === "verified";
  const invalid = status === "invalid" || output?.verification === "corrupt";
  const mode = state.settings?.videoEncoder ?? "auto";
  const differentMode = complete && output.encoderMode && output.encoderMode !== mode;
  const prior = complete ? null : project.lastDelivery;
  return `
    ${heading("Export", "Render the accepted candidate and download the finished files.")}
    <div class="stack"><section class="panel slim">
      <div class="panel-header">
        <h2>Video export</h2>
        ${complete ? badge("Verified", "good") : invalid ? badge("Files failed verification", "error") :
          status === "failed" ? badge("Render failed", "error") : running ? badge("Rendering", "warm") : ""}
      </div>
      <dl class="receipt">
        <dt>Candidate</dt><dd>${shortHash(project.qc?.candidateHash)}</dd>
        <dt>Duration</dt><dd>${timecode(project.input.duration)}</dd>
        <dt>Narration</dt><dd>${project.audio ? escapeHtml(project.audio.name) : "No recording"}</dd>
        ${project.mix ? `<dt>Music</dt><dd>${project.mix.musicMode === "track" ? escapeHtml(project.mix.music?.name) : "No music"}</dd>
          <dt>Sound effects</dt><dd>${project.mix.effects.length}</dd>` : ""}
        ${media?.encoder ? `<dt>Video encoder</dt><dd><code>${escapeHtml(media.encoder)}</code></dd>` : ""}
        ${typeof media?.hardwareEncoding === "boolean"
          ? `<dt>Encoding mode</dt><dd>${media.hardwareEncoding ? "Hardware encoding" : "CPU encoding"}</dd>` : ""}
        ${complete ? deliveryCaptionSummary(output) : ""}
      </dl>
      ${deliveryNotices(output)}
      ${running ? '<p class="hint actions" role="status">Rendering the accepted candidate…</p>' : ""}
      ${status === "failed" ? `<p class="notice error actions">${escapeHtml(output.error || "The render failed.")}</p>` : ""}
      ${invalid ? `<p class="notice error actions">${escapeHtml(output.integrityError || "Bacchus could not verify the delivery files.")}</p>` : ""}
      ${!state.config.renderAvailable ? mediaSettingsNotice("Set up the media tools to export a video.") : ""}
      ${complete ? deliveryDownloads(output, project.title) : ""}
      ${running ? "" : `<div class="actions stack">
        <p class="hint">Next render uses ${encoderPreference(mode)}.${differentMode ? " The encoding setting changed after this delivery." : ""}${complete ? " Rerendering uses the accepted candidate and its existing approvals." : ""}</p>
        <div class="cluster">${button(complete ? "Rerender video" : invalid ? "Render a fresh copy" : status === "failed" ? "Retry export" : "Render video", complete ? "rerender-export" : "export", {
        primary: true,
        disabled: !state.config.renderAvailable,
      })}
      ${complete ? button("Check delivery files", "check-delivery-files", { style: "quiet" }) : ""}
      <button class="button quiet" type="button" data-view="settings">Encoding settings</button></div></div>`}
    </section>
    ${previousDelivery(prior, project.title)}</div>`;
}

function attachPreview(project) {
  const image = content.querySelector("#preview-image");
  const seek = content.querySelector("#preview-time");
  const current = content.querySelector("#current-time");
  const play = content.querySelector("#play-preview");
  const narration = content.querySelector("#preview-narration");
  const playNarration = content.querySelector("#play-narration");
  const feedback = content.querySelector("#preview-feedback");
  const shots = Array.from(content.querySelectorAll("[data-preview-time]"));
  const samples = Array.from(content.querySelectorAll("[data-review-sample]"));
  const starts = project.plan.scenes.map(scene => Number(sceneStart(project, scene)));
  const duration = Number(project.input.duration);
  const proof = currentAnimationReview(project);
  const range = reviewRange(proof, duration);
  const key = [project.id, project.input.hash, project.plan.hash, proof?.id, ...project.assets.map(asset => asset.hash)].join(".");
  const savedPosition = state.previewPosition?.key === key ? state.previewPosition : null;
  const path = projectPath("/preview");
  let timer = null;
  let frameTimer = null;
  let queued = null;
  let fetchController = null;
  let disposed = false;
  let frameUrl = null;
  let playbackId = 0;
  let hasSeeked = Boolean(savedPosition?.hasSeeked);

  async function requestFrame() {
    if (disposed || fetchController || queued == null) return;
    const time = Math.min(queued, Math.max(0, duration - .001));
    queued = null;
    const query = new URLSearchParams({ time, revision: project.revision });
    fetchController = new AbortController();
    try {
      const response = await fetch(`${path}?${query}`, { signal: fetchController.signal });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.error);
      const png = await response.blob();
      if (disposed) return;
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = URL.createObjectURL(png);
      image.src = frameUrl;
      feedback.hidden = true;
    } catch (error) {
      if (disposed || error.name === "AbortError") return;
      queued = null;
      feedback.textContent = error.message || "This frame could not be loaded. Try another point in the preview.";
      feedback.hidden = false;
      pause();
    } finally {
      fetchController = null;
      if (!disposed) requestFrame();
    }
  }

  function show(time, throttle = false) {
    time = Math.max(range.start, Math.min(range.end, time));
    state.previewPosition = { key, time, hasSeeked };
    seek.value = time;
    current.textContent = timecode(time);
    seek.setAttribute("aria-valuetext", proof ? proofSeconds(time) : timecode(time));
    const index = starts.findLastIndex(start => start <= time);
    shots.forEach((shot, shotIndex) => shot.setAttribute("aria-current", String(index === shotIndex)));
    queued = time;
    clearTimeout(frameTimer);
    if (throttle) frameTimer = setTimeout(requestFrame, 100);
    else requestFrame();
  }

  function pause() {
    playbackId += 1;
    clearInterval(timer);
    timer = null;
    narration?.pause();
    play.textContent = proof ? "Play review range" : "Play silent preview";
    play.setAttribute("aria-pressed", "false");
    if (playNarration) {
      playNarration.textContent = proof ? "Play range with mix" : "Play with mix";
      playNarration.setAttribute("aria-pressed", "false");
    }
  }

  function start(withNarration = false) {
    pause();
    const playback = playbackId;
    const offset = !hasSeeked || Number(seek.value) >= range.end ? range.start : Number(seek.value);
    hasSeeked = true;
    const clock = performance.now() - offset * 1000;
    const control = withNarration ? playNarration : play;
    if (withNarration) {
      narration.currentTime = offset;
      narration.play().then(() => {
        if (playback === playbackId) control.textContent = "Pause preview";
      }, () => {
        if (disposed || playback !== playbackId) return;
        pause();
        feedback.textContent = "The mix could not be played.";
        feedback.hidden = false;
      });
    }
    control.textContent = withNarration && narration.readyState < 2 ? "Loading mix…" : "Pause preview";
    control.setAttribute("aria-pressed", "true");
    timer = setInterval(() => {
      const time = Math.min(range.end, withNarration ? narration.currentTime : (performance.now() - clock) / 1000);
      show(time);
      if (time >= range.end || (withNarration && narration.ended)) pause();
    }, proof ? 125 : 250);
    show(offset);
  }

  play.addEventListener("click", () => {
    if (play.getAttribute("aria-pressed") === "true") pause();
    else start();
  });
  playNarration?.addEventListener("click", () => {
    if (playNarration.getAttribute("aria-pressed") === "true") pause();
    else start(true);
  });
  seek.addEventListener("input", () => {
    pause();
    hasSeeked = true;
    show(Number(seek.value), true);
  });
  function visibleSceneTime(index) {
    const start = starts[index];
    const end = starts[index + 1] ?? duration;
    return project.plan.scenes[index].motion === "fade" ? start + Math.min(.55, (end - start) / 2) : start;
  }

  shots.forEach((shot, index) => shot.addEventListener("click", () => {
    pause();
    hasSeeked = true;
    show(visibleSceneTime(index));
  }));
  samples.forEach(sample => sample.addEventListener("click", () => {
    pause();
    hasSeeked = true;
    show(Number(sample.dataset.reviewSample));
  }));
  const firstStill = proof ? Number(proof.samples[Math.floor(proof.samples.length / 2)]?.time ?? range.start) : visibleSceneTime(0);
  show(savedPosition?.time ?? firstStill);
  return () => {
    disposed = true;
    clearTimeout(frameTimer);
    fetchController?.abort();
    if (frameUrl) URL.revokeObjectURL(frameUrl);
    pause();
  };
}

function schedulePoll() {
  clearTimeout(state.poll);
  if (state.closed || state.connection === "offline") return;
  if (!state.project?.busy && state.project?.export?.status !== "running") return;
  const id = state.project.id;
  state.poll = setTimeout(async () => {
    try {
      const current = await api(`/api/projects/${encodeURIComponent(id)}`);
      if (state.closed || state.connection === "offline" || state.project?.id !== id) return;
      if (state.pending) {
        schedulePoll();
        return;
      }
      state.project = current;
      if (state.view === "project") render();
      else schedulePoll();
    } catch {
      if (state.project?.id === id) schedulePoll();
    }
  }, 850);
}

function scheduleTimingModelPoll() {
  clearTimeout(state.timingModelPoll);
  if (!state.closed && state.connection !== "offline" && timingModelIsWorking()) {
    state.timingModelPoll = setTimeout(refreshTimingModel, 850);
  }
}

async function refreshTimingModel() {
  if (state.closed) return;
  if (!state.timingModelPending) {
    const query = ++state.timingModelQuery;
    try {
      const model = await api("/api/timing-model");
      if (query !== state.timingModelQuery) return;
      state.timingModel = model;
      state.timingModelError = "";
    } catch (error) {
      if (query !== state.timingModelQuery) return;
      state.timingModelError = error.message;
    }
    updateTimingModel();
  }
  scheduleTimingModelPoll();
}

async function changeTimingModel(endpoint, payload) {
  if (state.timingModelPending) return;
  state.timingModelQuery += 1;
  clearTimeout(state.timingModelPoll);
  state.timingModelPending = true;
  state.timingModelError = "";
  updateTimingModel();
  try {
    state.timingModel = await post(`/api/timing-model/${endpoint}`, payload);
    if (endpoint === "location") {
      state.timingModelDirectory = state.timingModel.existingDirectory ?? payload.directory;
      const input = content.querySelector("#timing-model-directory");
      if (input) input.value = state.timingModelDirectory;
    }
  } catch (error) {
    state.timingModelError = error.message;
  } finally {
    state.timingModelPending = false;
    updateTimingModel();
    scheduleTimingModelPoll();
  }
}

function beginRequest(busy) {
  state.pending = true;
  content.setAttribute("aria-busy", "true");
  const controls = [picker, newProject, ...navigation.querySelectorAll("button"),
    ...studioNavigation.querySelectorAll("button"), ...content.querySelectorAll("button")];
  const disabledTemporarily = controls.filter(control => !control.disabled);
  disabledTemporarily.forEach(control => { control.disabled = true; });
  setMessage(busy);
  return () => {
    state.pending = false;
    content.removeAttribute("aria-busy");
    disabledTemporarily.forEach(control => { control.disabled = false; });
  };
}

async function quitBacchus() {
  if (state.closed || state.pending || !state.config) return;
  if (!window.confirm("Quit Bacchus?")) return;
  const finish = beginRequest("Quitting Bacchus…");
  try {
    await apiResponse("/api/quit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    state.closed = true;
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  if (!state.closed) return;
  clearTimeout(state.poll);
  clearTimeout(state.timingModelPoll);
  clearTimeout(state.connectionPoll);
  state.stopPreview?.();
  content.querySelectorAll("audio").forEach(audio => audio.pause());
  picker.disabled = true;
  newProject.disabled = true;
  navigation.hidden = true;
  studioNavigation.hidden = true;
  workspaceName.textContent = "Studio";
  projectName.textContent = "Closed";
  packName.hidden = true;
  setConnection("closed");
  setMessage();
  content.innerHTML = heading("Bacchus has quit", "You can close this window.");
  content.focus();
}

async function perform(operation, { busy, success, stage, resetPlan = false, view } = {}) {
  if (state.pending) return;
  const finish = beginRequest(busy || "Saving…");
  let updated = false;
  try {
    const project = await operation();
    if (!project?.id) throw new Error("The studio did not return a project.");
    updated = true;
    if (state.project?.id !== project.id) {
      state.timingWord = 1;
      state.assetProofId = "";
      state.animationReviewId = "";
      try { localStorage.setItem("bacchus-project", project.id); } catch { /* Browser storage is optional. */ }
    }
    state.project = project;
    upsertProject(project);
    if (resetPlan) state.planDraft = null;
    if (view) state.view = view;
    if (typeof stage === "function") state.stage = stage(project);
    else if (stage) state.stage = stage;
    setMessage(success || "", success ? "good" : "");
  } catch (error) {
    setMessage(error.message, "error");
    if (state.project && (error.status === 409 || error.status === 422)) {
      try {
        const current = await api(projectPath());
        if (current.revision !== state.project.revision) {
          state.project = current;
          upsertProject(current);
          updated = true;
        }
      } catch { /* Keep the previous project available while the connection recovers. */ }
    }
  } finally {
    finish();
    if (updated) render();
    else schedulePoll();
  }
}

async function refreshSettings() {
  if (state.pending) return;
  const finish = beginRequest("Checking studio tools…");
  const hadSettings = Boolean(state.settings);
  try {
    const [settings, config] = await Promise.all([api("/api/settings"), api("/api/config")]);
    state.settings = settings;
    state.config = config;
    if (hadSettings) {
      updateReadiness(settings);
    } else if (state.view === "settings") {
      render();
    }
    setMessage("Readiness refreshed.", "good");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  refreshTimingModel();
}

async function installMediaTools(control) {
  if (state.pending || !state.settings?.mediaInstallAvailable) return;
  const finish = beginRequest("Downloading and installing media tools…");
  const label = control.textContent;
  control.textContent = "Installing media tools…";
  try {
    const settings = await post("/api/media-tools/install", {});
    state.settings = settings;
    const config = await api("/api/config").catch(() => null);
    if (config) state.config = config;
    updateReadiness(settings);
    setMessage("Media tools installed.", "good");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    control.textContent = label;
    finish();
  }
}

async function saveSettings(payload, setup) {
  if (state.pending) return;
  const finish = beginRequest("Applying studio settings…");
  let updated = false;
  try {
    state.settings = await post("/api/settings", payload);
    updated = true;
    state.config = await api("/api/config");
    if (setup) state.view = "project";
    setMessage(setup ? "" : "Studio settings saved.", setup ? "" : "good");
  } catch (error) {
    const secrets = [payload.openAIKey, payload.anthropicKey].filter(Boolean);
    const sanitized = secrets.reduce((detail, secret) => detail.replaceAll(secret, "[hidden]"), error.message);
    setMessage(sanitized, "error");
  } finally {
    finish();
    if (updated) render();
  }
}

function settingsPayload(data) {
  const payload = {
    completed: true,
    plannerProvider: data.get("plannerProvider"),
    videoEncoder: data.get("videoEncoder"),
    useOpenAIImages: data.has("useOpenAIImages"),
    apiLogDirectory: data.get("apiLogDirectory")?.trim() ?? "",
    clearOpenAIKey: data.has("clearOpenAIKey"),
    clearAnthropicKey: data.has("clearAnthropicKey"),
  };
  for (const field of ["openAIKey", "anthropicKey", "openAIModel", "claudeModel", "imageModel"]) {
    const value = data.get(field)?.trim();
    if (value) payload[field] = value;
  }
  return payload;
}

async function refreshStyles() {
  try {
    const result = await api("/api/styles");
    state.styles = result.styles;
    state.stylesError = "";
    if (state.view === "styles" && !state.styleEditor && state.styles.length) {
      state.styleEditor = structuredClone(state.styles[0]);
    }
  } catch (error) {
    state.stylesError = error.message;
  }
  if (!state.pending && (state.view === "styles" || state.view === "library" || (state.view === "project" && !state.project))) render();
}

function captureStyleForm(form) {
  const data = new FormData(form);
  const value = name => String(data.get(name) ?? "");
  const choice = name => data.has(name) ? value(name) === "yes" : null;
  state.styleEditor = {
    ...state.styleEditor,
    name: value("name"),
    libraryName: value("libraryName"),
    description: value("description"),
    visualPreset: value("visualPreset"),
    palette: Object.fromEntries(Object.keys(paletteFields).filter(key => data.has(`override-${key}`))
      .map(key => [key, value(`palette-${key}`)])),
    answers: {
      visualNotes: value("visualNotes"), colorNotes: value("colorNotes"), sceneNotes: value("sceneNotes"),
      music: choice("music"), musicNotes: value("musicNotes"), musicPreset: value("musicPreset"),
      soundEffects: choice("soundEffects"), effectsNotes: value("effectsNotes"),
    },
    guide: value("guide"),
  };
  return state.styleEditor;
}

function stylePayload(style) {
  return {
    name: style.name, libraryName: style.libraryName || style.name, description: style.description,
    visualPreset: style.visualPreset, palette: style.palette, answers: style.answers, guide: style.guide,
    referenceImages: style.referenceImages.map(({ sha256, name, width, height }) => ({ sha256, name, width, height })),
  };
}

function selectStyleEditor(style) {
  const key = style.id ?? "";
  const draft = state.interruptedStyleDrafts.get(key);
  state.styleEditor = draft?.style ?? structuredClone(style);
  state.styleProposal = draft?.proposal ?? "";
  state.interruptedStyleDrafts.delete(key);
}

function recordStyle(style) {
  if (styleById(style.id)) {
    state.styles = state.styles.map(entry => entry.id === style.id ? style : entry);
  } else {
    state.styles.push(style);
  }
}

async function importStyleArchive(archive) {
  if (!archive?.size) {
    setMessage("Choose a Bacchus ZIP to import.", "error");
    return;
  }
  if (archive.size > maxStyleArchiveBytes) {
    setMessage("Choose a ZIP no larger than 512 MiB.", "error");
    return;
  }
  const finish = beginRequest("Importing the style and library…");
  let imported;
  try {
    imported = await api("/api/styles/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: archive });
    if (state.styleEditor) {
      state.interruptedStyleDrafts.set(state.styleEditor.id ?? "", { style: state.styleEditor, proposal: state.styleProposal });
    }
    recordStyle(imported);
    selectStyleEditor(imported);
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  if (!imported) return;
  render();
  setMessage(`Imported ${imported.name} as a separate style and artwork library.`, "good");
  content.querySelector("#style-name")?.focus();
}

async function exportStyleArchive(id) {
  const finish = beginRequest("Preparing the style and library ZIP…");
  try {
    const response = await apiResponse(`/api/styles/${encodeURIComponent(id)}/archive`);
    const archive = await response.blob();
    const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/i)?.[1] ?? "bacchus-style-library.zip";
    const url = URL.createObjectURL(archive);
    try {
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
    setMessage("Style and library ZIP download started.", "good");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
}

function returnToNewProject(styleId) {
  state.returnToKickoff = false;
  if (styleId) state.newStyleId = styleId;
  state.project = null;
  openStage("intake", false);
  content.querySelector("#new-style-id")?.focus();
}

async function saveStyle(form) {
  const style = captureStyleForm(form);
  const payload = stylePayload(style);
  const endpoint = style.id ? `/api/styles/${encodeURIComponent(style.id)}` : "/api/styles";
  if (style.id) payload.revision = style.revision;
  const finish = beginRequest("Saving the style…");
  let saved = null;
  try {
    saved = await post(endpoint, payload);
    recordStyle(saved);
    selectStyleEditor(saved);
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  if (!saved) return;
  if (state.returnToKickoff) returnToNewProject(saved.id);
  else render();
  setMessage(`Saved ${saved.name} version ${saved.revision}.`, "good");
}

async function importStyleGuide() {
  const file = content.querySelector("#style-guide-file").files[0];
  if (!file) {
    setMessage("Choose a Markdown file to import.", "error");
    return;
  }
  const style = captureStyleForm(content.querySelector('[data-form="style"]'));
  if (style.guide && !window.confirm("Replace the current guide in this draft?")) return;
  try {
    const guide = await file.text();
    if (Array.from(guide).length > 24_000) throw new Error("Choose a guide with at most 24,000 characters.");
    style.guide = guide;
    state.styleProposal = "";
    render();
    setMessage("Markdown imported into this style draft.", "good");
    content.querySelector("#style-guide").focus();
  } catch (error) {
    setMessage(error.message, "error");
  }
}

async function addStyleReferences() {
  const form = content.querySelector('[data-form="style"]');
  const files = Array.from(content.querySelector("#style-reference-file").files);
  if (!files.length) {
    setMessage("Choose reference images to add.", "error");
    return;
  }
  const style = captureStyleForm(form);
  const finish = beginRequest("Importing reference images…");
  try {
    for (const file of files) {
      const query = new URLSearchParams({ name: file.name });
      const reference = await api(`/api/style-references?${query}`, {
        method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
      });
      if (!style.referenceImages.some(image => image.sha256 === reference.sha256)) style.referenceImages.push(reference);
    }
    setMessage("Reference images added to this draft.", "good");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
    render();
  }
}

async function draftStyleGuide() {
  const form = content.querySelector('[data-form="style"]');
  if (!form.reportValidity()) return;
  const style = captureStyleForm(form);
  const finish = beginRequest("Claude is drafting the style guide…");
  try {
    const result = await post("/api/styles/draft", stylePayload(style));
    if (!result.guide) throw new Error("Claude returned an empty style guide.");
    state.styleProposal = result.guide;
    setMessage("Style guide draft ready to review.", "good");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  if (state.styleProposal) {
    render();
    content.querySelector("#proposed-style-guide")?.focus();
  }
}

async function refreshLibrary() {
  const styleId = state.libraryStyle;
  if (!styleId) {
    state.library = [];
    if (state.view === "library") render();
    return;
  }
  try {
    const query = new URLSearchParams({ styleId });
    const listing = await api(`/api/library?${query}`);
    if (state.libraryStyle !== styleId) return;
    state.library = listing.assets;
    state.libraryIssues = listing.issues ?? [];
    state.libraryError = listing.storageError || "";
  } catch (error) {
    if (state.view === "library" && state.libraryStyle === styleId) {
      state.library ??= [];
      state.libraryError = error.message;
    }
  }
  if (state.view === "library" && state.libraryStyle === styleId && !state.pending) render();
}

async function quarantineLibraryEntry(entryId) {
  const styleId = state.libraryStyle;
  if (!state.libraryIssues.some(issue => issue.id === entryId)) return;
  if (!window.confirm("Move this damaged metadata entry out of the active library and retain a recovery copy?")) return;
  const finish = beginRequest("Moving damaged library metadata aside…");
  let recovery;
  try {
    recovery = await post("/api/library/quarantine", { styleId, entryId });
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  if (recovery) {
    await Promise.all([refreshLibrary(), refreshStyles()]);
    setMessage(`Damaged metadata moved aside. Recovery copy: ${recovery.retainedPath}`, "good");
  }
}

async function pinArtwork(data) {
  const projectId = data.get("project");
  const alias = data.get("alias").trim();
  const libraryId = state.libraryId;
  const asset = state.library.find(item => item.id === libraryId);
  const version = state.libraryVersion ?? asset?.latestVersion;
  const selected = asset?.versions.find(item => item.version === version);
  const path = `/api/projects/${encodeURIComponent(projectId)}`;
  const previousProject = state.project?.id;
  await perform(async () => {
    const project = await api(path);
    if (projectStyleId(project) !== state.libraryStyle) {
      throw new Error("Choose a project that uses this style library.");
    }
    const existing = project.assets.find(item => item.id === alias);
    if (existing && !(existing.issue && existing.hash !== selected?.hash)
        && (existing.libraryId !== libraryId || existing.libraryVersion !== version)) {
      throw new Error(`This project already uses “${alias}”. Choose a unique artwork ID.`);
    }
    return post(`${path}/library/pin`, { revision: project.revision, libraryId, version, alias });
  }, {
    busy: "Pinning artwork to the project…",
    success: `Pinned version ${version} as “${alias}”.`,
    stage: project => project.id === previousProject ? state.stage : nextStage(project),
  });
  const status = content.querySelector("#library-pin-status");
  if (status && !message.hidden) {
    status.textContent = message.textContent;
    status.className = `notice inline ${message.classList.contains("error") ? "error" : "good"}`;
    status.hidden = false;
    setMessage();
  }
}

function readLayer(section) {
  const read = key => section.querySelector(`[data-layer-field="${key}"]`)?.value ?? "";
  const number = key => Number(read(key));
  const kind = read("kind");
  const effect = read("enter.effect");
  const items = ["bar-chart", "flow"].includes(kind) ? read("items").split("\n").map(row => row.trim()).filter(Boolean).map(row => {
    if (kind === "flow") return { label: row, value: 0 };
    const divider = row.lastIndexOf("|");
    const rawValue = divider === -1 ? "" : row.slice(divider + 1).trim();
    return { label: divider === -1 ? row : row.slice(0, divider).trim(), value: rawValue ? Number(rawValue) : null };
  }) : [];
  return {
    id: read("id"), kind,
    box: Object.fromEntries(["x", "y", "w", "h"].map(key => [key, number(`box.${key}`)])),
    text: kind === "art" ? "" : read("text"),
    artId: kind === "art" ? read("artId") : "",
    artBrief: kind === "art" ? read("artBrief") : "",
    items,
    unit: kind === "bar-chart" ? read("unit") : "",
    enter: { line: number("enter.line"), word: number("enter.word"), effect, duration: effect === "show" ? 0 : number("enter.duration") },
    leave: { line: number("leave.line"), word: number("leave.word") },
  };
}

function repaintScene(index, focusSelector = "summary") {
  render();
  const scene = content.querySelector(`[data-scene="${index}"]`);
  if (!scene) return;
  scene.open = true;
  scene.querySelector(focusSelector)?.focus({ preventScroll: true });
}

function editLayer(action, name) {
  const [sceneIndex, layerIndex] = name.split("/").map(Number);
  const layers = updatePlanDraft()[sceneIndex].layers;
  if (action === "add-layer") {
    const used = new Set(layers.map(layer => layer.id));
    let suffix = 1;
    while (used.has(`layer-${suffix}`)) suffix += 1;
    layers.push(newLayer(`layer-${suffix}`));
  } else if (action === "remove-layer") {
    layers.splice(layerIndex, 1);
  } else {
    const shift = action === "layer-back" ? -1 : 1;
    const target = layerIndex + shift;
    if (target >= 0 && target < layers.length) {
      [layers[layerIndex], layers[target]] = [layers[target], layers[layerIndex]];
    }
  }
  repaintScene(sceneIndex);
}

function changeSceneLayout(control) {
  const owner = control.closest("[data-scene]");
  const index = Number(owner.dataset.scene);
  if (owner.dataset.layout === "layered" && control.value !== "layered"
      && !window.confirm("Switch to a preset layout and remove this scene’s custom layers?")) {
    control.value = "layered";
    updatePlanDraft();
    return;
  }
  const scene = updatePlanDraft()[index];
  if (scene.layout === "layered") {
    const layer = newLayer("layer-1");
    if (scene.artId) {
      layer.kind = "art";
      layer.artId = scene.artId;
      layer.artBrief = scene.artBrief;
    } else {
      layer.text = scene.caption || scene.title;
    }
    scene.layers = [layer];
    scene.artId = "";
    scene.artBrief = "";
    scene.motion = "still";
  } else {
    const art = scene.layers.find(layer => layer.kind === "art");
    if (art) {
      scene.artId = art.artId;
      scene.artBrief = art.artBrief;
    }
    scene.layers = [];
  }
  repaintScene(index, '[data-field="layout"]');
}

function updatePlanDraft() {
  const scenes = Array.from(content.querySelectorAll("[data-scene]"), section => {
    const scene = Object.fromEntries(Array.from(section.querySelectorAll("[data-field]"), field => {
      const value = field.type === "number" ? Number(field.value) : field.value;
      return [field.dataset.field, value];
    }));
    return { artId: "", artBrief: "", ...scene, layers: Array.from(section.querySelectorAll("[data-layer]"), readLayer) };
  });
  state.planDraft = { id: state.project.id, hash: state.project.plan.hash, scenes };
  const save = content.querySelector("[data-plan-save]");
  if (save) save.disabled = !scenes.length;
  const approve = content.querySelector('[data-action="approve-plan"]');
  if (approve) approve.disabled = true;
  const dirty = content.querySelector("[data-plan-dirty]");
  if (dirty) dirty.hidden = false;
  const next = content.querySelector("[data-plan-next]");
  if (next) next.hidden = true;
  return scenes;
}

function updateMixDraft(form) {
  const project = state.project;
  const data = new FormData(form);
  const settings = {
    musicMode: data.get("musicMode") ?? "",
    musicGainDb: Number(data.get("musicGainDb") ?? -24),
    effects: Array.from(form.querySelectorAll("[data-effect]"), row => {
      const start = Number(row.querySelector("[data-fx-start]").value);
      row.querySelector("progress").value = start;
      return { id: row.dataset.effect, start, gainDb: Number(row.querySelector("[data-fx-gain]").value) };
    }),
    musicRegions: Array.from(form.querySelectorAll("[data-music-region]"), row => {
      const mute = row.querySelector("[data-region-mute]").checked;
      const level = row.querySelector("[data-region-gain]");
      level.disabled = mute;
      return {
        startLine: Number(row.querySelector("[data-region-start]").value),
        endLine: Number(row.querySelector("[data-region-end]").value),
        gainDb: mute ? null : Number(level.value),
      };
    }),
  };
  const dirty = JSON.stringify(settings) !== JSON.stringify(mixSettings(project.mix));
  state.mixDraft = dirty ? { id: project.id, revision: project.revision, settings } : null;
  const busy = state.pending || Boolean(project.busy);
  const control = selector => form.querySelector(selector);
  control("[data-mix-save]").disabled = busy || !dirty || !settings.musicMode;
  const approve = control('[data-action="approve-mix"]');
  const confirmed = control("#confirm-mix-review");
  if (dirty && confirmed) confirmed.checked = false;
  if (approve) approve.disabled = busy || dirty || !confirmed?.checked;
  const prepare = control('[data-action="prepare-mix"]');
  if (prepare) {
    const choice = project.mix.musicMode === "none" || (project.mix.musicMode === "track" && project.mix.music);
    prepare.disabled = busy || dirty || !choice || state.config.audioAvailable === false;
  }
  const saved = control("[data-mix-saved-review]");
  saved.hidden = dirty;
  if (dirty) saved.querySelector("audio")?.pause();
  control("[data-mix-unsaved]").hidden = !dirty;
  control("[data-mix-next]").hidden = dirty || !mixComplete(project);
  return settings;
}

function editMusicRegion(action, index) {
  const settings = updateMixDraft(content.querySelector('[data-form="mix"]'));
  const regions = settings.musicRegions;
  if (action === "add-music-region") {
    const last = Math.max(0, ...regions.map(region => region.endLine));
    const next = Math.min(state.project.input.lines.length, last + 1);
    regions.push({ startLine: next, endLine: next, gainDb: settings.musicGainDb });
  } else {
    regions.splice(index, 1);
  }
  state.mixDraft = { id: state.project.id, revision: state.project.revision, settings };
  render();
  content.querySelector('[data-action="add-music-region"]')?.focus({ preventScroll: true });
}

function selectProject(id) {
  if (!id) {
    state.project = null;
    state.view = "project";
    render();
    return;
  }
  perform(() => api(`/api/projects/${encodeURIComponent(id)}`), { busy: "Opening project…", stage: nextStage, view: "project" });
}

picker.addEventListener("change", () => selectProject(picker.value));

document.addEventListener("change", event => {
  const control = event.target;
  if (control.id === "new-style-id") {
    if (control.value === newStyleOption) {
      state.returnToKickoff = true;
      selectStyleEditor(emptyStyle());
      openStudioView("styles");
    } else {
      state.newStyleId = control.value;
      content.querySelector("#kickoff-style-details").innerHTML = kickoffStyleSummary(styleById(state.newStyleId));
    }
  } else if (control.id === "new-recording") {
    state.newRecording = control.files[0] ?? null;
    content.querySelector("#kickoff-recording-details").innerHTML = kickoffRecordingSummary();
  } else if (control.id === "library-style") {
    setLibraryStyle(control.value);
    state.libraryAliasPreferred = "";
    state.libraryProject = projectStyleId(state.project) === state.libraryStyle ? state.project.id : "";
    render();
    refreshLibrary();
  } else if (control.id === "library-version") {
    state.libraryVersion = Number(control.value);
    content.querySelector("#library-inspector").innerHTML = libraryInspector();
    content.querySelector("#library-version").focus();
  } else if (control.id === "library-project") {
    state.libraryProject = control.value;
  } else if (control.id === "timing-word-index") {
    const index = Number(control.value);
    const word = state.project.transcript.words[index - 1];
    if (!word) return;
    state.timingWord = index;
    content.querySelector("#timing-word-fields").innerHTML = timingWordFields(word, state.project.audio.duration);
  } else if (control.dataset.field === "layout" && control.closest("[data-scene]").dataset.layout !== control.value) {
    changeSceneLayout(control);
  } else if (["kind", "enter.effect"].includes(control.dataset.layerField)) {
    const index = Number(control.closest("[data-scene]").dataset.scene);
    const layerIndex = [...control.closest("[data-scene]").querySelectorAll("[data-layer]")].indexOf(control.closest("[data-layer]"));
    const layer = updatePlanDraft()[index].layers[layerIndex];
    if (control.dataset.layerField === "kind") layer.items = [];
    if (layer.enter.effect !== "show" && !layer.enter.duration) layer.enter.duration = 0.3;
    repaintScene(index, `[id="${control.id}"]`);
  }
});

document.addEventListener("play", event => {
  if (event.target.tagName !== "AUDIO") return;
  content.querySelectorAll("audio").forEach(audio => {
    if (audio !== event.target) audio.pause();
  });
}, true);

document.addEventListener("input", event => {
  const control = event.target;
  if (control.id === "new-title") state.newTitle = control.value;
  if (control.id === "timing-model-directory") state.timingModelDirectory = control.value;
  if (control.form?.dataset.form === "style") {
    const color = control.dataset.paletteOverride;
    if (color) control.form.querySelector(`#palette-${color}`).disabled = !control.checked;
    captureStyleForm(control.form);
  }
  if (control.id === "library-search") {
    state.libraryQuery = control.value;
    updateLibrarySearch();
  } else if (control.id === "library-alias") {
    state.libraryAlias = control.value;
  }
  if (control.id === "intention") {
    state.inputDraft = {
      id: state.project.id,
      intention: content.querySelector("#intention").value,
    };
  }
  if (control.dataset.field || control.dataset.layerField) updatePlanDraft();
  if (control.hasAttribute("data-mix-setting")) updateMixDraft(control.form);
  if (control.id === "confirm-mix-review") {
    const approve = content.querySelector('[data-action="approve-mix"]');
    if (approve) approve.disabled = !control.checked || state.pending || Boolean(state.project.busy);
  }
});

function importFile(name, data) {
  const project = state.project;
  const file = data.get("file");
  const isArtwork = name === "art";
  const limit = ({
    art: state.config.maxArtBytes,
    timings: state.config.maxTimingBytes,
  })[name] ?? (name === "timings" ? Infinity : state.config.maxAudioBytes);
  if (!file?.size) {
    setMessage("Choose a file to import.", "error");
    return;
  }
  if (file.size > limit) {
    setMessage(`Choose a file smaller than ${Math.floor(limit / 1024 / 1024)} MB.`, "error");
    return;
  }
  const id = data.get("id")?.trim();
  const replacedArt = isArtwork && project.assets.some(asset => asset.id === id);
  const replacedAudio = name === "audio" && project.audio && project.transcript;
  const replacedMusic = name === "mix-music" && project.mix.music;
  const changingMix = name.startsWith("mix-");
  const replacingTimings = name === "timings" && project.transcript;
  const warning = replacedArt ? `Replace the artwork for ${id}? The new image will need approval.`
    : replacedAudio ? "Replace this recording and clear the current transcript?"
    : replacedMusic ? "Replace the project's music track?"
    : replacingTimings ? "Replace the current word timings with this file?"
    : changingMix && state.mixDraft ? "Discard unsaved composition edits and import this audio?"
    : "";
  if (warning && !window.confirm(warning)) return;
  const path = isArtwork ? `/assets/${encodeURIComponent(id)}`
    : ({ audio: "/audio", timings: "/timings", "mix-music": "/mix/music", "mix-effects": "/mix/effects" })[name];
  perform(() => upload(path, file), { busy: "Importing file…", success: "File imported." });
}

async function createProject(title, style) {
  const recording = state.newRecording;
  const limit = state.config.maxAudioBytes;
  if (recording && !recording.size) {
    setMessage("Choose a narration recording with audio.", "error");
    return;
  }
  if (recording?.size > limit) {
    setMessage(`Choose a recording smaller than ${Math.floor(limit / 1024 / 1024)} MB.`, "error");
    return;
  }
  let importError = "";
  let styleChanged = false;
  await perform(async () => {
    let project;
    try {
      project = await post("/api/projects", { title, styleId: style.id, styleRevision: style.revision });
    } catch (error) {
      styleChanged = error.status === 409;
      throw error;
    }
    state.newRecording = null;
    if (recording) {
      try {
        return await upload("/audio", recording, project);
      } catch (error) {
        importError = error.message;
      }
    }
    return project;
  }, { stage: "intake", busy: recording ? "Creating project and importing narration…" : "Creating project…" });
  if (styleChanged) await refreshStyles();
  if (importError) setMessage(`Project created. Try importing the recording again in Intake. ${importError}`, "error");
}

document.addEventListener("submit", event => {
  const form = event.target;
  const name = form.dataset.form;
  if (!name) return;
  event.preventDefault();
  if (state.pending) return;
  const data = new FormData(form);
  if (name === "style") {
    saveStyle(form);
  } else if (name === "style-archive") {
    importStyleArchive(data.get("archive"));
  } else if (name === "settings") {
    const payload = settingsPayload(data);
    if ((payload.openAIKey && payload.clearOpenAIKey) || (payload.anthropicKey && payload.clearAnthropicKey)) {
      setMessage("Choose either a replacement key or the clear option for each provider.", "error");
      return;
    }
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ""; });
    saveSettings(payload, !state.settings.completed);
  } else if (name === "timing-model-location") {
    const directory = data.get("directory")?.trim();
    if (directory) changeTimingModel("location", { directory });
  } else if (name === "library-pin") {
    pinArtwork(data);
  } else if (name === "art-issue") {
    const reason = data.get("reason").trim();
    if (!reason) {
      setMessage("Describe the artwork issue before recording it.", "error");
      return;
    }
    perform(() => mutate(`/assets/${encodeURIComponent(form.dataset.assetId)}/issue`, { kind: data.get("kind"), reason }),
      { success: "Artwork issue recorded." }).then(refreshStyles);
  } else if (name === "proof-result") {
    const { proofGroup, proofId } = form.dataset;
    const passed = form.dataset.passed === "true";
    const reason = String(data.get("reason") ?? "").trim();
    if (form.querySelector("[name=reason]") && !reason) {
      setMessage("Enter a reason before recording this decision.", "error");
      return;
    }
    perform(() => mutate(`/${proofGroup}/checks/${encodeURIComponent(proofId)}/result`, { passed, reason }),
      { success: passed ? "Proof approved." : "Rejection recorded." });
  } else if (name === "new-project") {
    const title = data.get("title").trim();
    if (!title) {
      setMessage("Enter a project name.", "error");
      return;
    }
    const style = styleById(data.get("styleId"));
    if (!style) {
      setMessage("Choose a style for this project.", "error");
      return;
    }
    createProject(title, style);
  } else if (name === "derive-timings") {
    if (!canDeriveTimings()) return;
    if (state.project.transcript && !window.confirm("Replace the current word timings with new timings from the recording?")) return;
    perform(() => mutate("/timings/derive"), {
      busy: "Deriving exact word timings…", success: "Word timings are ready to review.",
    });
  } else if (name === "correct-timing") {
    const index = Number(data.get("index"));
    if (index !== state.timingWord) {
      setMessage("Select the word before saving its correction.", "error");
      return;
    }
    const words = state.project.transcript.words.map(({ text, start, end }, position) =>
      position === index - 1
        ? { text: data.get("text").trim(), start: Number(data.get("start")), end: Number(data.get("end")) }
        : { text, start, end }
    );
    perform(() => mutate("/timings/text", { words }), { success: "Word correction saved. Review the updated timing." });
  } else if (name === "input") {
    perform(() => mutate("/input", {
      script: data.get("script"),
      intention: data.get("intention"),
      confirmTiming: data.has("confirmTiming"),
    }), {
      success: "Recording and transcript locked.",
      stage: "plan",
    });
  } else if (name === "plan") {
    const scenes = updatePlanDraft();
    if (scenes.some(scene => scene.layers.some(layer => layer.items.some(item => !Number.isFinite(item.value))))) {
      setMessage("Enter a numeric value after the bar on each bar chart line.", "error");
      return;
    }
    perform(() => mutate("/plan", { scenes }), { success: "Scene changes saved.", resetPlan: true });
  } else if (name === "mix") {
    const settings = updateMixDraft(form);
    if (settings.musicMode && state.mixDraft) {
      perform(() => mutate("/mix", settings), { success: "Composition saved." });
    }
  } else if (name === "mix-preset") {
    const mix = state.project.mix;
    if ((mix.music || mix.effects.length || state.mixDraft)
        && !window.confirm("Replace the music and all sound effects with the new soundtrack?")) return;
    perform(() => mutate("/mix/preset", { preset: data.get("preset") }), {
      busy: "Creating the soundtrack…",
      success: "Soundtrack ready to review.",
    });
  } else if (["audio", "art", "timings", "mix-music", "mix-effects"].includes(name)) {
    importFile(name, data);
  }
});

const stageActions = {
  "approve-plan": ["/plan/approve", "Plan approved.", project => artworkComplete(project) ? afterArtwork(project) : "art"],
  "prepare-asset-proofs": ["/assets/checks", "Artwork proofs prepared.", undefined, "Preparing artwork proofs…"],
  "prepare-mix": ["/mix/prepare", "Verified WAV ready to review.", undefined, "Preparing and measuring the mix…"],
  "prepare-animation": ["/animation/prepare", "Animation prepared.", undefined, "Preparing the animation…"],
  qc: ["/qc", "Animation checks finished.", undefined, "Running animation checks…"],
  "accept-candidate": ["/candidate/accept", "Candidate accepted.", "export"],
  export: ["/export", ""],
};

async function copyRequest() {
  const request = content.querySelector("#art-request");
  try {
    await navigator.clipboard.writeText(request.value);
    setMessage("Artwork request copied.", "good");
  } catch {
    request.focus();
    request.select();
    setMessage("The request is selected. Press Ctrl+C to copy it.");
  }
}

function editScene(action, index) {
  const scenes = updatePlanDraft();
  if (action === "remove-scene") scenes.splice(index, 1);
  else {
    scenes.push({
      startLine: Math.min((scenes.at(-1)?.endLine || 0) + 1, state.project.input.lines.length),
      endLine: state.project.input.lines.length,
      layout: state.config.layouts[0],
      motion: state.config.motions[0],
      title: "",
      caption: "",
      artId: "",
      artBrief: "",
      layers: [],
    });
  }
  render();
  if (action === "add-scene") {
    const section = content.querySelector(`[data-scene="${scenes.length - 1}"]`);
    section.open = true;
    section.querySelector("summary").focus();
  }
}

document.addEventListener("click", event => {
  const control = event.target.closest("button[data-action], button[data-stage], button[data-view]");
  if (!control || state.pending || control.disabled) return;
  const action = control.dataset.action;
  const name = control.dataset.name;
  if (control.dataset.view) {
    openStudioView(control.dataset.view, name);
  } else if (control.dataset.stage) {
    openStage(control.dataset.stage);
  } else if (action === "new-project") {
    state.project = null;
    state.newTitle = "";
    state.newStyleId = "";
    state.newRecording = null;
    state.returnToKickoff = false;
    openStage("intake", false);
    content.querySelector("#new-title")?.focus();
  } else if (action === "clear-new-recording") {
    state.newRecording = null;
    const input = content.querySelector("#new-recording");
    input.value = "";
    content.querySelector("#kickoff-recording-details").innerHTML = kickoffRecordingSummary();
    input.focus();
  } else if (action === "new-style") {
    selectStyleEditor(emptyStyle());
    render();
    content.querySelector("#style-name")?.focus();
  } else if (action === "select-style") {
    const selected = styleById(name);
    if (!selected) return;
    selectStyleEditor(selected);
    setMessage();
    render();
  } else if (action === "return-to-kickoff") {
    returnToNewProject(name);
  } else if (action === "export-style-archive") {
    exportStyleArchive(name);
  } else if (action === "open-library-recovery") {
    openStudioView("library", "", name);
  } else if (action === "refresh-library") {
    refreshLibrary();
  } else if (action === "refresh-styles") {
    refreshStyles().then(() => {
      if (state.view === "library" && !state.libraryStyle && state.styles.length) {
        setLibraryStyle(state.styles[0].id);
        refreshLibrary();
      }
    });
  } else if (action === "quarantine-library-entry") {
    quarantineLibraryEntry(name);
  } else if (action === "install-timing-model") {
    changeTimingModel("install", {});
  } else if (action === "clear-timing-model-location") {
    changeTimingModel("location", { directory: "" });
  } else if (action === "refresh-timing-model") {
    refreshTimingModel();
  } else if (action === "rerender-export") {
    perform(() => mutate("/export", { rerender: true }), { busy: "Starting a fresh render…" });
  } else if (action === "check-delivery-files") {
    perform(() => api(projectPath()), { busy: "Checking the delivery files…" });
  } else if (action === "import-style-guide") {
    importStyleGuide();
  } else if (action === "upload-style-references") {
    addStyleReferences();
  } else if (action === "remove-style-reference") {
    const editor = captureStyleForm(content.querySelector('[data-form="style"]'));
    editor.referenceImages = editor.referenceImages.filter(image => image.sha256 !== name);
    render();
  } else if (action === "draft-style-guide") {
    draftStyleGuide();
  } else if (action === "adopt-style-draft" || action === "discard-style-draft") {
    const editor = captureStyleForm(content.querySelector('[data-form="style"]'));
    if (action === "adopt-style-draft") editor.guide = state.styleProposal;
    state.styleProposal = "";
    setMessage();
    render();
    content.querySelector("#style-guide")?.focus();
  } else if (action === "demo") {
    perform(() => post("/api/demo", {}), { busy: "Creating example…", stage: nextStage });
  } else if (action === "refresh-settings") {
    refreshSettings();
  } else if (action === "install-media-tools") {
    installMediaTools(control);
  } else if (action === "quit-bacchus") {
    quitBacchus();
  } else if (action === "skip-setup") {
    saveSettings({ completed: true }, true);
  } else if (action === "select-library-art") {
    selectLibraryAsset(state.library.find(asset => asset.id === name));
    content.querySelector("#library-inspector").innerHTML = libraryInspector();
    content.querySelectorAll('[data-action="select-library-art"]').forEach(entry => {
      entry.setAttribute("aria-pressed", String(entry.dataset.name === name));
    });
  } else if (action === "unlock-input") {
    if (window.confirm("Unlocking clears the current plan, quality checks, and export from this project. Unlock the intake?")) {
      const { id, input } = state.project;
      state.inputDraft = { id, intention: input.intention };
      perform(() => mutate("/input/unlock"), { success: "Intake unlocked.", resetPlan: true });
    }
  } else if (action === "seek-timing") {
    const source = content.querySelector("#narration-source");
    if (source) {
      source.pause();
      source.currentTime = Number(name);
      source.focus({ preventScroll: true });
      source.scrollIntoView({ block: "nearest" });
    }
  } else if (action === "remove-audio") {
    if (window.confirm("Remove this recording and clear the current transcript?")) {
      perform(() => mutate("/audio/remove"), { success: "Recording removed." });
    }
  } else if (action === "generate-plan" || action === "starter-plan") {
    if (state.project.plan && !window.confirm("Replacing the current plan clears its approval and candidate. Replace the plan?")) return;
    const ai = action === "generate-plan";
    perform(() => mutate(ai ? "/plan/generate" : "/plan/starter"), {
      busy: ai ? "Generating the scene plan…" : "Creating a starter plan…",
      success: "Scene draft ready to review.",
      resetPlan: true,
    });
  } else if (action === "add-scene" || action === "remove-scene") {
    editScene(action, Number(name));
  } else if (["add-layer", "remove-layer", "layer-back", "layer-forward"].includes(action)) {
    editLayer(action, name);
  } else if (action === "add-music-region" || action === "remove-music-region") {
    editMusicRegion(action, Number(name));
  } else if (action === "approve-mix") {
    if (content.querySelector("#confirm-mix-review")?.checked) {
      perform(() => mutate("/mix/approve", { reviewed: true }), { success: "Verified composition approved.", stage: "review" });
    }
  } else if (action === "select-art") {
    content.querySelector("#asset-id").value = name;
    content.querySelector("#art-file").focus();
  } else if (action === "approve-art") {
    perform(() => mutate(`/assets/${encodeURIComponent(name)}/approve`), {
      success: "Source approved for planned-use review.",
    });
  } else if (action === "publish-art") {
    perform(() => mutate(`/assets/${encodeURIComponent(name)}/publish`), { success: "Artwork published to the library." })
      .then(refreshStyles);
  } else if (action === "generate-art") {
    perform(() => mutate(`/assets/${encodeURIComponent(name)}/generate`), {
      busy: `Generating ${name} with OpenAI…`,
      success: "Image generated and ready to approve.",
    });
  } else if (action === "remove-effect") {
    if (window.confirm("Remove this sound effect from the composition?")) {
      perform(() => mutate(`/mix/effects/${encodeURIComponent(name)}/remove`), { success: "Sound effect removed." });
    }
  } else if (action === "copy-request") {
    copyRequest();
  } else if (action === "select-asset-proof" || action === "select-animation-proof" || action === "full-animation-preview") {
    const artwork = action === "select-asset-proof";
    if (artwork) state.assetProofId = name;
    else state.animationReviewId = action === "full-animation-preview" ? "full" : name;
    setMessage();
    render();
    const inspector = content.querySelector(artwork ? "#asset-proof-detail" : "#animation-player");
    inspector?.focus({ preventScroll: true });
    inspector?.scrollIntoView({ block: "nearest" });
  } else if (action === "advise-assets" || action === "advise-animation") {
    const group = action === "advise-assets" ? "assets" : "animation";
    perform(() => mutate(`/${group}/checks/${encodeURIComponent(name)}/advise`), {
      busy: "Claude is reviewing this proof…",
      success: "Claude’s advisory findings are ready.",
    });
  } else if (stageActions[action]) {
    const [path, success, stage, busy] = stageActions[action];
    perform(() => mutate(path), { success, stage, busy: action === "export" ? "Starting render…" : busy });
  }
});

document.addEventListener("invalid", event => {
  const scene = event.target.closest(".scene-editor");
  if (scene) scene.open = true;
}, true);

async function initialize() {
  picker.disabled = true;
  newProject.disabled = true;
  render();
  try {
    const [config, listing, settings] = await Promise.all([
      api("/api/config"), api("/api/projects"), api("/api/settings"),
    ]);
    state.config = config;
    state.projects = listing.projects;
    state.settings = settings;
    if (!settings.completed) state.view = "settings";
    setConnection("online");
    let last = "";
    try { last = localStorage.getItem("bacchus-project") || ""; } catch { /* Browser storage is optional. */ }
    if (state.projects.some(item => item.id === last)) {
      try {
        state.project = await api(`/api/projects/${encodeURIComponent(last)}`);
        state.stage = nextStage(state.project);
      } catch (error) {
        setMessage(`The last project could not be opened. ${error.message}`, "error");
      }
    }
    render();
    refreshStyles();
    refreshTimingModel();
  } catch (error) {
    setConnection("offline");
    content.innerHTML = heading("Studio unavailable", "Open Bacchus again to reconnect.");
    setMessage(error.message, "error");
  } finally {
    picker.disabled = !state.config;
    newProject.disabled = !state.config;
  }
}

connectionStatus.setAttribute("role", "status");
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.connection !== "connecting") probeConnection();
});
initialize().finally(scheduleConnectionProbe);
