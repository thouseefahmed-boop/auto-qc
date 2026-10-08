"use strict";
const LABELS = ["bad_product", "bad_movement", "nudity"];
const titles = {
  overview: "Overview",
  videos: "Video library",
  testsets: "Testsets",
  modes: "Model modes",
  benchmarks: "Benchmarks",
};
const state = {
  tab: "overview",
  stats: null,
  offset: 0,
  set: "",
  label: "",
  search: "",
  generationRows: [],
  modes: [],
  testsets: [],
  runs: [],
  detail: null,
  runPage: 0,
};
const $ = (s) => document.querySelector(s);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const nice = (s) =>
  String(s)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
const fmt = (n) => Number(n || 0).toLocaleString();
const pct = (n) => (n == null ? "—" : (100 * n).toFixed(1) + "%");
const date = (s) =>
  new Date(s).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
const badge = (s, c = "gray") => `<span class="badge ${c}">${esc(s)}</span>`;
const statusBadge = (s) =>
  badge(
    nice(s),
    s === "completed"
      ? "green"
      : s === "running" || s === "queued"
        ? "amber"
        : s === "failed"
          ? "red"
          : "gray",
  );
const labelValue = (v) =>
  v === true
    ? badge("Issue present", "red")
    : v === false
      ? badge("Issue absent", "green")
      : badge("Unknown");
const head = (title, desc, actions = "") =>
  `<div class="page-head"><div><div class="eyebrow">AUTOQC WORKSPACE</div><h1>${title}</h1><p>${desc}</p></div><div class="actions">${actions}</div></div>`;
const empty = (title, desc, action = "") =>
  `<div class="empty"><div class="empty-symbol">◇</div><h3>${title}</h3><p>${desc}</p>${action}</div>`;
