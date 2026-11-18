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

const state = {
  config: null,
  closed: false,
  view: "project",
  settings: null,
  library: null,
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
  inputDraft: null,
  planDraft: null,
  mixDraft: null,
  previewPosition: null,
  poll: null,
  stopPreview: null,
};

const content = document.querySelector("#stage-content");
const message = document.querySelector("#message");
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

function projectPath(suffix = "") {
  return `/api/projects/${encodeURIComponent(state.project.id)}${suffix}`;
}

async function api(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(data?.error || `The studio returned HTTP ${response.status}.`);
    error.status = response.status;
    throw error;
  }
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

function upload(suffix, file) {
  const query = new URLSearchParams({ revision: state.project.revision, name: file.name });
  return api(`${projectPath(suffix)}?${query}`, {
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
  const labels = { online: "Local studio connected", offline: "Studio unavailable", closed: "Studio closed" };
  document.querySelector(".sidebar").dataset.connection = status;
  document.querySelector("#connection-status").textContent = labels[status];
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

async function openStudioView(view, alias = "") {
  state.view = view;
  setMessage();
  if (view === "library") {
    state.libraryAliasPreferred = alias;
    state.libraryAlias = alias || libraryAlias(state.library?.find(asset => asset.id === state.libraryId));
    state.libraryProject = state.project?.id ?? "";
  }
  render();
  content.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  if (view === "library") await refreshLibrary();
  if (view === "settings" && !state.settings) await refreshSettings();
}

function pendingArt(project) {
  const approved = new Set(project.assets.filter(asset => asset.approved).map(asset => asset.id));
  const required = new Set(project.plan?.scenes.map(scene => scene.artId).filter(Boolean));
  return [...required].filter(id => !approved.has(id));
}

function afterArtwork(project) {
  return project.mix?.approved ? "review" : "composition";
}

function afterArtworkButton(project) {
  const stage = afterArtwork(project);
  return goTo(stage, stage === "review" ? "Open preview" : "Open composition");
}

function stageStates(project) {
  return {
    intake: Boolean(project?.input),
    plan: Boolean(project?.plan?.approved),
    art: Boolean(project?.plan?.approved && !pendingArt(project).length),
    composition: Boolean(project?.mix?.approved),
    review: Boolean(project?.accepted),
    export: project?.export?.status === "complete",
  };
}

function nextStage(project) {
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
  const summary = { id: project.id, title: project.title, updatedAt: project.updatedAt };
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
  const toolName = state.view === "library" ? "Art library" : state.view === "settings" ?
    (state.settings?.completed ? "Settings" : "Setup") : null;
  workspaceName.textContent = toolName ? "Studio" : "Projects";
  projectName.textContent = toolName ?? project?.title ?? "New project";
  const style = state.config?.styles?.find(option => option.id === project?.style);
  packName.textContent = state.config?.pack ? `${style?.name ?? state.config.pack.name} · v${state.config.pack.version}` : "";
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
  const transcriptionEnabled = Boolean(settings.useOpenAITranscription);
  const imagesEnabled = Boolean(settings.useOpenAIImages);
  return [
    readiness("Audio tools", settings.audioAvailable, "Install FFmpeg and ffprobe"),
    readiness("Video export", settings.renderAvailable, "Install FFmpeg and ffprobe"),
    readiness(`Scene planning · ${planner}`, state.config.plannerAvailable, `Needs ${planner} key`, "Configured"),
    readiness("OpenAI transcription", state.config.transcriberAvailable, transcriptionEnabled ? "Needs OpenAI key" : "Off",
      "Configured", transcriptionEnabled ? "warm" : ""),
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

function settingsModel(id, label, value) {
  return `<div class="field"><label for="${id}">${label}</label>
    <input id="${id}" name="${id}" value="${escapeHtml(value)}" maxlength="100" spellcheck="false">
  </div>`;
}

const workflowOverview = [
  "Import a recording and verify its transcript.",
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
    ${heading(setup ? "Set up Bacchus" : "Settings", "Check the local media tools and choose the providers you want to use.")}
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
            <label class="music-option"><input type="checkbox" name="useOpenAITranscription"${settings.useOpenAITranscription === true ? " checked" : ""}> Transcribe recordings with OpenAI</label>
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
              ${settingsModel("transcribeModel", "Transcription model", settings.transcribeModel)}
              ${settingsModel("imageModel", "Image model", settings.imageModel)}
            </div>
          </details>
        </fieldset>
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
              <code>${escapeHtml(settings.libraryPath)}</code></div>
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
  if (!state.library.length) return '<p class="hint actions">Approved artwork appears here as you make projects.</p>';
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

function libraryInspector() {
  const asset = state.library?.find(item => item.id === state.libraryId);
  if (!asset) return "";
  const version = asset.versions.find(item => item.version === state.libraryVersion) ?? asset.versions.at(-1);
  if (!version) return "";
  const image = localMedia(version.url);
  const name = version.name || asset.name || asset.id;
  const created = libraryDate(version.createdAt);
  const target = state.libraryProject || state.project?.id || state.projects[0]?.id;
  return `<section class="panel stack">
    <h2>${escapeHtml(name)}</h2>
    <div class="field">
      <label for="library-version">Version</label>
      <select id="library-version">
        ${[...asset.versions].sort((left, right) => right.version - left.version).map(item => `<option value="${escapeHtml(item.version)}"${version.version === item.version ? " selected" : ""}>Version ${escapeHtml(item.version)}${item.version === asset.latestVersion ? " (latest)" : ""}</option>`).join("")}
      </select>
    </div>
    ${image ? `<figure class="library-preview"><img src="${image}" alt="${escapeHtml(name)}"></figure>` : ""}
    ${version.brief ? `<p class="hint">${escapeHtml(version.brief)}</p>` : ""}
    <dl class="receipt">
      ${version.style ? `<dt>Style</dt><dd>${escapeHtml(version.style)}</dd>` : ""}
      ${version.source ? `<dt>Source</dt><dd>${escapeHtml(version.source)}</dd>` : ""}
      ${created ? `<dt>Saved</dt><dd>${escapeHtml(created)}</dd>` : ""}
      <dt>Fingerprint</dt><dd>${shortHash(version.hash)}</dd>
    </dl>
    ${state.projects.length ? `<form class="stack" data-form="library-pin">
      <div class="field"><label for="library-project">Project</label>
        <select id="library-project" name="project">
          ${state.projects.map(project => `<option value="${escapeHtml(project.id)}"${target === project.id ? " selected" : ""}>${escapeHtml(project.title)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label for="library-alias">Artwork ID in project</label>
        <input id="library-alias" name="alias" value="${escapeHtml(state.libraryAlias)}" pattern="[a-z0-9\\-]+" maxlength="64" required>
        <p class="hint">Enter the ID you’ll reference in the scene plan. Use lowercase letters, digits, and hyphens.</p>
      </div>
      <div class="cluster"><button class="button primary">Use in project</button>
        ${state.project ? '<button class="button quiet" type="button" data-view="project">Back to project</button>' : ""}</div>
      <p id="library-pin-status" class="notice inline" role="status" aria-live="polite" hidden></p>
    </form>` : '<div><button class="button primary" type="button" data-action="new-project">Create a project to use this image</button></div>'}
  </section>`;
}

function libraryView() {
  const matches = libraryMatches();
  if (!matches.some(item => item.id === state.libraryId)) selectLibraryAsset(matches[0]);
  return `
    ${heading("Art library", "Find approved artwork and pin a specific version to a project.")}
    <div class="columns library-columns">
      <section class="panel">
        <div class="field"><label for="library-search">Search artwork</label>
          <input id="library-search" type="search" value="${escapeHtml(state.libraryQuery)}" placeholder="Search by name, brief, or style">
        </div>
        <div id="library-results">${libraryRows(matches)}</div>
      </section>
      <div id="library-inspector">${libraryInspector()}</div>
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

function startView() {
  return `
    ${heading("New project", "Turn recorded narration into a finished animation.")}
    <div class="columns">
      <form class="panel" data-form="new-project">
        <div class="stack">
          <div class="field">
            <label for="new-title">Project name</label>
            <input id="new-title" name="title" maxlength="120" placeholder="e.g. The shape of a good question" value="${escapeHtml(state.newTitle)}" required>
          </div>
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

function timecode(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  return `${minutes}:${String(value % 60).padStart(2, "0")}`;
}

function shortHash(hash) {
  return `<code title="${escapeHtml(hash)}">${escapeHtml(String(hash ?? "").slice(0, 12))}</code>`;
}

function timingImport() {
  return `<details class="timing-import">
    <summary>Import word timings</summary>
    <form class="stack" data-form="timings">
      <div class="field">
        <label for="timing-file">Word timing file (JSON)</label>
        <input id="timing-file" type="file" name="file" accept=".json,application/json" required>
        <p class="hint">Use a timing file matched to this recording.</p>
      </div>
      <div><button class="button">Import timing file</button></div>
    </form>
  </details>`;
}

function intakeView(project) {
  const input = project.input;
  const audio = project.audio;
  const draft = state.inputDraft;
  const busy = Boolean(project.busy);
  const hasSource = Boolean(audio || input || project.requiresAudio === false);
  const attempts = Math.max(0, (state.config.maxTranscriptionCalls ?? 0) - (project.transcriptionCalls ?? 0));
  const styles = state.config.styles ?? [{ id: "warm-editorial", name: "Warm editorial" }];
  const style = input?.style ?? draft?.style ?? project.style ?? styles[0].id;
  const sourceTimings = project.transcript?.timing === "source-cues";
  return `
    ${heading("Intake", "Import the narration recording, then review its transcript and add creative direction.")}
    <div class="columns">
      <div class="stack">
      <section class="panel">
        <div class="panel-header">
          <h2>Narration recording</h2>
          ${input ? badge("Locked", "good") : audio ? badge("Imported", "good") :
            project.requiresAudio === false ? badge("Optional for this project") : badge("Required", "warm")}
        </div>
        ${audio ? `<p class="audio-name">${escapeHtml(audio.name)} · ${timecode(audio.duration)}</p>
          <audio controls preload="none" aria-label="Listen to narration" src="${localMedia(audio.url)}"></audio>` : input ?
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
          <h2>Transcript and brief</h2>
          ${input ? badge("Locked", "good") : ""}
        </div>
        ${!input ? `<div class="transcription-control">
          ${audio && state.config.transcriberAvailable
            ? `<div class="cluster">${button(project.transcript ? "Transcribe again" : "Transcribe recording", "transcribe-audio", { disabled: busy || !attempts })}
                <span class="hint">${attempts ? "Sends the recording to OpenAI for transcription." : "This project has used its transcription requests."}</span></div>`
            : audio ? providerNotice(state.settings?.useOpenAITranscription
              ? "Paste a transcript, import word timings, or add an OpenAI key to draft it here."
              : "Paste a transcript or import recording-matched word timings.", "Transcription settings") :
            '<p class="hint">Import a recording to transcribe it, or paste your transcript below.</p>'}
          ${project.busy === "transcribing" ? '<p class="hint">Transcription is in progress.</p>' :
          sourceTimings ? '<p class="hint"><strong>Recording-matched word timings.</strong> Editing the cue text switches to estimated timing.</p>' :
          project.transcript ? `<p class="hint">Draft from ${escapeHtml(project.transcript.model || "OpenAI")}. Review it against the recording.</p>` : ""}
          ${audio && !busy ? timingImport() : ""}
        </div>` : ""}
        <form data-form="input" class="stack">
          <div class="field">
            <label for="script">Narration transcript</label>
            <textarea id="script" name="script" rows="12" required ${input ? "readonly" : ""}
              placeholder="Review or paste the spoken text, one cue per line.">${escapeHtml(input?.script ?? draft?.script ?? project.transcript?.script)}</textarea>
            <p class="hint">Each nonempty line becomes a narration cue. Up to ${escapeHtml(state.config.maxLines)} cues.</p>
          </div>
          <div class="field">
            <label for="style">Visual style</label>
            <select id="style" name="style"${input ? " disabled" : ""}>
              ${styles.map(option => `<option value="${escapeHtml(option.id)}"${option.id === style ? " selected" : ""}>${escapeHtml(option.name)}</option>`).join("")}
            </select>
          </div>
          <div class="field">
            <label for="intention">Creative brief <span class="muted">(optional)</span></label>
            <textarea id="intention" name="intention" rows="3" maxlength="2000" ${input ? "readonly" : ""}
              placeholder="Describe the audience, visual preferences, tone, and what the animation should communicate.">${escapeHtml(input?.intention ?? draft?.intention)}</textarea>
          </div>
          ${input ? `
            <div class="cluster">
              <button class="button primary" type="button" data-stage="plan">Open scene plan</button>
              ${button("Unlock intake", "unlock-input", { style: "quiet", disabled: busy })}
            </div>` : `<div><button class="button primary"${busy ? " disabled" : ""}>Lock recording and transcript</button></div>`}
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
        </section>` : ""}
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

function sceneEditor(scene, index, project, expanded) {
  const prefix = `scene-${index}`;
  const lines = project.input.lines.length;
  const title = scene.title || `Scene ${index + 1}`;
  return `
    <details class="scene-editor"${expanded ? " open" : ""} data-scene="${index}">
      <summary>${escapeHtml(title)}<span class="scene-meta">Cues ${escapeHtml(scene.startLine)}–${escapeHtml(scene.endLine)} · ${escapeHtml(scene.layout)}</span></summary>
      <div class="stack">
        <div class="stack scene-fields">
          <div class="field"><label for="${prefix}-start">Start cue</label><input id="${prefix}-start" data-field="startLine" type="number" min="1" max="${lines}" value="${escapeHtml(scene.startLine)}" required></div>
          <div class="field"><label for="${prefix}-end">End cue</label><input id="${prefix}-end" data-field="endLine" type="number" min="1" max="${lines}" value="${escapeHtml(scene.endLine)}" required></div>
          <div class="field"><label for="${prefix}-layout">Layout</label><select id="${prefix}-layout" data-field="layout">${options(state.config.layouts, scene.layout)}</select></div>
          <div class="field"><label for="${prefix}-motion">Motion</label><select id="${prefix}-motion" data-field="motion">${options(state.config.motions, scene.motion)}</select></div>
        </div>
        <div class="field"><label for="${prefix}-title">Title</label><input id="${prefix}-title" data-field="title" value="${escapeHtml(scene.title)}"></div>
        <div class="field"><label for="${prefix}-caption">On-screen caption</label><textarea id="${prefix}-caption" data-field="caption" rows="2">${escapeHtml(scene.caption)}</textarea></div>
        <div class="field"><label for="${prefix}-art">Artwork ID</label><input id="${prefix}-art" data-field="artId" list="project-art-ids" pattern="[a-z0-9\\-]*" value="${escapeHtml(scene.artId)}" placeholder="e.g. opening-collage"></div>
        <div class="field"><label for="${prefix}-brief">Artwork brief</label><textarea id="${prefix}-brief" data-field="artBrief" rows="2">${escapeHtml(scene.artBrief)}</textarea></div>
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
  const next = pendingArt(project).length ? goTo("art", "Open artwork") : afterArtworkButton(project);
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

function artworkView(project) {
  if (!project.plan?.approved) return prerequisite("Artwork", "Approve a scene plan to see its complete artwork request.", "plan", "Open scene plan");
  const required = new Map(project.plan.scenes.filter(scene => scene.artId).map(scene => [scene.artId, scene.artBrief]));
  const assets = new Map(project.assets.map(asset => [asset.id, asset]));
  const entries = [...new Set([...required.keys(), ...assets.keys()])];
  const pending = pendingArt(project).length;
  const callsRemaining = Math.max(0, (state.config.maxArtGenerations ?? 0) - (project.artGenerations ?? 0));
  return `
    ${heading("Artwork", "Import the artwork your approved scene plan calls for and approve each image for use.")}
    <div class="columns">
      <section class="panel">
        <div class="panel-header">
          <h2>Project artwork</h2>
          ${badge(pending ? `${pending} pending` : "Ready", pending ? "warm" : "good")}
        </div>
        ${entries.length ? `<ul class="art-list">${entries.map(id => {
          const asset = assets.get(id);
          const url = localMedia(asset?.url);
          return `<li class="art-item">
            <div>
              <h3>${escapeHtml(id)}</h3>
              ${required.get(id) ? `<p class="art-copy">${escapeHtml(required.get(id))}</p>` : ""}
              ${asset?.name ? `<p class="art-copy">${escapeHtml(asset.name)}</p>` : ""}
              ${asset?.libraryVersion ? `<p class="art-copy">Library version ${escapeHtml(asset.libraryVersion)}</p>` : ""}
              ${url ? `<img src="${url}" alt="${escapeHtml(id)}" loading="lazy">` : ""}
            </div>
            <div class="stack">
              ${badge(asset?.approved ? "Approved" : asset ? "Awaiting approval" : "Missing", asset?.approved ? "good" : "warm")}
              ${asset && !asset.approved ? button("Approve artwork", "approve-art", { primary: true, name: id, disabled: Boolean(project.busy) }) : ""}
              ${asset?.approved && !asset.libraryId ? button("Save to library", "save-art-library", { name: id, disabled: Boolean(project.busy) }) : ""}
              ${!asset && state.config.imageAvailable ? button("Generate image", "generate-art", {
                name: id,
                disabled: Boolean(project.busy) || !callsRemaining,
                style: "small",
              }) : ""}
              ${button(asset ? "Replace file" : "Select for upload", "select-art", { name: id, style: "quiet small" })}
              ${asset ? "" : libraryShortcut("Use library artwork", id)}
            </div>
          </li>`;
        }).join("")}</ul>` : '<p class="hint">This plan uses the built-in text layouts.</p>'}
        <div class="cluster actions">
          ${libraryShortcut()}
          <span class="hint">Approving artwork saves it to the shared art library.</span>
        </div>
        ${project.missing.length ? state.config.imageAvailable
          ? `<p class="hint actions">${callsRemaining ? "Image generation sends one art brief at a time to OpenAI." : "This project has used its image generation requests."}</p>`
          : providerNotice(state.settings?.useOpenAIImages
            ? "Import your images or add an OpenAI key to generate them in Bacchus."
            : "Copy the combined request into your image tool, then import the files here.", "Image settings") : ""}
        ${!pending ? afterArtworkButton(project) : ""}
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
    </div>`;
}

function mixSettings(mix) {
  return {
    musicMode: mix.musicMode ?? "",
    musicGainDb: mix.music?.gainDb ?? -24,
    effects: mix.effects.map(({ id, start, gainDb }) => ({ id, start, gainDb })),
  };
}

function mixPreviewUrl(project) {
  return `${projectPath("/mix/preview")}?revision=${encodeURIComponent(project.revision)}`;
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

function compositionView(project) {
  if (!project.input) return prerequisite("Composition", "Lock the narration recording and transcript before composing the soundtrack.", "intake", "Open intake");
  if (!project.plan?.approved) return prerequisite("Composition", "Approve the scene plan before composing the soundtrack.", "plan", "Open scene plan");
  if (pendingArt(project).length) return prerequisite("Composition", "Approve the required artwork before composing the soundtrack.", "art", "Open artwork");
  const mix = project.mix ?? { musicMode: null, music: null, effects: [], approved: false };
  const values = state.mixDraft?.settings ?? mixSettings(mix);
  const dirty = Boolean(state.mixDraft);
  const busy = Boolean(project.busy);
  const audioDisabled = busy || state.config.audioAvailable === false;
  const effects = mix.effects;
  const duration = project.input.duration;
  const hasDecision = mix.musicMode === "none" || (mix.musicMode === "track" && mix.music);
  const canListen = hasDecision && hasAudibleTracks(project);
  const full = effects.length >= (state.config.maxEffects ?? 12);
  return `
    ${heading("Composition", "Choose the music, time any sound effects, and listen to the mix before approving it.")}
    <div class="columns">
      <form class="stack" data-form="mix">
        <section class="panel">
          <div class="panel-header">
            <h2>Music</h2>
            ${mix.approved ? badge("Approved", "good") : ""}
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
          </div>
        </section>
        <section class="panel">
          <div class="panel-header"><h2>Sound effects</h2>${badge(`${effects.length} / ${state.config.maxEffects ?? 12} cues`)}</div>
          ${effects.length ? `<ul class="mix-items">${effects.map((effect, index) =>
            effectEditor(effect, values.effects[index], duration, index, busy)).join("")}</ul>` : '<p class="hint">No sound effects added.</p>'}
        </section>
        <section class="panel">
          <div class="panel-header"><h2>Review mix</h2></div>
          <div data-mix-listen${dirty || !canListen ? " hidden" : ""}>${canListen ? previewAudio(mixPreviewUrl(project), "Listen to the complete mix") : ""}</div>
          <p class="hint" data-mix-unsaved${dirty ? "" : " hidden"}>Save these settings to hear the updated mix.</p>
          ${!hasDecision ? '<p class="hint" data-mix-no-decision>Make an explicit music choice to review the mix.</p>' : ""}
          ${hasDecision && !canListen ? '<p class="hint" data-mix-silent>This cut is silent.</p>' : ""}
          <div class="cluster">
            <button class="button" type="submit" data-mix-save${busy || !dirty || !values.musicMode ? " disabled" : ""}>Save composition</button>
            ${!mix.approved ? button("Approve composition", "approve-mix", { primary: true, disabled: busy || !hasDecision || dirty }) : ""}
          </div>
          <div data-mix-next${dirty || !mix.approved ? " hidden" : ""}>${goTo("review", "Open preview")}</div>
        </section>
      </form>
      <div class="stack">
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
  if (!qc) return '<p class="hint">Run the checks against the approved plan and its assets to create a review candidate.</p>';
  if (!qc.findings.length) return '<p class="hint">No findings from the automated checks.</p>';
  return `<ul class="read-list">${qc.findings.map(finding =>
    `<li><strong class="finding-${escapeHtml(finding.level)}">${escapeHtml(finding.level)}</strong> ${escapeHtml(finding.message)}</li>`
  ).join("")}</ul>`;
}

function sceneStart(project, scene) {
  return project.input.lines[scene.startLine - 1].start;
}

function reviewView(project) {
  if (!project.plan?.approved) return prerequisite("Preview & QC", "Approve a scene plan to open the preview.", "plan", "Open scene plan");
  if (pendingArt(project).length) return prerequisite("Preview & QC", "Import and approve the required artwork to open the preview.", "art", "Open artwork");
  const duration = project.input.duration;
  const qc = project.qc;
  const mixApproved = Boolean(project.mix?.approved);
  const hasSound = hasAudibleTracks(project);
  return `
    ${heading("Preview & QC", "Scrub through the animation and review the checks before accepting a candidate.")}
    <div class="columns">
      <section class="panel">
        <div class="preview-frame">
          <img id="preview-image" alt="Current animation frame">
        </div>
        <div class="player-controls">
          <button class="button small" type="button" id="play-preview" aria-pressed="false">Play silent preview</button>
          <input id="preview-time" type="range" min="0" max="${escapeHtml(duration)}" step=".01" value="0" aria-label="Preview position">
          <span class="timecode"><span id="current-time">0:00</span> / ${timecode(duration)}</span>
        </div>
        ${mixApproved && hasSound ? `<div class="narration-preview">
          <audio id="preview-narration" preload="none" src="${localMedia(mixPreviewUrl(project))}"></audio>
          <button class="button small quiet" type="button" id="play-narration" aria-pressed="false">Play with mix</button>
        </div>` : ""}
        <p id="preview-feedback" class="hint player-feedback" role="status" hidden></p>
        <ul class="shot-list" aria-label="Jump to scene">
          ${project.plan.scenes.map((scene, index) => `<li><button class="button small" type="button"
            data-preview-time="${escapeHtml(sceneStart(project, scene))}"
            title="${escapeHtml(scene.title || `Scene ${index + 1}`)}"
            aria-label="Scene ${index + 1}${scene.title ? `, ${escapeHtml(scene.title)}` : ""}">${index + 1}</button></li>`).join("")}
        </ul>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Quality checks</h2>
          ${project.accepted ? badge("Candidate accepted", "good") :
            qc?.status === "passed" ? badge("Passed", "good") : qc ? badge("Needs review", "warm") : ""}
        </div>
        ${mixApproved ? showFindings(qc) : '<p class="hint">Approve the audio composition to run quality checks. The silent preview is available now.</p>'}
        ${qc?.candidateHash ? `<dl class="receipt actions"><dt>Candidate</dt><dd>${shortHash(qc.candidateHash)}</dd></dl>` : ""}
        <div class="actions cluster">
          ${project.accepted ? '<button type="button" class="button primary" data-stage="export">Open export</button>' : ""}
          ${!project.accepted && qc?.status === "passed" ? button("Accept candidate", "accept-candidate", {
            primary: true,
            disabled: Boolean(project.busy),
          }) : ""}
          ${mixApproved && !project.accepted ? button(qc ? "Run checks again" : "Run quality checks", "qc", {
            primary: !qc,
            disabled: Boolean(project.busy),
          }) : ""}
          ${!mixApproved ? '<button class="button primary" type="button" data-stage="composition">Open composition</button>' : ""}
        </div>
      </section>
    </div>`;
}

function exportLink(url, label, primary = false) {
  const href = localMedia(url);
  return href ? `<a class="button${primary ? " primary" : ""}" href="${href}" download>${label}</a>` : "";
}

function exportView(project) {
  if (!project.accepted) return prerequisite("Export", "Run QC and accept the candidate before exporting a video.", "review", "Open preview & QC");
  const output = project.export;
  const status = output?.status;
  const running = status === "running" || project.busy === "rendering";
  return `
    ${heading("Export", "Render the accepted candidate and download the finished files.")}
    <section class="panel slim">
      <div class="panel-header">
        <h2>Video export</h2>
        ${status === "complete" ? badge("Complete", "good") : running ? badge("Rendering", "warm") : ""}
      </div>
      <dl class="receipt">
        <dt>Candidate</dt><dd>${shortHash(project.qc?.candidateHash)}</dd>
        <dt>Duration</dt><dd>${timecode(project.input.duration)}</dd>
        <dt>Narration</dt><dd>${project.audio ? escapeHtml(project.audio.name) : "No recording"}</dd>
        ${project.mix ? `<dt>Music</dt><dd>${project.mix.musicMode === "track" ? escapeHtml(project.mix.music?.name) : "No music"}</dd>
          <dt>Sound effects</dt><dd>${project.mix.effects.length}</dd>` : ""}
      </dl>
      ${running ? '<p class="hint actions" role="status">Rendering the accepted candidate…</p>' : ""}
      ${status === "failed" ? `<p class="notice error actions">${escapeHtml(output.error || "The render failed.")}</p>` : ""}
      ${!state.config.renderAvailable ? mediaSettingsNotice("Set up the media tools to export a video.") : ""}
      ${status === "complete" ? `<div class="actions cluster">
        ${exportLink(output.url, "Download MP4", true)}
        ${exportLink(output.captionsUrl, "Download captions")}
        ${exportLink(output.receiptUrl, "Download receipt")}
      </div>` : `<div class="actions">${button(status === "failed" ? "Retry export" : "Render video", "export", {
        primary: true,
        disabled: running || !state.config.renderAvailable,
      })}</div>`}
    </section>`;
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
  const starts = project.plan.scenes.map(scene => Number(sceneStart(project, scene)));
  const duration = Number(project.input.duration);
  const key = [project.id, project.input.hash, project.plan.hash, ...project.assets.map(asset => asset.hash)].join(".");
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
    const query = new URLSearchParams({ time: time.toFixed(3), revision: project.revision });
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
    state.previewPosition = { key, time, hasSeeked };
    seek.value = time;
    current.textContent = timecode(time);
    seek.setAttribute("aria-valuetext", timecode(time));
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
    play.textContent = "Play silent preview";
    play.setAttribute("aria-pressed", "false");
    if (playNarration) {
      playNarration.textContent = "Play with mix";
      playNarration.setAttribute("aria-pressed", "false");
    }
  }

  function start(withNarration = false) {
    pause();
    const playback = playbackId;
    const offset = !hasSeeked || Number(seek.value) >= duration ? 0 : Number(seek.value);
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
      const time = Math.min(duration, withNarration ? narration.currentTime : (performance.now() - clock) / 1000);
      show(time);
      if (time >= duration || (withNarration && narration.ended)) pause();
    }, 250);
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
  show(savedPosition?.time ?? visibleSceneTime(0));
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
  if (state.closed) return;
  if (!state.project?.busy && state.project?.export?.status !== "running") return;
  const id = state.project.id;
  state.poll = setTimeout(async () => {
    try {
      const current = await api(`/api/projects/${encodeURIComponent(id)}`);
      if (state.closed || state.project?.id !== id) return;
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
    const response = await fetch("/api/quit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(data?.error || "Bacchus could not quit. Please try again.");
    }
    state.closed = true;
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    finish();
  }
  if (!state.closed) return;
  clearTimeout(state.poll);
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
    useOpenAITranscription: data.has("useOpenAITranscription"),
    useOpenAIImages: data.has("useOpenAIImages"),
    clearOpenAIKey: data.has("clearOpenAIKey"),
    clearAnthropicKey: data.has("clearAnthropicKey"),
  };
  for (const field of ["openAIKey", "anthropicKey", "openAIModel", "claudeModel", "transcribeModel", "imageModel"]) {
    const value = data.get(field)?.trim();
    if (value) payload[field] = value;
  }
  return payload;
}

async function refreshLibrary() {
  try {
    const listing = await api("/api/library");
    state.library = listing.assets;
    if (state.view === "library" && !state.pending) render();
  } catch (error) {
    if (state.view === "library") {
      state.library ??= [];
      if (!state.pending) render();
      setMessage(error.message, "error");
    }
  }
}

async function pinArtwork(data) {
  const projectId = data.get("project");
  const alias = data.get("alias").trim();
  const libraryId = state.libraryId;
  const asset = state.library.find(item => item.id === libraryId);
  const version = state.libraryVersion ?? asset?.latestVersion;
  const path = `/api/projects/${encodeURIComponent(projectId)}`;
  const previousProject = state.project?.id;
  await perform(async () => {
    const project = await api(path);
    const existing = project.assets.find(item => item.id === alias);
    if (existing && (existing.libraryId !== libraryId || existing.libraryVersion !== version)) {
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

function updatePlanDraft() {
  const scenes = Array.from(content.querySelectorAll("[data-scene]"), section => {
    return Object.fromEntries(Array.from(section.querySelectorAll("[data-field]"), field => {
      const value = field.type === "number" ? Number(field.value) : field.value;
      return [field.dataset.field, value];
    }));
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
  };
  const dirty = JSON.stringify(settings) !== JSON.stringify(mixSettings(project.mix));
  state.mixDraft = dirty ? { id: project.id, revision: project.revision, settings } : null;
  const busy = state.pending || Boolean(project.busy);
  const control = selector => form.querySelector(selector);
  control("[data-mix-save]").disabled = busy || !dirty || !settings.musicMode;
  const approve = control('[data-action="approve-mix"]');
  if (approve) approve.disabled = busy || dirty || !settings.musicMode;
  const listen = control("[data-mix-listen]");
  listen.hidden = dirty || !project.mix.musicMode || !hasAudibleTracks(project);
  if (dirty) listen.querySelector("audio")?.pause();
  control("[data-mix-unsaved]").hidden = !dirty;
  const noDecision = control("[data-mix-no-decision]");
  if (noDecision) noDecision.hidden = dirty;
  const silent = control("[data-mix-silent]");
  if (silent) silent.hidden = dirty;
  control("[data-mix-next]").hidden = dirty || !project.mix.approved;
  return settings;
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
  if (control.id === "library-version") {
    state.libraryVersion = Number(control.value);
    content.querySelector("#library-inspector").innerHTML = libraryInspector();
    content.querySelector("#library-version").focus();
  } else if (control.id === "library-project") {
    state.libraryProject = control.value;
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
  if (control.id === "library-search") {
    state.libraryQuery = control.value;
    updateLibrarySearch();
  } else if (control.id === "library-alias") {
    state.libraryAlias = control.value;
  }
  if (["script", "intention", "style"].includes(control.id)) {
    state.inputDraft = {
      id: state.project.id,
      script: content.querySelector("#script").value,
      intention: content.querySelector("#intention").value,
      style: content.querySelector("#style").value,
    };
  }
  if (control.dataset.field) updatePlanDraft();
  if (control.hasAttribute("data-mix-setting")) updateMixDraft(control.form);
});

function replaceTranscriptDraft() {
  if (state.inputDraft) state.inputDraft.script = undefined;
}

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
  const replacedAudio = name === "audio" && project.audio && (project.transcript || state.inputDraft?.script);
  const replacedMusic = name === "mix-music" && project.mix.music;
  const changingMix = name.startsWith("mix-");
  const replacingTimings = name === "timings" && content.querySelector("#script").value.trim();
  const warning = replacedArt ? `Replace the artwork for ${id}? The new image will need approval.`
    : replacedAudio ? "Replace this recording and clear the current transcript?"
    : replacedMusic ? "Replace the project's music track?"
    : replacingTimings ? "Import these timings and replace the transcript in the editor?"
    : changingMix && state.mixDraft ? "Discard unsaved composition edits and import this audio?"
    : "";
  if (warning && !window.confirm(warning)) return;
  const path = isArtwork ? `/assets/${encodeURIComponent(id)}`
    : ({ audio: "/audio", timings: "/timings", "mix-music": "/mix/music", "mix-effects": "/mix/effects" })[name];
  perform(async () => {
    const updated = await upload(path, file);
    if (name === "audio" && updated.audio?.hash !== project.audio?.hash) replaceTranscriptDraft();
    if (name === "timings") replaceTranscriptDraft();
    return updated;
  }, { busy: "Importing file…", success: "File imported." });
}

document.addEventListener("submit", event => {
  const form = event.target;
  const name = form.dataset.form;
  if (!name) return;
  event.preventDefault();
  if (state.pending) return;
  const data = new FormData(form);
  if (name === "settings") {
    const payload = settingsPayload(data);
    if ((payload.openAIKey && payload.clearOpenAIKey) || (payload.anthropicKey && payload.clearAnthropicKey)) {
      setMessage("Choose either a replacement key or the clear option for each provider.", "error");
      return;
    }
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ""; });
    saveSettings(payload, !state.settings.completed);
  } else if (name === "library-pin") {
    pinArtwork(data);
  } else if (name === "new-project") {
    const title = data.get("title").trim();
    if (!title) {
      setMessage("Enter a project name.", "error");
      return;
    }
    perform(() => post("/api/projects", { title }), { stage: "intake" });
  } else if (name === "input") {
    perform(() => mutate("/input", {
      script: data.get("script"),
      intention: data.get("intention"),
      style: data.get("style"),
    }), {
      success: "Recording and transcript locked.",
      stage: "plan",
    });
  } else if (name === "plan") {
    const scenes = updatePlanDraft();
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
  "approve-plan": ["/plan/approve", "Plan approved.", project => pendingArt(project).length ? "art" : afterArtwork(project)],
  "approve-mix": ["/mix/approve", "Composition approved.", "review"],
  qc: ["/qc", "Quality checks finished."],
  "accept-candidate": ["/candidate/accept", "Candidate accepted.", "export"],
  export: ["/export", ""],
};

function transcribe() {
  const initial = content.querySelector("#script").value;
  if (initial.trim() && !window.confirm("Generate a new transcript and replace the text in the editor?")) return;
  perform(async () => {
    const project = await mutate("/transcribe");
    if (content.querySelector("#script").value === initial) replaceTranscriptDraft();
    return project;
  }, { busy: "Transcribing the recording…", success: "Transcript ready to review." });
}

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
    openStage("intake", false);
    content.querySelector("#new-title")?.focus();
  } else if (action === "demo") {
    perform(() => post("/api/demo", {}), { busy: "Creating example…", stage: "review" });
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
      const { id, input, style } = state.project;
      state.inputDraft = { id, script: input.script, intention: input.intention, style: input.style ?? style };
      perform(() => mutate("/input/unlock"), { success: "Intake unlocked.", resetPlan: true });
    }
  } else if (action === "transcribe-audio") {
    transcribe();
  } else if (action === "remove-audio") {
    if (window.confirm("Remove this recording and clear the current transcript?")) {
      perform(async () => {
        const project = await mutate("/audio/remove");
        replaceTranscriptDraft();
        return project;
      }, { success: "Recording removed." });
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
  } else if (action === "select-art") {
    content.querySelector("#asset-id").value = name;
    content.querySelector("#art-file").focus();
  } else if (action === "approve-art" || action === "save-art-library") {
    perform(() => mutate(`/assets/${encodeURIComponent(name)}/approve`), {
      success: action === "save-art-library" ? "Artwork saved to the library." : "Artwork approved and saved to the library.",
    });
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
  } else if (stageActions[action]) {
    const [path, success, stage] = stageActions[action];
    perform(() => mutate(path), { success, stage, busy: action === "export" ? "Starting render…" : undefined });
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
    const [config, listing, settings] = await Promise.all([api("/api/config"), api("/api/projects"), api("/api/settings")]);
    state.config = config;
    state.projects = listing.projects;
    state.settings = settings;
    if (!settings.completed) state.view = "settings";
    setConnection("online");
    let last = "";
    try { last = localStorage.getItem("bacchus-project") || ""; } catch { /* Browser storage is optional. */ }
    if (state.projects.some(item => item.id === last)) {
      state.project = await api(`/api/projects/${encodeURIComponent(last)}`);
      state.stage = nextStage(state.project);
    }
    render();
  } catch (error) {
    setConnection("offline");
    content.innerHTML = heading("Studio unavailable", "Open Bacchus again to reconnect.");
    setMessage(error.message, "error");
  } finally {
    picker.disabled = !state.config;
    newProject.disabled = !state.config;
  }
}

initialize();