let toastTimer;
function toast(message, error = false) {
  $("#toast").textContent = message;
  $("#toast").className = "visible" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").className = ""), 6500);
}
async function api(path, method = "GET", body) {
  const r = await fetch("/api" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data;
  try {
    data = await r.json();
  } catch {
    throw new Error("The server returned an unexpected response");
  }
  if (!r.ok) {
    let d = data.detail;
    throw new Error(
      typeof d === "string" ? d : d?.message || JSON.stringify(d),
    );
  }
  return data;
}
function closeModal() {
  const modal = $("#modal");
  modal.querySelectorAll("video").forEach((v) => {
    v.pause();
    v.removeAttribute("src");
    v.load();
  });
  modal.close();
  $("#modal-content").innerHTML = "";
}
function modal(title, body, footer = "") {
  if ($("#modal").open) closeModal();
  $("#modal-content").innerHTML =
    `<div class="modal-head"><h2>${title}</h2><button class="modal-close" data-action="close" aria-label="Close">×</button></div><div class="modal-body">${body}<div id="form-error"></div></div><div class="modal-footer">${footer || '<button data-action="close">Close</button>'}</div>`;
  $("#modal").showModal();
}
function formError(e) {
  const el = $("#form-error");
  if (el) el.innerHTML = `<div class="error-message">${esc(e.message)}</div>`;
  else toast(e.message, true);
}
function formValue(id) {
  return $("#" + id)?.value;
}
async function guarded(button, fn) {
  if (button) button.disabled = true;
  try {
    await fn();
  } catch (e) {
    formError(e);
  } finally {
    if (button) button.disabled = false;
  }
}
async function load() {
  const tab = location.hash.slice(1) || "overview";
  state.tab = titles[tab] ? tab : "overview";
  state.detail = null;
  document
    .querySelectorAll("nav a")
    .forEach((a) => a.classList.toggle("active", a.dataset.tab === state.tab));
  $("#breadcrumb").textContent = titles[state.tab];
  $("#content").innerHTML = '<div class="loading">Loading…</div>';
  const current = state.tab;
  try {
    state.stats = await api("/stats");
    if (current !== state.tab) return;
    if (current === "overview") await overview();
    if (current === "videos") await videos();
    if (current === "testsets") await testsets();
    if (current === "modes") await modes();
    if (current === "benchmarks") await benchmarks();
  } catch (e) {
    $("#content").innerHTML =
      `<div class="page">${empty("Could not load the workspace", esc(e.message), '<button data-action="refresh">Try again</button>')}</div>`;
  }
}
async function overview() {
  const s = state.stats;
  state.runs = await api("/benchmarks?limit=5");
  $("#content").innerHTML =
    `<div class="page">${head("Your evaluation, at a glance", "One place for reviewed videos, frozen testsets, and model performance.", '<button data-action="refresh">↻ Refresh</button><button class="primary" data-action="new-testset">+ Create testset</button>')}
  <div class="stats-grid">${[
    [s.generations, "Reviewed videos", "Unique artifact IDs", "▷"],
    [
      s.sets.normal || 0,
      "Normal videos",
      "All three issue labels reviewed absent",
      "✓",
    ],
    [
      s.testsets,
      "Frozen testsets",
      "Membership and human labels preserved",
      "▦",
    ],
    [
      s.benchmarks,
      "Benchmark runs",
      "Real results only · no simulated scores",
      "↗",
    ],
  ]
    .map(
      ([n, t, note, icon]) =>
        `<div class="stat"><div class="stat-label">${t}<span class="stat-icon">${icon}</span></div><div class="stat-number">${fmt(n)}</div><div class="stat-note">${note}</div></div>`,
    )
    .join("")}</div>
  ${!s.sets.normal ? '<div class="notice warning"><span>ⓘ</span><div><b>Your mixed testset needs reviewed normal videos.</b>Rejected issue candidates are not automatically normal. Add videos and explicitly mark all three labels absent to build the 20 normal + 20 bad product + 20 nudity set.</div></div>' : ""}
  <div class="grid-two"><section class="card"><div class="card-head"><div><h2>Adversarial coverage</h2><p class="tiny">Human-approved issues in your video bank</p></div>${badge("LIVE LIBRARY", "green")}</div><div class="card-body">${LABELS.map((l, i) => `<div class="row"><div class="row-label"><span class="category-icon">${["◈", "↝", "◇"][i]}</span>${nice(l)}</div><div class="actions"><div class="bar"><div style="width:${Math.round((100 * s.labels[l]) / Math.max(s.generations, 1))}%"></div></div><b>${fmt(s.labels[l])}</b></div></div>`).join("")}<p class="inline-note">A video can have multiple issues. Category counts can overlap; testset membership cannot.</p></div></section>
  <section class="card"><div class="card-head"><h2>From labels to benchmarks</h2></div><div class="card-body pipeline">${[
    ["Review & organize", "Human labels are true, false, or unknown."],
    [
      "Freeze a testset",
      "A saved query resolves into unique videos and fixed labels.",
    ],
    [
      "Compare model modes",
      "Queue Cosmos and Qwen on the same testset. Workers connect later.",
    ],
  ]
    .map(
      ([t, d], i) =>
        `<div class="pipeline-step"><span class="step-number">${i + 1}</span><div><h3>${t}</h3><p>${d}</p></div></div>`,
    )
    .join("")}</div></section></div>
  <div class="spacer"></div><section class="card"><div class="card-head"><div><h2>Recent benchmarks</h2><p class="tiny">Track runs from queue to per-video results</p></div><a href="#benchmarks">View all →</a></div>${state.runs.length ? runTable(state.runs) : empty("No benchmark results yet", "Create a testset and queue a run. Workers will supply real predictions when you connect them.", '<button data-action="new-run">Queue a benchmark</button>')}</section>
  <div class="notice"><span>ⓘ</span><div><b>Keep labels and model predictions distinct.</b>The legacy “nudity” category includes exposure concerns such as visible thighs and body contour. Configure the exact policy before interpreting scores as explicit-nudity detection.</div></div></div>`;
}
async function videos() {
  const q = new URLSearchParams({ limit: 20, offset: state.offset });
  if (state.set) q.set("set", state.set);
  if (state.label) q.set("label", state.label);
  if (state.search) q.set("search", state.search);
  const data = await api("/generations?" + q);
  state.generationRows = data.items;
  $("#content").innerHTML =
    `<div class="page">${head("Video library", "Reviewed attributes, reference images, and streamed playback.", '<button class="primary" data-action="add-video">+ Add video</button>')}<div class="toolbar"><input id="video-search" placeholder="Search artifact ID, product, or item ID" value="${esc(state.search)}"><select id="set-filter" aria-label="Set filter"><option value="">All sets</option>${["normal", "adversarial", "unclassified"].map((s) => `<option ${state.set === s ? "selected" : ""} value="${s}">${nice(s)}</option>`).join("")}</select><select id="label-filter" aria-label="Issue filter"><option value="">All issues</option>${LABELS.map((s) => `<option ${state.label === s ? "selected" : ""} value="${s}">${nice(s)}</option>`).join("")}</select><button data-action="filter">Filter</button><span class="tiny muted">${fmt(data.total)} videos</span></div>
  <div class="video-grid">${data.items
    .map((r) => {
      const m = r.tags.metadata || {};
      return `<article class="video-card"><div class="video-cover" data-action="review-video" data-id="${r.uuid}" role="button" tabindex="0" aria-label="Review video"><video muted playsinline preload="none" ${m.image_url ? `poster="${esc(m.image_url)}"` : ""}></video><span class="play-overlay">▷</span></div><div class="info"><div class="video-title">${esc(m.product_title || "Lifestyle video")}</div><div class="pills">${badge(nice(r.tags.set), r.tags.set === "normal" ? "green" : "gray")}${LABELS.filter(
        (l) => r.tags.labels[l] === true,
      )
        .map((l) => badge(nice(l), "amber"))
        .join(
          "",
        )}</div><span class="mono">${r.artifact_id}</span>${m.item_id ? `<span class="mono">Item: ${esc(m.item_id)}</span>` : ""}<button class="small-btn full" data-action="review-video" data-id="${r.uuid}">Play & review labels</button></div></article>`;
    })
    .join(
      "",
    )}</div>${!data.total ? empty("No videos in this slice", "Try a different filter or add a video by artifact ID.") : ""}<div class="pagination"><button data-action="prev" ${!state.offset ? "disabled" : ""}>← Previous</button><span>${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + 20, data.total)} of ${fmt(data.total)}</span><button data-action="next" ${state.offset + 20 >= data.total ? "disabled" : ""}>Next →</button></div></div>`;
}
function labelsForm(labels = {}) {
  return LABELS.map(
    (l) =>
      `<label for="label-${l}">${nice(l)}</label><select id="label-${l}"><option value="unknown" ${labels[l] == null ? "selected" : ""}>Unknown / not reviewed</option><option value="true" ${labels[l] === true ? "selected" : ""}>Issue present</option><option value="false" ${labels[l] === false ? "selected" : ""}>Issue absent</option></select>`,
  ).join("");
}
function readLabels() {
  return Object.fromEntries(
    LABELS.map((l) => [
      l,
      formValue("label-" + l) === "unknown"
        ? null
        : formValue("label-" + l) === "true",
    ]),
  );
}
async function reviewVideo(id) {
  const row = await api("/generations/" + id);
  state.reviewRow = row;
  const m = row.tags.metadata || {};
  modal(
    "Review video labels",
    `<div class="review-media"><div><video class="player" controls autoplay muted playsinline src="/api/media/${row.artifact_id}"></video><p class="mono" style="margin-top:12px">Artifact: ${row.artifact_id}</p>${m.item_id ? `<p class="mono">Item: ${esc(m.item_id)}</p>` : ""}</div><div><h3>${esc(m.product_title || "Lifestyle video")}</h3>${m.image_url ? `<label>Product reference</label><img class="reference" src="${esc(m.image_url)}" alt="Product reference">` : '<p class="field-help">No product reference available. Keep bad product unknown if you cannot assess it.</p>'}${labelsForm(row.tags.labels)}<label>Review note</label><input id="review-note" maxlength="2000" placeholder="Optional note"><p class="field-help">All three labels must be explicitly absent for a normal video. Past testsets keep their original labels.</p></div></div>`,
    `<button data-action="mark-normal">Mark all issues absent</button><button class="primary" data-action="save-review">Save labels</button>`,
  );
}
function addVideo() {
  modal(
    "Add a video",
    `<p>Use an artifact UUID. Videos play from S3; nothing is downloaded to your Mac.</p><label>Video artifact ID</label><input id="add-artifact" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"><div class="split"><div><label>Product title</label><input id="add-title" placeholder="Optional"><label>Product reference image URL</label><input id="add-image" placeholder="https://…"></div><div>${labelsForm()}<label>Note</label><input id="review-note" placeholder="Optional"></div></div><p class="field-help">Unknown is the default. Mark a label absent only after reviewing it.</p>`,
    `<button data-action="close">Cancel</button><button class="primary" data-action="save-video">Add video</button>`,
  );
}
async function testsets() {
  state.testsets = await api("/testsets");
  $("#content").innerHTML =
    `<div class="page">${head("Frozen testsets", "Save the selection recipe and the exact videos it resolves to.", '<button class="primary" data-action="new-testset">+ Create testset</button>')}<div class="notice"><span>▦</span><div><b>A query is the recipe. Items are the frozen result.</b>Each video appears once. Human labels, annotation timestamps, and product references are saved with the selected videos. Create a new testset to change membership.</div></div><section class="card">${state.testsets.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Videos</th><th>Composition</th><th>Created</th><th></th></tr></thead><tbody>${state.testsets.map((t) => `<tr><td><a class="name-link" data-action="view-testset" data-id="${t.uuid}">${esc(t.name)}</a><div class="mono muted" style="margin-top:6px">${t.uuid}</div></td><td><b>${t.size}</b></td><td>${t.query.slices.map((s) => badge(`${s.count} ${nice(s.label || s.set)}`)).join(" ")}</td><td class="muted">${date(t.created_at)}</td><td><button class="small-btn" data-action="new-run" data-testset="${t.uuid}">Benchmark →</button></td></tr>`).join("")}</tbody></table></div>` : empty("Create your first testset", "Start with 20 normal, 20 bad-product, and 20 nudity videos, or use your own composition.", '<button data-action="new-testset">Create testset</button>')}</section></div>`;
}
function recipeForm() {
  return `<label>Testset name</label><input id="testset-name" value="Lifestyle · mixed 60"><label>Deterministic seed</label><input id="testset-seed" type="number" min="0" value="42"><label>Videos per slice</label><div class="truth-grid">${[
    ["normal", "Normal", 20],
    ["bad_product", "Bad product", 20],
    ["nudity", "Nudity", 20],
    ["bad_movement", "Bad movement", 0],
  ]
    .map(
      ([k, t, n]) =>
        `<div><label class="tiny" for="count-${k}">${t}</label><input id="count-${k}" type="number" value="${n}" min="0" max="1000"></div>`,
    )
    .join(
      "",
    )}</div><p class="field-help">Set a count to zero to leave that slice out. The same artifact cannot appear in two slices.</p><div id="preview-result"></div>`;
}
function recipe() {
  const slices = ["normal", ...LABELS]
    .map((k) => ({
      set: k === "normal" ? "normal" : "adversarial",
      label: k === "normal" ? null : k,
      count: Number(formValue("count-" + k)),
    }))
    .filter((s) => s.count > 0);
  return { seed: Number(formValue("testset-seed")), slices };
}
function newTestset() {
  modal(
    "Build a testset",
    recipeForm(),
    '<button data-action="preview-testset">Preview selection</button><button class="primary" data-action="save-testset">Freeze testset</button>',
  );
}
async function viewTestset(id) {
  const t = await api("/testsets/" + id);
  modal(
    esc(t.name),
    `<div class="notice"><div><b>${t.items.length} unique videos · frozen labels</b>Seed ${t.query.seed}. These items are used for every benchmark on this testset.</div></div><pre>${esc(JSON.stringify(t.query, null, 2))}</pre><div class="table-wrap"><table><thead><tr><th>Artifact ID</th><th>Human-positive labels</th></tr></thead><tbody>${t.items
      .map(
        (i) =>
          `<tr><td class="mono">${i.artifact_id}</td><td>${
            LABELS.filter((l) => i.labels[l] === true)
              .map((l) => badge(nice(l), "amber"))
              .join(" ") ||
            badge(i.set === "normal" ? "Normal" : "No confirmed issue")
          }</td></tr>`,
      )
      .join("")}</tbody></table></div>`,
  );
}
async function modes() {
  state.modes = await api("/modes");
  $("#content").innerHTML =
    `<div class="page">${head("Model modes", "A named, reproducible model configuration—not just a model name.", '<button class="primary" data-action="new-mode">+ New mode</button>')}<div class="notice warning"><span>◇</span><div><b>Workers will be connected later.</b>Cosmos and Qwen A3B are registered. Supply the exact checkpoint and worker integration when available. Queued benchmarks wait safely for a compatible worker.</div></div><div class="mode-grid">${state.modes
      .map(
        (m) =>
          `<section class="card"><div class="card-body"><div class="mode-top"><div class="model-icon">◇</div><div><h2>${esc(m.name)}</h2><p class="tiny">${esc(m.config.model)}</p></div></div><dl>${[
            ["Checkpoint", m.config.checkpoint || "Not supplied yet"],
            ["Frame sampling", m.config.sampling_fps + " fps"],
            ["Prompt version", m.config.prompt_version],
            ["Worker pool", m.config.worker_pool],
          ]
            .map(
              ([k, v]) =>
                `<div class="kv"><dt>${k}</dt><dd>${esc(v)}</dd></div>`,
            )
            .join(
              "",
            )}</dl><div class="actions">${badge(m.config.checkpoint ? "Checkpoint configured" : "Awaiting worker details", m.config.checkpoint ? "green" : "amber")}<button class="small-btn" data-action="edit-mode" data-id="${m.uuid}">Edit configuration</button></div><p class="field-help">Runs keep a configuration snapshot, even if this mode changes later.</p></div></section>`,
      )
      .join("")}</div></div>`;
}
function modeForm(mode = null) {
  state.editMode = mode;
  const config = mode?.config || {
    model: "",
    checkpoint: null,
    sampling_fps: 4,
    prompt_version: "autoqc-v1",
    prompt:
      "Detect bad_product, bad_movement, and nudity according to the supplied label definitions. Return true, false, or null for each label; do not guess.",
    label_definitions: Object.fromEntries(
      LABELS.map((l) => [l, "Define the reviewed policy for " + l]),
    ),
    preprocessing: {},
    worker_pool: "default",
  };
  modal(
    mode ? "Edit mode" : "New model mode",
    `<label>Mode name</label><input id="mode-name" value="${esc(mode?.name || "")}"><label>Configuration JSON</label><textarea id="mode-config" style="min-height:350px">${esc(JSON.stringify(config, null, 2))}</textarea><p class="field-help">Model ID, exact checkpoint, sampling FPS, prompt, label definitions, preprocessing, and worker pool. Do not store secrets here.</p>`,
    `<button data-action="close">Cancel</button><button class="primary" data-action="save-mode">Save configuration</button>`,
  );
}
function runTable(runs) {
  return `<div class="table-wrap"><table><thead><tr><th>Mode / testset</th><th>Status</th><th>Progress</th><th>Bad product</th><th>Bad movement</th><th>Nudity</th><th></th></tr></thead><tbody>${runs
    .map(
      (r) =>
        `<tr><td><a class="name-link" data-action="view-run" data-id="${r.uuid}">${esc(r.config_snapshot.mode_name)}</a><div class="tiny muted" style="margin-top:5px">${esc(r.config_snapshot.testset_name)} · ${date(r.created_at)}</div></td><td>${statusBadge(r.status)}</td><td><span class="tiny">${r.numbers.processed || 0} / ${r.numbers.total || 0}</span><div class="progress"><div style="width:${(100 * (r.numbers.processed || 0)) / Math.max(r.numbers.total || 0, 1)}%"></div></div></td>${LABELS.map(
          (l) => {
            const m = r.numbers.labels?.[l];
            return `<td><span class="metric-num">${m ? `${m.tp}/${m.human_positive}` : "—"}</span><div class="tiny muted" style="margin-top:4px">${m ? pct(m.detection_rate) : "—"} caught</div></td>`;
          },
        ).join(
          "",
        )}<td><button class="small-btn" data-action="view-run" data-id="${r.uuid}">Inspect →</button></td></tr>`,
    )
    .join("")}</tbody></table></div>`;
}
async function benchmarks() {
  state.runs = await api("/benchmarks");
  $("#content").innerHTML =
    `<div class="page">${head("Benchmarks", "Compare models on identical videos and inspect each result.", '<button data-action="refresh">↻ Refresh</button><button class="primary" data-action="new-run">+ Queue benchmark</button>')}<div class="notice"><span>↗</span><div><b>“Caught” means true positives / all human-positive videos.</b>Unknown labels and inference failures are shown separately. Precision and recall are available inside each run; unknown human labels never count as negatives.</div></div><section class="card">${state.runs.length ? runTable(state.runs) : empty("No runs yet", "Select a frozen testset and one or both model modes. Jobs remain queued until your workers connect.", '<button data-action="new-run">Queue benchmark</button>')}</section></div>`;
}
async function newRun(testsetId = "") {
  [state.modes, state.testsets] = await Promise.all([
    api("/modes"),
    api("/testsets"),
  ]);
  if (!state.testsets.length) {
    toast("Create a frozen testset first.");
    newTestset();
    return;
  }
  modal(
    "Queue a benchmark",
    `<p>Choose one testset and the modes to compare. Each mode gets its own durable run.</p><label>Frozen testset</label><select id="run-testset">${state.testsets.map((t) => `<option value="${t.uuid}" ${t.uuid === testsetId ? "selected" : ""}>${esc(t.name)} · ${t.size} videos</option>`).join("")}</select><label>Model modes</label>${state.modes.map((m) => `<label class="label-check"><input type="checkbox" class="run-mode" value="${m.uuid}" checked><span><b>${esc(m.name)}</b><span class="muted tiny"> · ${m.config.sampling_fps} fps</span></span></label>`).join("")}<p class="field-help">Actual workers run inference. Queuing does not fabricate predictions or scores.</p>`,
    `<button data-action="close">Cancel</button><button class="primary" data-action="save-run">Queue selected modes</button>`,
  );
}
async function viewRun(id) {
  const run = await api("/benchmarks/" + id);
  state.detail = run;
  state.runPage = 0;
  renderRun();
}
function renderRun() {
  const r = state.detail;
  const items = r.items.slice(state.runPage * 15, state.runPage * 15 + 15);
  $("#content").innerHTML =
    `<div class="page">${head(esc(r.config_snapshot.mode_name), esc(r.config_snapshot.testset_name) + " · " + r.uuid, `<button data-action="back-runs">← All runs</button><button data-action="refresh-run" data-id="${r.uuid}">↻ Refresh</button>${["queued", "running"].includes(r.status) ? `<button class="danger" data-action="cancel-run" data-id="${r.uuid}">Cancel run</button>` : ""}`)}<div class="actions" style="margin-bottom:23px">${statusBadge(r.status)}<span class="muted tiny">${r.numbers.processed}/${r.numbers.total} processed · ${r.numbers.inference_errors} inference errors${r.worker_id ? " · worker " + esc(r.worker_id) : " · waiting for a compatible worker"}</span></div><div class="stats-grid" style="grid-template-columns:repeat(3,1fr)">${LABELS.map(
      (l) => {
        const m = r.numbers.labels[l];
        return `<section class="stat"><div class="stat-label">${nice(l)}</div><div class="stat-number">${m.tp}<span style="font-size:18px;color:var(--muted)"> / ${m.human_positive}</span></div><div class="stat-note">${pct(m.detection_rate)} of all human positives caught</div><div class="spacer"></div><div class="kv"><dt>Precision</dt><dd>${pct(m.precision)}</dd></div><div class="kv"><dt>Recall, judged</dt><dd>${pct(m.recall)}</dd></div><div class="kv"><dt>TP / FN / FP / TN</dt><dd>${m.tp} / ${m.fn} / ${m.fp} / ${m.tn}</dd></div><div class="kv"><dt>Human unknown</dt><dd>${m.human_unknown}</dd></div><div class="kv"><dt>Model unknown</dt><dd>${m.model_unknown}</dd></div><div class="kv"><dt>Coverage</dt><dd>${pct(m.coverage)}</dd></div><div class="kv"><dt>Errors / pending</dt><dd>${m.inference_errors} / ${m.pending}</dd></div></section>`;
      },
    ).join(
      "",
    )}</div><section class="card"><div class="card-head"><h2>Per-video results</h2><button class="small-btn" data-action="run-config">Configuration snapshot</button></div><div class="card-body run-items">${items
      .map((i) => {
        const p = r.predictions[i.artifact_id];
        return `<div class="prediction-row"><div><div class="mono">${i.artifact_id}</div><p class="tiny" style="margin-top:7px">${esc(i.metadata?.product_title || "Lifestyle video")} · Slice ${i.slice_index + 1}</p>${p?.error ? `<p class="tiny" style="color:var(--red)">${esc(p.error)}</p>` : ""}</div><div class="prediction-labels">${LABELS.map((l) => `<div>${nice(l)}<b>Human: ${i.labels[l] == null ? "?" : i.labels[l] ? "issue" : "absent"}</b><b>Model: ${!p ? "pending" : p.error ? "error" : p.labels[l] == null ? "?" : p.labels[l] ? "issue" : "absent"}</b></div>`).join("")}</div><button class="small-btn" data-action="prediction-video" data-id="${i.artifact_id}">Inspect</button></div>`;
      })
      .join(
        "",
      )}<div class="pagination"><button data-action="run-prev" ${!state.runPage ? "disabled" : ""}>← Previous</button><span>${state.runPage + 1} / ${Math.ceil(r.items.length / 15)}</span><button data-action="run-next" ${(state.runPage + 1) * 15 >= r.items.length ? "disabled" : ""}>Next →</button></div></div></section></div>`;
  if (r.error) {
    const error = document.createElement("div");
    error.className = "error-message";
    error.textContent = r.error;
    $("#content .page-head").after(error);
  }
}
function predictionVideo(id) {
  const i = state.detail.items.find((x) => x.artifact_id === id),
    p = state.detail.predictions[id];
  modal(
    "Frozen annotation & model result",
    `<div class="review-media"><div><video class="player" autoplay muted playsinline controls src="/api/media/${id}"></video><p class="mono" style="margin-top:10px">${id}</p></div><div>${i.metadata?.image_url ? `<img class="reference" src="${esc(i.metadata.image_url)}" alt="Frozen product reference">` : ""}${LABELS.map((l) => `<label>${nice(l)}</label><div class="actions">Human ${labelValue(i.labels[l])}</div><div class="actions" style="margin-top:5px">Model ${p?.error ? badge("Inference error", "red") : !p ? badge("Pending") : labelValue(p.labels[l])}</div>`).join("")}<p class="field-help">These are frozen labels from the testset, not later library edits.</p></div></div>${p ? `<label>Evidence / error</label><pre>${esc(JSON.stringify(p, null, 2))}</pre>` : ""}`,
  );
}
document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target || target.disabled) return;
  const action = target.dataset.action,
    id = target.dataset.id;
  try {
    if (action === "close") {
      closeModal();
      return;
    }
    if (action === "refresh") {
      await load();
      return;
    }
    if (action === "new-testset") {
      newTestset();
      return;
    }
    if (action === "add-video") {
      addVideo();
      return;
    }
    if (action === "filter") {
      state.set = formValue("set-filter");
      state.label = formValue("label-filter");
      state.search = formValue("video-search");
      state.offset = 0;
      await videos();
      return;
    }
    if (action === "prev" || action === "next") {
      state.offset = Math.max(0, state.offset + (action === "next" ? 20 : -20));
      await videos();
      return;
    }
    if (action === "review-video") {
      await reviewVideo(id);
      return;
    }
    if (action === "mark-normal") {
      LABELS.forEach((l) => ($("#label-" + l).value = "false"));
      toast(
        "All three labels set absent. Save only if you reviewed all three.",
      );
      return;
    }
    if (action === "save-review") {
      await guarded(target, async () => {
        await api("/generations/" + state.reviewRow.uuid + "/labels", "PATCH", {
          labels: readLabels(),
          note: formValue("review-note"),
          expected_updated_at: state.reviewRow.updated_at,
        });
        closeModal();
        toast("Labels saved. Frozen testsets unchanged.");
        await load();
      });
      return;
    }
    if (action === "save-video") {
      await guarded(target, async () => {
        const image = formValue("add-image");
        if (image && !/^https?:\/\//i.test(image))
          throw new Error("Reference image must be an http(s) URL");
        await api("/generations", "POST", {
          artifact_id: formValue("add-artifact").trim(),
          labels: readLabels(),
          note: formValue("review-note"),
          metadata: { product_title: formValue("add-title"), image_url: image },
        });
        closeModal();
        toast("Video added.");
        location.hash = "videos";
        await load();
      });
      return;
    }
    if (action === "preview-testset") {
      await guarded(target, async () => {
        const data = await api("/testsets/preview", "POST", recipe());
        $("#preview-result").innerHTML =
          `<div class="notice" style="margin-top:20px"><div><b>${data.total} unique videos are available.</b>${data.availability.map((s) => `${s.count} selected from ${s.available} ${nice(s.label || s.set)} videos`).join("<br>")}</div></div>`;
        $("#form-error").innerHTML = "";
      });
      return;
    }
    if (action === "save-testset") {
      await guarded(target, async () => {
        await api("/testsets", "POST", {
          name: formValue("testset-name"),
          query: recipe(),
        });
        closeModal();
        toast("Testset frozen.");
        location.hash = "testsets";
        await load();
      });
      return;
    }
    if (action === "view-testset") {
      await viewTestset(id);
      return;
    }
    if (action === "new-mode") {
      modeForm();
      return;
    }
    if (action === "edit-mode") {
      modeForm(state.modes.find((m) => m.uuid === id));
      return;
    }
    if (action === "save-mode") {
      await guarded(target, async () => {
        const body = {
          name: formValue("mode-name"),
          config: JSON.parse(formValue("mode-config")),
        };
        await api(
          "/modes" + (state.editMode ? "/" + state.editMode.uuid : ""),
          state.editMode ? "PUT" : "POST",
          body,
        );
        closeModal();
        toast("Mode saved. Past run snapshots unchanged.");
        await load();
      });
      return;
    }
    if (action === "new-run") {
      await newRun(target.dataset.testset);
      return;
    }
    if (action === "save-run") {
      await guarded(target, async () => {
        const mode_ids = [
          ...document.querySelectorAll(".run-mode:checked"),
        ].map((x) => x.value);
        await api("/benchmarks", "POST", {
          mode_ids,
          testset_id: formValue("run-testset"),
        });
        closeModal();
        toast("Runs queued. Waiting for real worker predictions.");
        location.hash = "benchmarks";
        await load();
      });
      return;
    }
    if (action === "view-run" || action === "refresh-run") {
      await viewRun(id);
      return;
    }
    if (action === "back-runs") {
      state.detail = null;
      await benchmarks();
      return;
    }
    if (action === "cancel-run") {
      if (
        confirm("Cancel this run? All results already saved will be preserved.")
      ) {
        await api("/benchmarks/" + id + "/cancel", "POST");
        await viewRun(id);
      }
      return;
    }
    if (action === "run-config") {
      modal(
        "Frozen configuration",
        `<pre>${esc(JSON.stringify(state.detail.config_snapshot, null, 2))}</pre>`,
      );
      return;
    }
    if (action === "prediction-video") {
      predictionVideo(id);
      return;
    }
    if (action === "run-prev" || action === "run-next") {
      state.runPage += action === "run-next" ? 1 : -1;
      renderRun();
    }
  } catch (e) {
    toast(e.message, true);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.id === "video-search") {
    document.querySelector('[data-action="filter"]').click();
  }
  if (
    (e.key === "Enter" || e.key === " ") &&
    e.target.classList.contains("video-cover")
  ) {
    e.preventDefault();
    e.target.click();
  }
});
$("#modal").addEventListener("cancel", (e) => {
  e.preventDefault();
  closeModal();
});
window.addEventListener("hashchange", () => {
  state.offset = 0;
  load();
});
setInterval(async () => {
  if (state.tab === "benchmarks" && !$("#modal").open) {
    try {
      if (state.detail) {
        const page = state.runPage;
        state.detail = await api("/benchmarks/" + state.detail.uuid);
        state.runPage = page;
        renderRun();
      } else await benchmarks();
    } catch {
      /* A manual refresh shows connection errors. */
    }
  }
}, 12000);
load();
