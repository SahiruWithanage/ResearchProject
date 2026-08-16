/* Edge Simulator UI - vanilla JS, no build step.
 *
 * Principles:
 * - Every dropdown/field is generated from /api/schema (registry
 *   introspection). Nothing plugin-specific is hardcoded here.
 * - The YAML panel is the truth the simulator sees: form edits are
 *   serialized server-side (/api/yaml/dump), panel edits are parsed
 *   server-side (/api/yaml/parse) - the browser never parses YAML.
 */
"use strict";

/* ------------------------------------------------------------------ *
 * helpers
 * ------------------------------------------------------------------ */

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c !== null && c !== undefined) {
      node.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
  }
  return node;
}

function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.setAttribute("class", v);
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

async function api(path, body) {
  const opts = body === undefined
    ? {}
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
  const resp = await fetch(path, opts);
  return resp.json();
}

/* ------------------------------------------------------------------ *
 * state
 * ------------------------------------------------------------------ */

let schema = null;      // /api/schema payload
let cfg = null;         // the config object mirrored by the YAML panel
let currentName = "untitled.yaml";
let layout = {};        // "node:<id>" / "ctrl:<id>" -> {x, y} (cosmetic)

const yamlText = $("#yaml-text");
const yamlStatus = $("#yaml-status");

function nodeIds() {
  return (cfg.nodes || []).map((n) => n.id).filter(Boolean);
}

function setOrDelete(obj, key, value) {
  if (value === undefined || value === null || value === "") delete obj[key];
  else obj[key] = value;
}

/* ------------------------------------------------------------------ *
 * YAML sync (form -> panel and panel -> form)
 * ------------------------------------------------------------------ */

const refreshYaml = debounce(async () => {
  const r = await api("/api/yaml/dump", { data: cfg });
  if (r.ok && document.activeElement !== yamlText) {
    yamlText.value = r.yaml;
    setYamlStatus(true, "in sync with the form");
  }
}, 300);

function setYamlStatus(ok, msg) {
  yamlStatus.className = ok ? "ok" : "bad";
  yamlStatus.textContent = (ok ? "✓ " : "✗ ") + msg;
}

/** Call after any scalar cfg change. */
function dirty() {
  drawTopology();
  refreshYaml();
}

/** Call after structural cfg changes (lists, type switches). */
function rebuild() {
  renderBuild();
  refreshModal();
  dirty();
}

// Set the instant an edit is typed, cleared once that text is parsed into
// cfg. Anything that acts on the config must flush this first (see
// syncedYaml) or it would silently act on the pre-edit config.
let yamlPending = false;

async function parseYamlPanel() {
  const r = await api("/api/yaml/parse", { yaml: yamlText.value });
  if (r.ok) {
    cfg = r.data;
    yamlPending = false;
    renderBuild();
    drawTopology();
    setYamlStatus(true, "parsed - form updated");
    return true;
  }
  setYamlStatus(false, r.error);
  return false;
}

yamlText.addEventListener("input", () => {
  yamlPending = true;
});
yamlText.addEventListener("input", debounce(parseYamlPanel, 500));

/**
 * The authoritative YAML for the current state, used by every action that
 * acts on the config (validate, run, compare, save).
 *
 * Both directions of the form/YAML mirror are debounced, so without this
 * flush an action fired within that window would use stale content: run
 * would silently use the pre-edit config, and save could write a file that
 * differs from what was just run. Returns null when the panel text is not
 * valid YAML, in which case the caller must abort.
 */
async function syncedYaml() {
  if (yamlPending && !(await parseYamlPanel())) return null;
  const r = await api("/api/yaml/dump", { data: cfg });
  return r.ok ? r.yaml : null;
}

/* ------------------------------------------------------------------ *
 * generic widgets
 * ------------------------------------------------------------------ */

/**
 * One labelled input. `help` is plain-language text from the backend
 * (ui/help.py); when present the label gets a "?" marker and the whole
 * field shows the text on hover, so someone who has never seen the config
 * format can still tell what a setting does.
 */
function field(labelText, inputEl, { required = false, wide = false, help = "" } = {}) {
  const lab = el("label", { text: labelText });
  if (required) lab.append(el("span", { class: "req", text: " *" }));
  if (help) lab.append(el("span", { class: "helpmark", text: "?" }));
  const wrap = el("div", { class: "field" + (wide ? " wide" : ""), title: help || null },
    lab, inputEl);
  return wrap;
}

/** Look up help for a plug-in parameter, falling back to generic text. */
function paramHelp(spec, name) {
  if (spec && spec.help) return spec.help;
  return (schema.field_help && schema.field_help[name]) || "";
}

/**
 * A dropdown of plug-ins plus a line explaining the one currently chosen,
 * and a tooltip on each option, so the choice is understandable without
 * knowing the codebase.
 */
function pluginChooser(labelText, registryName, current, onChange, { help = "" } = {}) {
  const registry = schema.registries[registryName];
  const sel = el("select");
  for (const name of Object.keys(registry)) {
    const opt = el("option", { value: name, text: name });
    const text = registry[name].help || registry[name].doc || "";
    if (text) opt.setAttribute("title", text);
    sel.append(opt);
  }
  if (current) sel.value = current;
  const note = el("div", { class: "plugin-note" });
  const describe = () => {
    const entry = registry[sel.value];
    note.textContent = (entry && (entry.help || entry.doc)) || "";
  };
  describe();
  sel.addEventListener("change", () => {
    describe();
    onChange(sel.value);
  });
  const wrap = el("div", { class: "field wide", title: help || null });
  const lab = el("label", { text: labelText });
  if (help) lab.append(el("span", { class: "helpmark", text: "?" }));
  wrap.append(lab, sel, note);
  return wrap;
}

function numInput(value, onChange, { placeholder = "", integer = false } = {}) {
  const input = el("input", {
    type: "number",
    step: integer ? "1" : "any",
    placeholder: placeholder === null ? "" : String(placeholder ?? ""),
  });
  if (value !== undefined && value !== null) input.value = value;
  input.addEventListener("change", () => {
    if (input.value === "") onChange(undefined);
    else onChange(integer ? Math.trunc(Number(input.value)) : Number(input.value));
  });
  return input;
}

function txtInput(value, onChange, { placeholder = "" } = {}) {
  const input = el("input", { type: "text", placeholder });
  if (value !== undefined && value !== null) input.value = value;
  input.addEventListener("change", () => onChange(input.value === "" ? undefined : input.value));
  return input;
}

function boolInput(value, onChange) {
  const input = el("input", { type: "checkbox" });
  input.checked = Boolean(value);
  input.addEventListener("change", () => onChange(input.checked));
  return input;
}

function selInput(options, value, onChange) {
  const sel = el("select");
  for (const opt of options) {
    const [v, label] = Array.isArray(opt) ? opt : [opt, opt];
    sel.append(el("option", { value: v, text: label }));
  }
  if (value !== undefined && value !== null) sel.value = value;
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}

/** Arrays/objects without a dedicated editor: edit as JSON inline. */
function jsonInput(value, onChange) {
  const ta = el("textarea", { class: "json", spellcheck: "false" });
  if (value !== undefined) ta.value = JSON.stringify(value);
  ta.addEventListener("change", () => {
    if (ta.value.trim() === "") {
      ta.style.borderColor = "";
      onChange(undefined);
      return;
    }
    try {
      onChange(JSON.parse(ta.value));
      ta.style.borderColor = "";
    } catch {
      ta.style.borderColor = "var(--bad)";
    }
  });
  return ta;
}

/* ------------------------------------------------------------------ *
 * schema-driven params
 * ------------------------------------------------------------------ */

/**
 * One parameter -> one field, chosen by the schema:
 * dist-capable fields get the fixed/distribution widget, `rate` the
 * rate-pattern widget, composite lists a row editor, the rest scalars.
 * ctx: {registry, pluginType, distFields, rateFields}
 */
function paramWidget(name, spec, obj, ctx) {
  const help = paramHelp(spec, name);
  if (ctx.rateFields && ctx.rateFields.includes(name)) {
    return field(name, specWidget(name, obj, "pattern", "rate_patterns"), {
      required: spec.required,
      wide: true,
      help,
    });
  }
  if (ctx.distFields && ctx.distFields.includes(name)) {
    return field(name, specWidget(name, obj, "dist", "distributions"), {
      required: spec.required,
      wide: true,
      help,
    });
  }
  const comp =
    schema.composites?.[ctx.registry]?.[ctx.pluginType]?.[name] ??
    schema.composites?.[ctx.registry]?.["*"]?.[name];
  if (comp) return listEditor(name, obj, comp, ctx);

  if (name === "node" || spec.type === "node_id") {
    return field(name, selInput(nodeIds(), obj[name], (v) => {
      obj[name] = v;
      dirty();
    }), { required: spec.required, help });
  }

  let input;
  const set = (v) => {
    setOrDelete(obj, name, v);
    dirty();
  };
  switch (spec.type) {
    case "number":
      input = numInput(obj[name], set, { placeholder: spec.default });
      break;
    case "integer":
      input = numInput(obj[name], set, { placeholder: spec.default, integer: true });
      break;
    case "boolean":
      input = boolInput(obj[name] ?? spec.default, set);
      break;
    case "string":
      input = txtInput(obj[name], set, { placeholder: spec.default ?? "" });
      break;
    default:
      input = jsonInput(obj[name], set);
      return field(name, input, { required: spec.required, wide: true, help });
  }
  return field(name, input, { required: spec.required, help });
}

function paramsBlock(schemaParams, obj, ctx) {
  const wrap = el("div", { class: "fields" });
  for (const [name, spec] of Object.entries(schemaParams || {})) {
    wrap.append(paramWidget(name, spec, obj, ctx));
  }
  return wrap;
}

/**
 * The fixed-or-spec widget shared by distributions and rate patterns:
 * a plain number, or `{dist: name, ...}` / `{pattern: name, ...}` with
 * the chosen plugin's own params.
 */
function specWidget(name, obj, key, registryName) {
  const wrap = el("div", { class: "dist-widget" });
  const registry = schema.registries[registryName];

  function currentMode() {
    return obj[name] && typeof obj[name] === "object" && obj[name][key] ? "spec" : "fixed";
  }

  function render() {
    wrap.innerHTML = "";
    const mode = currentMode();
    const modeBar = el("div", { class: "mode" });
    const label = key === "dist" ? "distribution" : "pattern";
    modeBar.append(
      el("button", {
        class: mode === "fixed" ? "on" : "",
        text: "fixed",
        onclick: () => {
          if (currentMode() !== "fixed") {
            delete obj[name];
            render();
            dirty();
          }
        },
      }),
      el("button", {
        class: mode === "spec" ? "on" : "",
        text: label,
        onclick: () => {
          if (currentMode() !== "spec") {
            const first = Object.keys(registry)[0];
            obj[name] = { [key]: first };
            render();
            dirty();
          }
        },
      })
    );
    wrap.append(modeBar);

    if (mode === "fixed") {
      const v = typeof obj[name] === "number" ? obj[name] : undefined;
      wrap.append(
        numInput(v, (nv) => {
          setOrDelete(obj, name, nv);
          dirty();
        })
      );
    } else {
      const spec = obj[name];
      wrap.append(
        pluginChooser(label, registryName, spec[key], (type) => {
          obj[name] = { [key]: type }; // params of the old type don't apply
          render();
          dirty();
        })
      );
      wrap.append(
        paramsBlock(registry[spec[key]]?.params, spec, {
          registry: registryName,
          pluginType: spec[key],
        })
      );
    }
  }

  render();
  return wrap;
}

/** Row editor for list params with an introspected item schema. */
function listEditor(name, obj, itemSchema, ctx) {
  const wrap = el("div", { class: "subcard" });

  function render() {
    wrap.innerHTML = "";
    const head = el("div", { class: "subhead" }, el("b", { text: name }));
    head.append(
      el("button", {
        class: "tiny",
        text: "+ add",
        onclick: () => {
          if (!Array.isArray(obj[name])) obj[name] = [];
          obj[name].push({});
          render();
          dirty();
        },
      })
    );
    wrap.append(head);

    const items = Array.isArray(obj[name]) ? obj[name] : [];
    items.forEach((item, i) => {
      const row = el("div", { class: "subcard" });
      const rowHead = el("div", { class: "subhead" }, el("b", { text: `#${i + 1}` }));
      rowHead.append(
        el("button", {
          class: "tiny danger",
          text: "remove",
          onclick: () => {
            obj[name].splice(i, 1);
            if (obj[name].length === 0) delete obj[name];
            render();
            dirty();
          },
        })
      );
      row.append(rowHead);
      const fieldsWrap = el("div", { class: "fields" });
      for (const [pname, pspec] of Object.entries(itemSchema)) {
        // item fields reuse paramWidget, minus composite recursion
        fieldsWrap.append(
          paramWidget(pname, pspec, item, {
            registry: null,
            pluginType: null,
            distFields: ctx.registry === "generators" ? schema.dist_capable_fields : null,
            rateFields: null,
          })
        );
      }
      row.append(fieldsWrap);
      wrap.append(row);
    });
    if (items.length === 0) {
      wrap.append(el("div", { class: "muted", text: "none" }));
    }
  }

  render();
  return wrap;
}

/* ------------------------------------------------------------------ *
 * build tab sections
 * ------------------------------------------------------------------ */

function renderBuild() {
  const box = $("#build-panels");
  box.innerHTML = "";
  box.append(
    simCard(),
    profilesCard(),
    nodesCard(),
    controllersCard(),
    networkCard(),
    scenariosCard()
  );
}

function simCard() {
  cfg.logging = cfg.logging || {};
  const card = el("div", { class: "card" }, el("h2", { text: "Simulation" }));
  const fields = el("div", { class: "fields" });
  const fh = (n) => (schema.field_help && schema.field_help[n]) || "";
  fields.append(
    field("seed", numInput(cfg.seed, (v) => { setOrDelete(cfg, "seed", v); dirty(); }, { integer: true }), { required: true, help: fh("seed") }),
    field("sim_duration (s)", numInput(cfg.sim_duration, (v) => { setOrDelete(cfg, "sim_duration", v); dirty(); }), { required: true, help: fh("sim_duration") }),
    field("dt (s)", numInput(cfg.dt, (v) => { setOrDelete(cfg, "dt", v); dirty(); }, { placeholder: "0.01" }), { required: true, help: fh("dt") }),
    field("logging output_dir", txtInput(cfg.logging.output_dir, (v) => { setOrDelete(cfg.logging, "output_dir", v); dirty(); }), { required: true, wide: true, help: fh("output_dir") }),
    field("log_state_every (s)", numInput(cfg.logging.log_state_every, (v) => { setOrDelete(cfg.logging, "log_state_every", v); dirty(); }), { help: fh("log_state_every") })
  );
  card.append(fields);
  return card;
}

function nodeFieldWidget(obj, name) {
  const help = (schema.field_help && schema.field_help[name]) || "";
  const set = (v) => {
    setOrDelete(obj, name, v);
    dirty();
  };
  if (name === "accepts_task_types") {
    const input = txtInput(
      Array.isArray(obj[name]) ? obj[name].join(", ") : obj[name],
      (v) => {
        const arr = (v || "").split(",").map((s) => s.trim()).filter(Boolean);
        setOrDelete(obj, name, arr.length ? arr : undefined);
        dirty();
      },
      { placeholder: "all types" }
    );
    return field(name, input, { wide: true, help });
  }
  if (name === "tier") {
    return field(name, txtInput(obj[name], set, { placeholder: "edge" }), { help });
  }
  if (name === "queue_limit") {
    return field(name, numInput(obj[name], set, { integer: true, placeholder: "unlimited" }), { help });
  }
  return field(name, numInput(obj[name], set), { help });
}

function profilesCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Node profiles" }, el("span", { class: "pill", text: "presets" }));
  h.append(
    el("button", {
      class: "tiny",
      text: "+ add profile",
      onclick: () => {
        const name = prompt("Profile name:", "my_profile");
        if (!name) return;
        cfg.node_profiles = cfg.node_profiles || {};
        cfg.node_profiles[name] = { cpu_capacity: 1.0, memory_capacity: 4.0 };
        rebuild();
      },
    })
  );
  card.append(h);
  card.append(
    el("p", {
      class: "hint",
      text:
        "Reusable hardware presets - define a device class once (e.g. a " +
        "Raspberry-Pi-ish sensor_class or a rack edge_server) and any node " +
        "can adopt it via its 'profile' dropdown. Fields set directly on a " +
        "node override its profile.",
    })
  );

  const profiles = cfg.node_profiles || {};
  if (Object.keys(profiles).length === 0) {
    card.append(el("div", { class: "muted", text: "No profiles - nodes define their hardware inline." }));
    return card;
  }
  for (const [name, spec] of Object.entries(profiles)) {
    const sub = el("div", { class: "subcard" });
    const head = el("div", { class: "subhead" }, el("b", { text: name }));
    head.append(
      el("button", {
        class: "tiny danger",
        text: "remove",
        onclick: () => {
          delete cfg.node_profiles[name];
          if (Object.keys(cfg.node_profiles).length === 0) delete cfg.node_profiles;
          rebuild();
        },
      })
    );
    sub.append(head);
    const fields = el("div", { class: "fields" });
    for (const f of schema.node_fields) fields.append(nodeFieldWidget(spec, f));
    sub.append(fields);
    card.append(sub);
  }
  return card;
}

function generatorSection(node) {
  const wrap = el("div", {});
  wrap.append(el("h3", { text: "workload generator" }));
  node.source = node.source || {};
  node.source.generator = node.source.generator || { type: "poisson", rate: 1.0 };
  const gen = node.source.generator;
  const registry = schema.registries.generators;

  wrap.append(
    pluginChooser("generator", "generators", gen.type, (type) => {
      const fresh = { type };
      // carry over params the new generator also understands
      for (const k of Object.keys(gen)) {
        if (k !== "type" && registry[type].params[k] !== undefined) fresh[k] = gen[k];
      }
      node.source.generator = fresh;
      rebuild();
    }, { help: "How this device decides when to create new tasks." })
  );
  wrap.append(
    paramsBlock(registry[gen.type]?.params, gen, {
      registry: "generators",
      pluginType: gen.type,
      distFields: schema.dist_capable_fields,
      rateFields: schema.rate_pattern_fields,
    })
  );
  return wrap;
}

function addNode() {
  cfg.nodes = cfg.nodes || [];
  cfg.nodes.push({
    id: `node_${cfg.nodes.length + 1}`,
    type: "helper",
    cpu_capacity: 1.0,
    memory_capacity: 4.0,
    tier: "edge",
  });
  rebuild();
  return cfg.nodes.length - 1;
}

/**
 * Rename a node everywhere it is referenced: controller `manages`, network
 * link overrides, bandwidth traces, scenario targets, and its saved map
 * position. Renaming used to silently break those references.
 */
function renameNode(oldId, newId) {
  const node = (cfg.nodes || []).find((n) => n.id === oldId);
  if (!node) return;
  node.id = newId;
  for (const ctrl of cfg.controllers || []) {
    if (Array.isArray(ctrl.manages)) {
      ctrl.manages = ctrl.manages.map((m) => (m === oldId ? newId : m));
    }
  }
  for (const link of (cfg.network && cfg.network.links) || []) {
    if (link.from === oldId) link.from = newId;
    if (link.to === oldId) link.to = newId;
  }
  for (const tr of (cfg.network && cfg.network.params && cfg.network.params.traces) || []) {
    if (tr.from === oldId) tr.from = newId;
    if (tr.to === oldId) tr.to = newId;
  }
  for (const scn of cfg.scenarios || []) {
    if (scn.node === oldId) scn.node = newId;
  }
  if (layout[`node:${oldId}`]) {
    layout[`node:${newId}`] = layout[`node:${oldId}`];
    delete layout[`node:${oldId}`];
    localStorage.setItem(layoutKey(), JSON.stringify(layout));
  }
  rebuild();
}

/**
 * Delete a node and every reference to it. Splicing it out of cfg.nodes
 * alone leaves its name in controller `manages` lists, link overrides,
 * traces and scenario targets, which only surfaces later as a confusing
 * "unknown node" error at validation time.
 */
function removeNode(index) {
  const node = (cfg.nodes || [])[index];
  if (!node) return;
  const id = node.id;
  cfg.nodes.splice(index, 1);

  for (const ctrl of cfg.controllers || []) {
    if (Array.isArray(ctrl.manages)) {
      ctrl.manages = ctrl.manages.filter((m) => m !== id);
    }
  }
  if (cfg.network) {
    if (Array.isArray(cfg.network.links)) {
      cfg.network.links = cfg.network.links.filter(
        (l) => l.from !== id && l.to !== id
      );
      if (cfg.network.links.length === 0) delete cfg.network.links;
    }
    const traces = cfg.network.params && cfg.network.params.traces;
    if (Array.isArray(traces)) {
      cfg.network.params.traces = traces.filter(
        (t) => t.from !== id && t.to !== id
      );
      if (cfg.network.params.traces.length === 0) delete cfg.network.params.traces;
    }
  }
  if (Array.isArray(cfg.scenarios)) {
    cfg.scenarios = cfg.scenarios.filter((s) => s.node !== id);
    if (cfg.scenarios.length === 0) delete cfg.scenarios;
  }
  delete layout[`node:${id}`];
  localStorage.setItem(layoutKey(), JSON.stringify(layout));
  closeModal();
  rebuild();
}

function removeController(index) {
  const ctrl = (cfg.controllers || [])[index];
  if (!ctrl) return;
  const id = ctrl.id;
  cfg.controllers.splice(index, 1);
  for (const other of cfg.controllers || []) {
    if (other.parent === id) other.parent = null;
  }
  delete layout[`ctrl:${id}`];
  localStorage.setItem(layoutKey(), JSON.stringify(layout));
  closeModal();
  rebuild();
}

function renameController(oldId, newId) {
  const ctrl = (cfg.controllers || []).find((c) => c.id === oldId);
  if (!ctrl) return;
  ctrl.id = newId;
  for (const other of cfg.controllers || []) {
    if (other.parent === oldId) other.parent = newId;
  }
  if (layout[`ctrl:${oldId}`]) {
    layout[`ctrl:${newId}`] = layout[`ctrl:${oldId}`];
    delete layout[`ctrl:${oldId}`];
    localStorage.setItem(layoutKey(), JSON.stringify(layout));
  }
  rebuild();
}

/** Read-only view of what a node's profile grants, and what it overrides. */
function profileSummary(node) {
  const prof = (cfg.node_profiles || {})[node.profile];
  if (!prof) return null;
  const wrap = el("div", {});
  wrap.append(el("h3", { text: `profile "${node.profile}" - effective hardware` }));
  const t = el("table", { class: "mini" });
  t.append(
    el("tr", {},
      el("th", { text: "field" }),
      el("th", { text: "from profile" }),
      el("th", { text: "this node" }),
      el("th", { text: "effective" }))
  );
  for (const f of schema.node_fields) {
    const fromProfile = prof[f];
    const own = node[f];
    if (fromProfile === undefined && own === undefined) continue;
    const fmt = (v) => (v === undefined ? "-" : Array.isArray(v) ? v.join(", ") : String(v));
    t.append(
      el("tr", {},
        el("td", { text: f }),
        el("td", { text: fmt(fromProfile) }),
        el("td", { text: own === undefined ? "-" : fmt(own) }),
        el("td", {}, el("b", { text: fmt(own !== undefined ? own : fromProfile) })))
    );
  }
  wrap.append(t);
  wrap.append(
    el("p", {
      class: "hint",
      text: "Set a field on the node to override the profile; clear it to fall back.",
    })
  );
  return wrap;
}

/** One node's full editor - used by the Config page and the map popup. */
function nodeSubcard(node, i) {
  {
    const sub = el("div", { class: "subcard" });
    const head = el("div", { class: "subhead" });
    head.append(
      el("b", { text: node.id || `#${i + 1}` }),
      el("span", { class: "pill", text: node.type || "?" }),
      el("button", {
        class: "tiny danger",
        text: "remove",
        onclick: () => removeNode(i),
      })
    );
    sub.append(head);

    const fields = el("div", { class: "fields" });
    fields.append(
      field(
        "id (rename)",
        txtInput(node.id, (v) => {
          if (v && v !== node.id) renameNode(node.id, v);
        }),
        { required: true }
      ),
      field(
        "type",
        selInput(["source", "helper"], node.type, (v) => {
          node.type = v;
          if (v === "helper") delete node.source;
          rebuild();
        }),
        { required: true }
      ),
      field(
        "profile",
        selInput(
          ["", ...Object.keys(cfg.node_profiles || {})].map((p) => [p, p || "(none)"]),
          node.profile || "",
          (v) => {
            setOrDelete(node, "profile", v || undefined);
            rebuild();
          }
        )
      )
    );
    for (const f of schema.node_fields) fields.append(nodeFieldWidget(node, f));
    sub.append(fields);

    const prof = profileSummary(node);
    if (prof) sub.append(prof);

    if (node.type === "source") sub.append(generatorSection(node));
    return sub;
  }
}

function nodesCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Nodes" });
  h.append(el("button", { class: "tiny", text: "+ add node", onclick: addNode }));
  card.append(h);
  (cfg.nodes || []).forEach((node, i) => card.append(nodeSubcard(node, i)));
  return card;
}

function addController() {
  cfg.controllers = cfg.controllers || [];
  cfg.controllers.push({
    id: `ctrl_${cfg.controllers.length + 1}`,
    allocator: { type: "load_aware" },
    manages: [],
    parent: null,
  });
  rebuild();
  return cfg.controllers.length - 1;
}

/** One controller's full editor - used by the Config page and the map popup. */
function controllerSubcard(ctrl, i) {
  {
    const sub = el("div", { class: "subcard" });
    const head = el("div", { class: "subhead" }, el("b", { text: ctrl.id || `#${i + 1}` }));
    head.append(
      el("button", {
        class: "tiny danger",
        text: "remove",
        onclick: () => removeController(i),
      })
    );
    sub.append(head);

    const base = el("div", { class: "fields" });
    base.append(
      field(
        "id (rename)",
        txtInput(ctrl.id, (v) => {
          if (v && v !== ctrl.id) renameController(ctrl.id, v);
        }),
        { required: true }
      ),
      field(
        "scheduling_delay (s)",
        numInput(ctrl.scheduling_delay, (v) => { setOrDelete(ctrl, "scheduling_delay", v); dirty(); }, { placeholder: "0" })
      )
    );
    sub.append(base);

    // allocator - the strategy under study
    sub.append(el("h3", { text: "allocator" }));
    ctrl.allocator = ctrl.allocator || { type: "load_aware" };
    const allocReg = schema.registries.allocators;
    sub.append(
      pluginChooser("strategy", "allocators", ctrl.allocator.type, (type) => {
        ctrl.allocator = { type };
        rebuild();
      }, { help: "The rule this controller uses to pick which node runs each task. This is the thing under study." })
    );
    sub.append(
      paramsBlock(allocReg[ctrl.allocator.type]?.params, ctrl.allocator, {
        registry: "allocators",
        pluginType: ctrl.allocator.type,
      })
    );

    // observability - what the controller knows
    sub.append(el("h3", { text: "observability (what the controller knows)" }));
    const obsReg = schema.registries.observability_models;
    const obsCurrent = ctrl.observability?.type || "perfect";
    sub.append(
      pluginChooser("model", "observability_models", obsCurrent, (type) => {
        if (type === "perfect") delete ctrl.observability; // the default
        else ctrl.observability = { type };
        rebuild();
      }, { help: "How up to date the controller's picture of each node is when it decides." })
    );
    if (ctrl.observability) {
      sub.append(
        paramsBlock(obsReg[ctrl.observability.type]?.params, ctrl.observability, {
          registry: "observability_models",
          pluginType: ctrl.observability.type,
        })
      );
    }

    // manages - checkbox per node
    sub.append(el("h3", { text: "manages" }));
    const manages = el("div", { class: "row" });
    for (const id of nodeIds()) {
      const cb = el("input", { type: "checkbox" });
      cb.checked = (ctrl.manages || []).includes(id);
      cb.addEventListener("change", () => {
        ctrl.manages = ctrl.manages || [];
        if (cb.checked) ctrl.manages.push(id);
        else ctrl.manages = ctrl.manages.filter((m) => m !== id);
        dirty();
      });
      manages.append(el("label", { class: "row" }, cb, id));
    }
    sub.append(manages);
    return sub;
  }
}

function controllersCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Controllers" });
  h.append(el("button", { class: "tiny", text: "+ add controller", onclick: addController }));
  card.append(h);
  (cfg.controllers || []).forEach((ctrl, i) => card.append(controllerSubcard(ctrl, i)));
  return card;
}

function networkCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Network" });
  card.append(h);
  card.append(
    el("p", {
      class: "hint",
      text:
        "Every node can already talk to every other node using the default " +
        "profile below, which sets the bandwidth, base latency and jitter " +
        "they share. You only add a link when one specific pair should differ, " +
        "such as a wired LAN cable between the gateway and the server. On the " +
        "map, a dotted line means the pair uses the default and a solid line " +
        "means it has its own profile.",
    })
  );

  if (!cfg.network) {
    card.append(
      el("div", { class: "row" },
        el("span", { class: "muted", text: "No network block - every transfer is instant." }),
        el("button", {
          class: "tiny",
          text: "+ add network model",
          onclick: () => {
            cfg.network = { type: "fluid_link", default_profile: "wifi" };
            rebuild();
          },
        })
      )
    );
    return card;
  }

  h.append(
    el("button", {
      class: "tiny danger",
      text: "remove (back to instant)",
      onclick: () => {
        delete cfg.network;
        rebuild();
      },
    })
  );

  const net = cfg.network;
  const netReg = schema.registries.network_models;
  const fields = el("div", { class: "fields" });
  card.append(
    pluginChooser("model", "network_models", net.type, (type) => {
      net.type = type;
      rebuild();
    }, { help: "How transfer time between nodes is calculated." })
  );
  fields.append(
    field(
      "default_profile",
      selInput(Object.keys(schema.network_profiles), net.default_profile || "wifi", (v) => {
        net.default_profile = v;
        dirty();
      }),
      { help: (schema.field_help && schema.field_help.default_profile) || "" }
    )
  );
  card.append(fields);

  // model params live under network.params in the YAML
  const paramSchema = netReg[net.type]?.params || {};
  if (Object.keys(paramSchema).length > 0) {
    card.append(el("h3", { text: "model parameters" }));
    net.params = net.params || {};
    card.append(
      paramsBlock(paramSchema, net.params, {
        registry: "network_models",
        pluginType: net.type,
      })
    );
  } else if (net.params && Object.keys(net.params).length === 0) {
    delete net.params;
  }

  // links - per-pair profile overrides
  card.append(el("h3", { text: "links (per-pair overrides; both directions resolve independently)" }));
  const linksWrap = el("div", {});
  function renderLinks() {
    linksWrap.innerHTML = "";
    (net.links || []).forEach((link, i) => {
      const row = el("div", { class: "row" });
      row.append(
        "from",
        selInput(nodeIds(), link.from, (v) => { link.from = v; dirty(); }),
        "to",
        selInput(nodeIds(), link.to, (v) => { link.to = v; dirty(); }),
        "profile",
        selInput(Object.keys(schema.network_profiles), link.profile, (v) => { link.profile = v; dirty(); }),
        el("button", {
          class: "tiny danger",
          text: "remove",
          onclick: () => {
            net.links.splice(i, 1);
            if (net.links.length === 0) delete net.links;
            renderLinks();
            dirty();
          },
        })
      );
      linksWrap.append(row);
    });
    linksWrap.append(
      el("button", {
        class: "tiny",
        text: "+ add link",
        onclick: () => {
          net.links = net.links || [];
          const ids = nodeIds();
          net.links.push({ from: ids[0], to: ids[ids.length - 1], profile: "lan" });
          renderLinks();
          dirty();
        },
      })
    );
  }
  renderLinks();
  card.append(linksWrap);

  card.append(el("h3", { text: "profile overrides (advanced, JSON)" }));
  card.append(
    field(
      "profiles",
      jsonInput(net.profiles, (v) => {
        setOrDelete(net, "profiles", v);
        dirty();
      }),
      { wide: true }
    )
  );
  return card;
}

function scenariosCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Scenarios" }, el("span", { class: "pill", text: "instability" }));
  h.append(
    el("button", {
      class: "tiny",
      text: "+ add scenario",
      onclick: () => {
        cfg.scenarios = cfg.scenarios || [];
        const first = Object.keys(schema.registries.scenarios)[0];
        cfg.scenarios.push({ type: first, node: nodeIds()[0] });
        rebuild();
      },
    })
  );
  card.append(h);

  const scnReg = schema.registries.scenarios;
  (cfg.scenarios || []).forEach((scn, i) => {
    const sub = el("div", { class: "subcard" });
    const head = el("div", { class: "subhead" }, el("b", { text: scn.type }));
    head.append(
      el("button", {
        class: "tiny danger",
        text: "remove",
        onclick: () => {
          cfg.scenarios.splice(i, 1);
          if (cfg.scenarios.length === 0) delete cfg.scenarios;
          rebuild();
        },
      })
    );
    sub.append(head);
    sub.append(
      pluginChooser("type", "scenarios", scn.type, (type) => {
        cfg.scenarios[i] = { type, node: scn.node };
        rebuild();
      }, { help: "What goes wrong during the run, and when." })
    );
    sub.append(
      paramsBlock(scnReg[scn.type]?.params, scn, {
        registry: "scenarios",
        pluginType: scn.type,
      })
    );
    card.append(sub);
  });
  if (!cfg.scenarios || cfg.scenarios.length === 0) {
    card.append(
      el("div", {
        class: "muted",
        text:
          "No scenarios. Nothing goes wrong during the run: no node fails and " +
          "no node becomes less reliable. Add one to test how the allocator " +
          "copes when things break.",
      })
    );
  }
  return card;
}

/* ------------------------------------------------------------------ *
 * modal - click a device on the map, configure it in a small window
 * ------------------------------------------------------------------ */

let modalState = null; // {title: fn|string, render: fn} while open

function openModal(title, render) {
  modalState = { title, render };
  $("#modal").hidden = false;
  refreshModal();
}

function closeModal() {
  modalState = null;
  $("#modal").hidden = true;
}

/** Re-render the open modal after cfg changes; closes if its subject vanished. */
function refreshModal() {
  if (!modalState) return;
  const content = modalState.render();
  if (!content) {
    closeModal();
    return;
  }
  $("#modal-title").textContent =
    typeof modalState.title === "function" ? modalState.title() : modalState.title;
  const body = $("#modal-body");
  body.innerHTML = "";
  body.append(content);
}

/** Track a list element across re-renders: by index while the list length is
 * unchanged (so editing its id keeps the popup open), by id after adds/removes
 * (so the popup follows its subject or closes when it's deleted). */
function listRef(list, index) {
  return { index, id: list[index]?.id, count: list.length };
}

function resolveRef(list, ref) {
  if (list.length !== ref.count) {
    ref.count = list.length;
    ref.index = list.findIndex((x) => x.id === ref.id);
    return ref.index >= 0 ? list[ref.index] : null;
  }
  const item = list[ref.index];
  if (item) ref.id = item.id;
  return item || null;
}

function openNodeModal(index) {
  const ref = listRef(cfg.nodes || [], index);
  openModal(
    () => `node · ${ref.id}`,
    () => {
      const node = resolveRef(cfg.nodes || [], ref);
      return node ? nodeSubcard(node, ref.index) : null;
    }
  );
}

function openControllerModal(index) {
  const ref = listRef(cfg.controllers || [], index);
  openModal(
    () => `controller · ${ref.id}`,
    () => {
      const ctrl = resolveRef(cfg.controllers || [], ref);
      return ctrl ? controllerSubcard(ctrl, ref.index) : null;
    }
  );
}

/** Create/update/remove the directional link override for (from -> to). */
function setLinkProfile(from, to, profile) {
  cfg.network.links = cfg.network.links || [];
  const i = cfg.network.links.findIndex((l) => l.from === from && l.to === to);
  if (profile === "") {
    if (i >= 0) cfg.network.links.splice(i, 1);
    if (cfg.network.links.length === 0) delete cfg.network.links;
  } else if (i >= 0) {
    cfg.network.links[i].profile = profile;
  } else {
    cfg.network.links.push({ from, to, profile });
  }
  dirty();
}

function linkOptions() {
  return [["", `(default: ${cfg.network.default_profile || "wifi"})`]]
    .concat(Object.keys(schema.network_profiles).map((p) => [p, p]))
    .concat([["none", "no connection (cannot send)"]]);
}

function linkProfileField(labelText, from, to) {
  const current = (cfg.network.links || []).find(
    (l) => l.from === from && l.to === to
  );
  return field(
    labelText,
    selInput(linkOptions(), (current && current.profile) || "", (v) =>
      setLinkProfile(from, to, v)
    ),
    {
      wide: true,
      help:
        "Pick a link profile, leave it on the default, or choose 'no " +
        "connection' to cut this route entirely. A node that cannot be " +
        "reached is never chosen, and a task with nowhere to go is lost.",
    }
  );
}

/** Click a line on the map -> edit that pair's link. One dropdown sets BOTH
 * directions (the normal case). The model resolves each direction on its own,
 * so an "asymmetric" toggle reveals per-direction control when wanted. */
function openLinkModal(a, b) {
  const links0 = (cfg.network && cfg.network.links) || [];
  const f0 = links0.find((l) => l.from === a && l.to === b);
  const r0 = links0.find((l) => l.from === b && l.to === a);
  let asym = ((f0 && f0.profile) || "") !== ((r0 && r0.profile) || "");

  openModal(`link · ${a} ⇄ ${b}`, () => {
    if (!cfg.network) return null;
    const wrap = el("div", {});
    const fields = el("div", { class: "fields" });
    if (!asym) {
      const cur = (cfg.network.links || []).find((l) => l.from === a && l.to === b);
      fields.append(
        field(
          "link profile (both directions)",
          selInput(linkOptions(), (cur && cur.profile) || "", (v) => {
            setLinkProfile(a, b, v);
            setLinkProfile(b, a, v);
          }),
          {
            wide: true,
            help:
              "Pick a link profile, leave it on the default, or choose 'no " +
              "connection' to cut these two off from each other entirely.",
          }
        )
      );
    } else {
      fields.append(
        linkProfileField(`${a} → ${b}`, a, b),
        linkProfileField(`${b} → ${a}`, b, a)
      );
    }
    wrap.append(fields);
    wrap.append(
      el("button", {
        class: "tiny",
        text: asym ? "use the same profile both ways" : "asymmetric (per direction)...",
        onclick: () => {
          if (asym) {
            // Collapsing to symmetric must actually equalise the directions,
            // otherwise the map keeps showing "lan / wifi".
            const fwd = (cfg.network.links || []).find((l) => l.from === a && l.to === b);
            const profile = (fwd && fwd.profile) || "";
            setLinkProfile(a, b, profile);
            setLinkProfile(b, a, profile);
          }
          asym = !asym;
          refreshModal();
        },
      })
    );
    wrap.append(
      el("p", {
        class: "hint",
        text: asym
          ? "Each direction resolves independently - fast one way, slow the " +
            "other is allowed. The map labels such pairs \"lan / wifi\"."
          : "Sets the same profile for both directions - the usual case.",
      })
    );
    return wrap;
  });
}

/**
 * Turn geometry on or off, and set how much ground the map covers.
 *
 * Without locations the map is a diagram and dragging is cosmetic. With
 * them the map is a plan: a node's position IS its position, distances
 * appear on the lines, and a wireless link dragged past its range dies.
 */
function openGeometryModal() {
  openModal("distance and positions", () => {
    const wrap = el("div", {});
    const on = anyNodeHasLocation();

    wrap.append(
      el("p", {
        class: "hint",
        text: on
          ? "Devices have real positions. Drag one on the map and its distance " +
            "to everything else changes with it."
          : "Devices have no positions yet, so the map is only a diagram and " +
            "dragging rearranges it without meaning anything.",
      })
    );

    const fields = el("div", { class: "fields" });
    fields.append(
      field(
        "map covers (km across)",
        numInput(mapExtentKm, (v) => {
          if (!v || v <= 0) return;
          mapExtentKm = v;
          localStorage.setItem("simui-map-extent", String(v));
          rebuild();
        }),
        {
          help:
            "How much ground the full width of the map represents. Smaller " +
            "means finer control over short distances.",
        }
      )
    );
    wrap.append(fields);

    wrap.append(
      el("button", {
        class: "tiny",
        text: on ? "remove all positions" : "give every device a position",
        onclick: () => {
          if (on) {
            for (const n of cfg.nodes || []) delete n.location;
          } else {
            // Seed from wherever things already sit on the map, so turning
            // this on does not scatter the layout.
            defaultPositions();
            for (const n of cfg.nodes || []) {
              n.location = pointToKm(layout[`node:${n.id}`] || { x: 400, y: 240 });
            }
          }
          rebuild();
        },
      })
    );

    if (on) {
      wrap.append(el("h3", { text: "how far each medium carries" }));
      const t = el("table", { class: "mini" });
      t.append(el("tr", {}, el("th", { text: "profile" }), el("th", { text: "range" })));
      for (const [name, r] of Object.entries(schema.profile_ranges || {})) {
        t.append(
          el("tr", {},
            el("td", { text: name }),
            el("td", { text: r === null ? "unlimited (wired)" : fmtKm(r) }))
        );
      }
      wrap.append(t);
      wrap.append(
        el("p", {
          class: "hint",
          text:
            "A pair further apart than this cannot reach each other at all, " +
            "so that node stops being a candidate. Override a range under " +
            "network... -> profiles, e.g. {\"wifi\": {\"max_range_km\": 0.3}}.",
        })
      );
    }
    return wrap;
  });
}

function openFromKey(key) {
  if (key.startsWith("node:")) {
    const i = (cfg.nodes || []).findIndex((n) => n.id === key.slice(5));
    if (i >= 0) openNodeModal(i);
  } else if (key.startsWith("ctrl:")) {
    const i = (cfg.controllers || []).findIndex((c) => c.id === key.slice(5));
    if (i >= 0) openControllerModal(i);
  }
}

/* ------------------------------------------------------------------ *
 * topology sketch (cosmetic; layout kept in localStorage)
 * ------------------------------------------------------------------ */

function layoutKey() {
  return `simui-layout:${currentName}`;
}

function loadLayout() {
  try {
    layout = JSON.parse(localStorage.getItem(layoutKey())) || {};
  } catch {
    layout = {};
  }
}

/* ---- geometry: km <-> map units --------------------------------------- *
 * The map is 800 x 480 units. `mapExtentKm` is how many kilometres the full
 * width represents, so a node's `location` (in km) has a unique place on the
 * map and dragging it writes a real distance back into the config.
 * Nodes without a location keep the old cosmetic behaviour.
 */
let mapExtentKm = Number(localStorage.getItem("simui-map-extent") || 1.0);
const MAP_W = 800, MAP_H = 480;

function kmPerUnit() {
  return mapExtentKm / MAP_W;
}

function kmToPoint(loc) {
  return { x: loc[0] / kmPerUnit(), y: MAP_H / 2 - loc[1] / kmPerUnit() };
}

function pointToKm(p) {
  return [
    +(p.x * kmPerUnit()).toFixed(4),
    +((MAP_H / 2 - p.y) * kmPerUnit()).toFixed(4),
  ];
}

/** Distances at edge scale read better in metres. */
function fmtKm(km) {
  if (km === null || km === undefined) return "";
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(2)} km`;
}

function anyNodeHasLocation() {
  return (cfg.nodes || []).some((n) => Array.isArray(n.location));
}

/** Straight-line distance in km, or null when either end has no location. */
function pairDistanceKm(aId, bId) {
  const a = (cfg.nodes || []).find((n) => n.id === aId);
  const b = (cfg.nodes || []).find((n) => n.id === bId);
  if (!a || !b || !Array.isArray(a.location) || !Array.isArray(b.location)) return null;
  return Math.hypot(a.location[0] - b.location[0], a.location[1] - b.location[1]);
}

/** How far a profile carries, in km; null means unlimited. */
function profileRangeKm(name) {
  const r = schema.profile_ranges || {};
  return r[name] === undefined ? null : r[name];
}

function defaultPositions() {
  const nodes = cfg.nodes || [];
  const ctrls = cfg.controllers || [];
  nodes.forEach((n, i) => {
    const key = `node:${n.id}`;
    // A real location wins: the map then shows actual geometry.
    if (Array.isArray(n.location)) {
      layout[key] = kmToPoint(n.location);
      return;
    }
    if (!layout[key]) {
      const a = (2 * Math.PI * i) / Math.max(nodes.length, 1) - Math.PI / 2;
      layout[key] = { x: 400 + 300 * Math.cos(a), y: 190 + 130 * Math.sin(a) };
    }
  });
  ctrls.forEach((c, i) => {
    const key = `ctrl:${c.id}`;
    if (!layout[key]) {
      layout[key] = { x: 150 + (500 * (i + 1)) / (ctrls.length + 1), y: 420 };
    }
  });
}

/**
 * Draw every pair's connection, identically on the build map and the replay
 * map: solid = explicit override, dotted = the implicit default-profile
 * connection (wifi etc.) every other pair already uses. Lines are clickable
 * when an onClick is supplied.
 */
function drawLinks(svg, pos, { nodes, links, defaultProfile, configured, onClick, distanceOf }) {
  if (!configured) return;
  const drawMesh = nodes.length <= 6; // implicit mesh only while readable
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i].id;
      const b = nodes[j].id;
      const fwd = links.find((l) => l.from === a && l.to === b);
      const rev = links.find((l) => l.from === b && l.to === a);
      const overridden = Boolean(fwd || rev);
      if (!overridden && !drawMesh) continue;
      const pa = pos(`node:${a}`);
      const pb = pos(`node:${b}`);
      if (!pa || !pb) continue;

      const isCut = (l) => l && (l.profile === "none" || l.reachable === false);
      const cutF = isCut(fwd);
      const cutR = isCut(rev);
      const severed = cutF && cutR; // no route either way

      // Geometry: how far apart are they, and does the medium carry that far?
      const km = distanceOf ? distanceOf(a, b) : null;
      const profF = (fwd && fwd.profile) || defaultProfile;
      const profR = (rev && rev.profile) || defaultProfile;
      const range = profileRangeKm(profF);
      const outOfRange =
        !severed && km !== null && range !== null && km > range;

      svg.append(
        svgEl("line", {
          class: severed || outOfRange
            ? "topo-link-cut"
            : overridden
            ? "topo-link"
            : "topo-link-default",
          x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y,
        })
      );
      let label;
      if (severed) {
        label = "no connection";
      } else if (outOfRange) {
        label = `${profF} out of range (${fmtKm(km)} > ${fmtKm(range)})`;
      } else if (cutF || cutR) {
        label = cutF ? `${b} -> ${a} only` : `${a} -> ${b} only`;
      } else {
        label = profF === profR ? profF : `${profF} / ${profR}`;
        if (km !== null) label += ` · ${fmtKm(km)}`;
      }
      svg.append(
        svgEl("text", {
          class: "topo-link-label",
          x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2 - 5,
          "text-anchor": "middle", text: label,
        })
      );
      if (onClick) {
        const hit = svgEl("line", {
          class: "topo-hit", x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y,
        });
        const tip = [`${a} <-> ${b}`, label];
        if (km !== null && range !== null) {
          tip.push(`${profF} carries ${fmtKm(range)}`);
        }
        hit.append(svgEl("title", { text: tip.join(" | ") }));
        hit.addEventListener("click", () => onClick(a, b));
        svg.append(hit);
      }
    }
  }
}

function drawTopology() {
  const svg = $("#topology");
  svg.innerHTML = "";
  if (!cfg) return;
  defaultPositions();

  const pos = (key) => layout[key] || { x: 400, y: 200 };

  // manages (dashed) under everything
  for (const ctrl of cfg.controllers || []) {
    const c = pos(`ctrl:${ctrl.id}`);
    for (const id of ctrl.manages || []) {
      const n = pos(`node:${id}`);
      svg.append(svgEl("line", { class: "topo-manage", x1: c.x, y1: c.y, x2: n.x, y2: n.y }));
    }
  }
  drawLinks(svg, pos, {
    nodes: cfg.nodes || [],
    links: cfg.network?.links || [],
    defaultProfile: cfg.network?.default_profile || "wifi",
    configured: Boolean(cfg.network),
    onClick: openLinkModal,
    distanceOf: pairDistanceKm,
  });

  const draggable = [];
  for (const node of cfg.nodes || []) {
    const key = `node:${node.id}`;
    const p = pos(key);
    const g = svgEl("g", { class: "topo-node" });
    g.append(
      svgEl("circle", {
        cx: p.x,
        cy: p.y,
        r: 16,
        fill: node.type === "source" ? "#dbe7ff" : "#dcf5e4",
        stroke: node.type === "source" ? "#2563eb" : "#15803d",
        "stroke-width": 2,
      }),
      svgEl("text", { x: p.x, y: p.y + 30, "text-anchor": "middle", text: node.id })
    );
    svg.append(g);
    draggable.push([g, key]);
  }
  for (const ctrl of cfg.controllers || []) {
    const key = `ctrl:${ctrl.id}`;
    const p = pos(key);
    const g = svgEl("g", { class: "topo-node" });
    g.append(
      svgEl("rect", {
        x: p.x - 12,
        y: p.y - 12,
        width: 24,
        height: 24,
        rx: 4,
        fill: "#fef3c7",
        stroke: "#b45309",
        "stroke-width": 2,
        transform: `rotate(45 ${p.x} ${p.y})`,
      }),
      svgEl("text", { x: p.x, y: p.y + 32, "text-anchor": "middle", text: ctrl.id })
    );
    svg.append(g);
    draggable.push([g, key]);
  }

  // drag to arrange, click (without dragging) to open the config popup
  for (const [g, key] of draggable) {
    g.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      const start = { x: ev.clientX, y: ev.clientY };
      let moved = false;
      const svgPoint = (e) => {
        const r = svg.getBoundingClientRect();
        return {
          x: ((e.clientX - r.left) / r.width) * 800,
          y: ((e.clientY - r.top) / r.height) * 480,
        };
      };
      const move = (e) => {
        if (Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 5) {
          moved = true;
        }
        if (moved) {
          layout[key] = svgPoint(e);
          // Dragging a located node moves it for real: the config's
          // `location` is the source of truth, so distances and range
          // limits follow the map.
          if (key.startsWith("node:")) {
            const node = (cfg.nodes || []).find((n) => n.id === key.slice(5));
            if (node && Array.isArray(node.location)) {
              node.location = pointToKm(layout[key]);
            }
          }
          drawTopology();
        }
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        if (moved) {
          localStorage.setItem(layoutKey(), JSON.stringify(layout));
          if (key.startsWith("node:")) dirty(); // a real move changes the config
        } else {
          openFromKey(key);
        }
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }
}

/* ------------------------------------------------------------------ *
 * run tab
 * ------------------------------------------------------------------ */

function yamlSyncFailed(out) {
  out.innerHTML = "";
  out.append(
    resultBox(false, "Fix the YAML panel first", "The text there is not valid YAML.")
  );
}

function resultBox(ok, title, detail) {
  const box = el("div", { class: `result-box ${ok ? "ok" : "bad"}` }, el("b", { text: title }));
  if (detail) box.append(el("pre", { text: detail }));
  return box;
}

async function doValidate() {
  const out = $("#validate-result");
  out.innerHTML = "";
  const text = await syncedYaml();
  if (text === null) return yamlSyncFailed(out);
  const r = await api("/api/validate", { yaml: text });
  if (r.ok) {
    out.append(
      resultBox(
        true,
        "Config is valid",
        `${r.nodes} nodes, ${r.controllers} controller(s), ${r.scenarios} scenario(s), ` +
          `${r.sim_duration} s simulated, seed ${r.seed}`
      )
    );
  } else {
    out.append(resultBox(false, `Invalid (${r.stage || "request"})`, r.error));
  }
}

function statCard(k, v) {
  return el("div", { class: "stat" }, el("div", { class: "v", text: v }), el("div", { class: "k", text: k }));
}

function miniTable(title, rows) {
  const t = el("table", { class: "mini" });
  t.append(el("tr", {}, el("th", { text: title }), el("th", { text: "" })));
  for (const [k, v] of rows) t.append(el("tr", {}, el("td", { text: k }), el("td", { text: v })));
  return t;
}

async function doRun() {
  const busy = $("#run-busy");
  const out = $("#run-result");
  const text = await syncedYaml();
  if (text === null) return yamlSyncFailed(out);
  busy.hidden = false;
  try {
    const r = await api("/api/run", { yaml: text });
    out.innerHTML = "";
    if (!r.ok) {
      out.append(resultBox(false, `Run failed (${r.stage || "request"})`, r.error));
      return;
    }
    const s = r.summary;
    out.append(resultBox(true, `Run ${r.run_id} finished`, `logs: ${r.log_dir}`));
    out.append(
      el("div", { class: "row" },
        el("button", {
          text: "⤓ download logs (.zip)",
          onclick: () => window.open(`/api/run/${r.run_id}/download`, "_blank"),
        }),
        el("button", {
          text: "⤓ summary CSV",
          onclick: () => exportSummaryCsv(r.run_id, s),
        }),
        el("span", { class: "muted", text: "zip = allocation_log, state_log, config_used, seed" }))
    );
    const cards = el("div", { class: "summary-cards" });
    cards.append(
      statCard("tasks generated", s.tasks_generated),
      statCard("SUCCEEDED", `${s.success_rate.toFixed(1)}%`),
      statCard("lost", s.tasks_lost),
      statCard("late", s.tasks_late),
      statCard("unfinished", s.tasks_unfinished),
      statCard("median latency", `${s.median_latency_s.toFixed(3)} s`),
      statCard("p95 latency", `${s.p95_latency_s.toFixed(3)} s`),
      statCard("offloaded", `${s.offload_ratio.toFixed(0)}%`),
      statCard("load fairness", s.load_fairness.toFixed(3)),
      statCard("simulated", `${s.final_time.toFixed(0)} s`),
      statCard("wall clock", `${s.wall_seconds.toFixed(3)} s`)
    );
    out.append(cards);
    out.append(
      el("div", {
        class: "hint",
        text:
          `Succeeded = ran to completion, result returned to the source, and ` +
          `beat the deadline: ${s.tasks_succeeded} of ${s.tasks_generated}. ` +
          `Everything else counts as a failure.`,
      })
    );
    out.append(miniTable("tasks per node", Object.entries(s.placement)));
    const perNode = el("table", { class: "mini" });
    perNode.append(
      el("tr", {},
        el("th", { text: "node" }), el("th", { text: "avg queue" }),
        el("th", { text: "max queue" }), el("th", { text: "avg cpu" }),
        el("th", { text: "avg memory" }))
    );
    for (const n of Object.keys(s.avg_queue)) {
      perNode.append(
        el("tr", {},
          el("td", { text: n }),
          el("td", { text: s.avg_queue[n].toFixed(2) }),
          el("td", { text: s.max_queue[n] }),
          el("td", { text: `${(s.avg_cpu_utilisation[n] * 100).toFixed(0)}%` }),
          el("td", { text: `${(s.avg_memory_utilisation[n] * 100).toFixed(0)}%` }))
      );
    }
    out.append(perNode);
    if (s.tasks_late > 0) {
      out.append(
        el("div", { class: "hint",
          text: `Late tasks missed by ${s.mean_lateness_s.toFixed(2)} s on ` +
                `average, worst ${s.max_lateness_s.toFixed(2)} s.` })
      );
    }
    refreshRunsList();
  } finally {
    busy.hidden = true;
  }
}

function downloadCsv(filename, rows) {
  const blob = new Blob([rows.join("\n")], { type: "text/csv" });
  const a = el("a", { href: URL.createObjectURL(blob), download: filename });
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportSummaryCsv(runId, s) {
  const rows = ["metric,value"];
  for (const [k, v] of Object.entries(s)) {
    if (v === null || v === undefined) continue;
    if (typeof v === "object") {
      for (const [k2, v2] of Object.entries(v)) rows.push(`${k}.${k2},${v2}`);
    } else {
      rows.push(`${k},${v}`);
    }
  }
  downloadCsv(`run_${runId}_summary.csv`, rows);
}

async function refreshRunsList() {
  const r = await api("/api/runs");
  const box = $("#runs-list");
  const entries = Object.entries(r.runs || {});
  if (entries.length === 0) {
    box.textContent = "none yet";
    return;
  }
  box.innerHTML = "";
  const t = el("table", { class: "mini" });
  t.append(
    el("tr", {},
      el("th", { text: "run" }), el("th", { text: "tasks" }),
      el("th", { text: "success %" }), el("th", { text: "mean latency" }))
  );
  for (const [id, entry] of entries.reverse()) {
    const s = entry.summary || {};
    t.append(
      el("tr", {},
        el("td", { text: id }),
        el("td", { text: s.tasks_generated ?? "…" }),
        el("td", { text: s.success_rate !== undefined ? s.success_rate.toFixed(1) : "…" }),
        el("td", { text: s.mean_latency_s !== undefined ? `${s.mean_latency_s.toFixed(3)} s` : "…" }))
    );
  }
  box.append(t);
}

/* ------------------------------------------------------------------ *
 * replay tab - animate a finished run from its timeline
 * ------------------------------------------------------------------ */

const MIN_VISIBLE_TRANSFER = 0.15; // sim-seconds; visual stretch only

// Deliberately well-separated hues: adjacent colours are hard to tell apart
// on fast-moving markers, so control traffic (orange), payloads (blue),
// results (green), births (pink) and monitoring (slate) never share a family.
const C = {
  born: "#ec4899", // pink ring
  control: "#f97316", // orange: report out, instruction back
  payload: "#2563eb", // blue
  result: "#16a34a", // green
  lost: "#dc2626", // red
  heartbeat: "#64748b", // slate
  recovering: "#0891b2", // cyan outline
};

const replay = {
  data: null,
  t: 0,
  playing: false,
  raf: null,
  lastTs: null,
  pos: {}, // id -> {x, y} on the replay canvas
  selected: null, // {kind: "node"|"ctrl", id} shown in the inspector
};

async function replayRefreshRuns() {
  const r = await api("/api/runs");
  const sel = $("#replay-run");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const id of Object.keys(r.runs || {})) sel.append(el("option", { value: id, text: id }));
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

async function replayLoad(runId) {
  if (!runId) return;
  const r = await api(`/api/run/${runId}/timeline`);
  if (!r.ok) {
    $("#replay-time").textContent = r.error;
    return;
  }
  replayStop();
  replay.data = r.timeline;
  replay.t = 0;

  // reuse the Build tab's dragged positions where node ids match
  replay.pos = {};
  const nodes = replay.data.nodes;
  nodes.forEach((n, i) => {
    const saved = layout[`node:${n.id}`];
    const a = (2 * Math.PI * i) / Math.max(nodes.length, 1) - Math.PI / 2;
    replay.pos[n.id] = saved
      ? { x: saved.x, y: saved.y }
      : { x: 400 + 300 * Math.cos(a), y: 190 + 130 * Math.sin(a) };
  });
  (replay.data.controllers || []).forEach((c, i) => {
    const saved = layout[`ctrl:${c.id}`];
    replay.pos[`ctrl:${c.id}`] = saved || {
      x: 150 + (500 * (i + 1)) / (replay.data.controllers.length + 1),
      y: 420,
    };
  });
  // which controller speaks for each source (for dispatch flashes)
  replay.ctrlOf = {};
  for (const c of replay.data.controllers || []) {
    for (const id of c.manages || []) replay.ctrlOf[id] = c;
  }
  renderReplay(0);
}

/** Latest state row at or before t for a node: [t, queue, active, rel, failure]. */
function stateAt(nodeId, t) {
  const rows = replay.data.states[nodeId];
  if (!rows || rows.length === 0 || rows[0][0] > t) return null;
  let lo = 0;
  let hi = rows.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (rows[mid][0] <= t) lo = mid;
    else hi = mid - 1;
  }
  return rows[lo];
}

function lerp(a, b, p) {
  return { x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p };
}

function renderReplay(t) {
  const svg = $("#replay-svg");
  svg.innerHTML = "";
  if (!replay.data) return;
  const data = replay.data;

  // controller context (dim, static)
  for (const ctrl of data.controllers || []) {
    const c = replay.pos[`ctrl:${ctrl.id}`];
    for (const id of ctrl.manages || []) {
      const n = replay.pos[id];
      if (n) svg.append(svgEl("line", { class: "topo-manage", x1: c.x, y1: c.y, x2: n.x, y2: n.y }));
    }
    const cg = svgEl("g", { class: "topo-node" });
    if (replay.selected && replay.selected.kind === "ctrl" && replay.selected.id === ctrl.id) {
      cg.append(svgEl("circle", {
        cx: c.x, cy: c.y, r: 24, fill: "none",
        stroke: "#2563eb", "stroke-width": 2, "stroke-dasharray": "4 3",
      }));
    }
    cg.append(
      svgEl("rect", {
        x: c.x - 12, y: c.y - 12, width: 24, height: 24, rx: 3,
        fill: "#fef3c7", stroke: "#b45309", "stroke-width": 2,
        transform: `rotate(45 ${c.x} ${c.y})`,
      }),
      svgEl("text", { x: c.x, y: c.y + 34, "text-anchor": "middle", "font-size": 13, fill: "#1d2733", text: ctrl.id })
    );
    cg.addEventListener("click", () => {
      replay.selected = { kind: "ctrl", id: ctrl.id };
      renderReplay(replay.t);
    });
    svg.append(cg);
  }

  // same connection picture as the build map (wifi/default pairs included)
  drawLinks(svg, (key) => replay.pos[key.slice(5)], {
    nodes: data.nodes,
    links: data.links || [],
    defaultProfile: data.network?.default_profile || "wifi",
    configured: data.network ? data.network.configured : (data.links || []).length > 0,
    onClick: null, // topology is fixed during replay
  });

  // nodes with live state
  for (const node of data.nodes) {
    const p = replay.pos[node.id];
    const st = stateAt(node.id, t);
    const failure = st ? st[4] : "normal";
    const queue = st ? st[1] : 0;
    const active = st ? st[2] : 0;

    let fill = node.type === "source" ? "#dbe7ff" : "#dcf5e4";
    let stroke = node.type === "source" ? "#2563eb" : "#15803d";
    if (failure === "failed") { fill = "#e5e7eb"; stroke = "#6b7280"; }
    else if (failure === "recovering") stroke = C.recovering;

    const g = svgEl("g", { class: "topo-node" });
    if (replay.selected && replay.selected.kind === "node" && replay.selected.id === node.id) {
      g.append(svgEl("circle", {
        cx: p.x, cy: p.y, r: 26, fill: "none",
        stroke: "#2563eb", "stroke-width": 2, "stroke-dasharray": "4 3",
      }));
    }
    g.append(
      svgEl("circle", { cx: p.x, cy: p.y, r: 20, fill, stroke, "stroke-width": failure === "recovering" ? 4 : 2.5 }),
      svgEl("text", { x: p.x, y: p.y + 2, "text-anchor": "middle", "font-size": 13, fill: "#1d2733", text: queue }),
      svgEl("text", {
        x: p.x, y: p.y + 14, "text-anchor": "middle", "font-size": 9,
        fill: "#6b7684", text: active > 0 ? `▶${active}` : "",
      }),
      svgEl("text", { x: p.x, y: p.y + 38, "text-anchor": "middle", "font-size": 13, fill: "#1d2733", text: node.id })
    );
    g.addEventListener("click", () => {
      replay.selected = { kind: "node", id: node.id };
      renderReplay(replay.t);
    });
    svg.append(g);
    // queue bar under the node
    if (queue > 0) {
      svg.append(
        svgEl("rect", {
          x: p.x - 15, y: p.y + 36, height: 4,
          width: Math.min(queue, 15) * 2, rx: 2, fill: stroke,
        })
      );
    }
  }

  // heartbeat reports travelling node -> controller (the controller's
  // knowledge refreshes when these land; failed nodes go silent)
  for (const ctrl of data.controllers || []) {
    const obs = ctrl.observability || {};
    if (obs.type !== "heartbeat" || !obs.interval) continue;
    const cp = replay.pos[`ctrl:${ctrl.id}`];
    if (!cp) continue;
    const dur = Math.max(obs.report_delay || 0, 0.3); // visual stretch
    const k = Math.floor(t / obs.interval);
    const since = t - k * obs.interval;
    if (k > 0 && since <= dur) {
      for (const id of ctrl.manages || []) {
        const st = stateAt(id, t);
        if (st && st[4] === "failed") continue; // silent while down
        const np = replay.pos[id];
        if (!np) continue;
        const p = lerp(np, cp, since / dur);
        svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 4, fill: C.heartbeat }));
      }
    }
  }

  // tasks in flight
  let generated = 0, completedSoFar = 0, metSoFar = 0, lostSoFar = 0;
  for (const task of data.tasks) {
    if (task.decision > t) break; // tasks are sorted by decision time
    generated += 1;

    // birth pulse at the task's real generation time (the controller only
    // decides at the next tick boundary, hence the separate dispatch marker)
    const born = task.arrival ?? task.decision;
    if (t >= born && t - born <= 0.35) {
      const p = replay.pos[task.source];
      if (p) {
        svg.append(svgEl("circle", {
          cx: p.x, cy: p.y, r: 6 + 40 * (t - born),
          fill: "none", stroke: C.born, "stroke-width": 3,
          opacity: 1 - (t - born) / 0.35,
        }));
      }
    }
    // Control exchange, as a round trip with the SOURCE - not the executor.
    // The source reports its new task, the controller decides, and the
    // instruction ("send it to X") comes back to the source, which is what
    // actually transmits the payload. A lost task gets no instruction back.
    const dCtrl = replay.ctrlOf ? replay.ctrlOf[task.source] : null;
    if (dCtrl) {
      const cp = replay.pos[`ctrl:${dCtrl.id}`];
      const sp = replay.pos[task.source];
      const ctrlEnd = Math.max(task.t_start ?? task.decision, born + 0.12);
      if (cp && sp && t >= born && t < ctrlEnd) {
        const half = born + (ctrlEnd - born) / 2;
        if (t < half) {
          const p = lerp(sp, cp, (t - born) / (half - born));
          svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 5, fill: C.control }));
        } else if (!task.lost) {
          const p = lerp(cp, sp, (t - half) / (ctrlEnd - half));
          svg.append(svgEl("rect", {
            x: p.x - 5.5, y: p.y - 5.5, width: 11, height: 11, fill: C.control,
            transform: `rotate(45 ${p.x} ${p.y})`,
          }));
        }
      }
    }
    if (task.lost) {
      lostSoFar += 1;
      if (t - task.decision <= 1.0) {
        const p = replay.pos[task.source];
        if (p) svg.append(svgEl("text", {
          x: p.x + 18, y: p.y - 16, "font-size": 22, fill: C.lost, text: "✗",
        }));
      }
      continue;
    }
    const src = replay.pos[task.source];
    const dst = replay.pos[task.node];
    const remote = task.node !== task.source;
    const end = task.ret ?? task.done;
    const hasReturn = remote && task.ret !== null && task.done !== null;
    // Real transfers are milliseconds, so they are stretched to stay visible.
    // The arrival flash must wait for the *stretched* leg to land, otherwise
    // the dot is deleted part-way down the line.
    const retEnd = hasReturn
      ? Math.max(task.ret, task.done + MIN_VISIBLE_TRANSFER)
      : end;

    if (end !== null && end <= t) {
      completedSoFar += 1;
      if (task.met) metSoFar += 1;
      if (t > retEnd + 0.5) continue; // long finished, nothing left to draw
    }
    if (!src || !dst) continue;

    if (remote && task.t_start !== null && t >= task.t_start) {
      const upEnd = Math.max(task.t_end ?? task.t_start, task.t_start + MIN_VISIBLE_TRANSFER);
      if (t < upEnd) { // payload on the wire
        const p = lerp(src, dst, (t - task.t_start) / (upEnd - task.t_start));
        svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 7, fill: C.payload }));
        continue;
      }
    }
    if (hasReturn && t >= task.done && t < retEnd) { // result heading home
      const p = lerp(dst, src, (t - task.done) / (retEnd - task.done));
      svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 7, fill: C.result }));
      continue;
    }
    if (end !== null && t >= retEnd && t - retEnd <= 0.5) { // arrival flash
      const home = task.ret !== null ? task.source : task.node;
      const p = replay.pos[home];
      if (p) svg.append(svgEl("circle", {
        cx: p.x, cy: p.y, r: 20 + 14 * (t - retEnd),
        fill: "none", stroke: task.met ? C.result : C.lost,
        "stroke-width": 3, opacity: 1 - (t - retEnd) / 0.5,
      }));
    }
    // queued/active tasks are represented by the node's queue bar
  }

  // live counters
  const stats = $("#replay-stats");
  stats.innerHTML = "";
  stats.append(
    statCard("generated", generated),
    statCard("completed", completedSoFar),
    statCard("lost", lostSoFar),
    statCard("deadlines met", completedSoFar ? `${((100 * metSoFar) / completedSoFar).toFixed(1)}%` : "-")
  );

  renderInspector(t);

  $("#replay-time").textContent = `t = ${t.toFixed(1)} s / ${data.duration.toFixed(0)} s`;
  const scrub = $("#replay-scrub");
  if (document.activeElement !== scrub) {
    scrub.value = Math.round((t / data.duration) * 1000);
  }
}

/** "What's happening inside this device right now" - live at the replay clock. */
function renderInspector(t) {
  const box = $("#replay-inspect");
  const sel = replay.selected;
  if (!sel) {
    box.innerHTML = "";
    box.append(el("span", { class: "muted", text: "Click a node or controller on the map to inspect it." }));
    return;
  }
  const data = replay.data;
  const rows = [];
  let title;

  if (sel.kind === "node") {
    title = `node · ${sel.id}`;
    const st = stateAt(sel.id, t);
    const running = [];
    const queued = [];
    const inbound = [];
    let generatedHere = 0, completedHere = 0, lostHere = 0, metHere = 0;
    for (const task of data.tasks) {
      if ((task.arrival ?? task.decision) <= t && task.source === sel.id) generatedHere += 1;
      if (task.lost && task.source === sel.id && task.decision <= t) lostHere += 1;
      if (task.node !== sel.id || task.lost) continue;
      const done = task.done;
      if (done !== null && done <= t) {
        completedHere += 1;
        if (task.met) metHere += 1;
        continue;
      }
      if (task.t_start !== null && t >= task.t_start && task.t_end !== null && t < task.t_end) {
        inbound.push(task.id);
      } else if (task.c_start !== null && t >= task.c_start) {
        running.push(task.id);
      } else if (task.decision <= t) {
        queued.push(task.id);
      }
    }
    if (st) {
      rows.push(["state", st[4]]);
      rows.push(["queue length", st[1]]);
      rows.push(["active (running)", st[2]]);
      rows.push(["reliability", Number(st[3]).toFixed(3)]);
    }
    rows.push(["tasks generated here", generatedHere]);
    rows.push(["completed here", `${completedHere} (${metHere} on time)`]);
    if (lostHere) rows.push(["lost from this source", lostHere]);
    rows.push(["arriving over network", inbound.length ? inbound.join(", ") : "-"]);
    rows.push(["executing now", running.length ? running.join(", ") : "-"]);
    rows.push(["waiting", queued.length ? queued.join(", ") : "-"]);
  } else {
    title = `controller · ${sel.id}`;
    const ctrl = (data.controllers || []).find((c) => c.id === sel.id);
    if (!ctrl) {
      replay.selected = null;
      return renderInspector(t);
    }
    const obs = ctrl.observability || {};
    const managed = new Set(ctrl.manages || []);
    let decided = 0, lost = 0;
    const placed = {};
    for (const task of data.tasks) {
      if (task.decision > t || !managed.has(task.source)) continue;
      decided += 1;
      if (task.lost) lost += 1;
      else if (task.node) placed[task.node] = (placed[task.node] || 0) + 1;
    }
    rows.push(["manages", (ctrl.manages || []).join(", ")]);
    rows.push([
      "observability",
      obs.type === "heartbeat"
        ? `heartbeat every ${obs.interval}s (+${obs.report_delay}s report delay)`
        : "perfect (live truth)",
    ]);
    if (obs.type === "heartbeat") {
      const age = t - Math.floor(t / obs.interval) * obs.interval + (obs.report_delay || 0);
      rows.push(["view is stale by ≈", `${age.toFixed(2)} s`]);
    }
    rows.push(["scheduling delay", `${ctrl.scheduling_delay} s`]);
    rows.push(["decisions made", decided]);
    rows.push(["placed", Object.entries(placed).map(([k, v]) => `${k}:${v}`).join(", ") || "-"]);
    if (lost) rows.push(["dropped (nowhere eligible)", lost]);
    const silent = (ctrl.manages || []).filter((id) => {
      const st = stateAt(id, t);
      return st && st[4] === "failed";
    });
    if (silent.length) rows.push(["silent (not reporting)", silent.join(", ")]);
  }

  box.innerHTML = "";
  const head = el("div", { class: "subhead" }, el("b", { text: title }));
  head.append(
    el("button", {
      class: "tiny",
      text: "close",
      onclick: () => {
        replay.selected = null;
        renderReplay(replay.t);
      },
    })
  );
  box.append(head);
  const t2 = el("table", { class: "mini" });
  for (const [k, v] of rows) t2.append(el("tr", {}, el("td", { text: k }), el("td", { text: String(v) })));
  box.append(t2);
}

function replayTick(ts) {
  if (!replay.playing) return;
  if (replay.lastTs !== null) {
    replay.t += ((ts - replay.lastTs) / 1000) * Number($("#replay-speed").value);
    if (replay.t >= replay.data.duration) {
      replay.t = replay.data.duration;
      renderReplay(replay.t);
      replayStop();
      return;
    }
  }
  replay.lastTs = ts;
  renderReplay(replay.t);
  replay.raf = requestAnimationFrame(replayTick);
}

function replayPlay() {
  if (!replay.data) return;
  if (replay.t >= replay.data.duration) replay.t = 0;
  replay.playing = true;
  replay.lastTs = null;
  $("#replay-play").innerHTML = "&#10074;&#10074; pause";
  replay.raf = requestAnimationFrame(replayTick);
}

function replayStop() {
  replay.playing = false;
  if (replay.raf) cancelAnimationFrame(replay.raf);
  $("#replay-play").innerHTML = "&#9654; play";
}

async function onReplayTab() {
  await replayRefreshRuns();
  const sel = $("#replay-run");
  if (!replay.data && sel.options.length > 0) {
    sel.value = sel.options[sel.options.length - 1].value; // newest run
    await replayLoad(sel.value);
  }
}

function bindReplay() {
  $("#replay-refresh").addEventListener("click", replayRefreshRuns);
  $("#replay-run").addEventListener("change", () => replayLoad($("#replay-run").value));
  $("#replay-play").addEventListener("click", () => (replay.playing ? replayStop() : replayPlay()));
  $("#replay-scrub").addEventListener("input", () => {
    if (!replay.data) return;
    replay.t = (Number($("#replay-scrub").value) / 1000) * replay.data.duration;
    renderReplay(replay.t);
  });
}

/* ------------------------------------------------------------------ *
 * compare tab - grid runs over the identical base world
 * ------------------------------------------------------------------ */

// Observability presets are parameter values (not plugins), matching the
// staleness ladder used in the evidence tables.
const OBS_PRESETS = [
  ["perfect (live truth)", null],
  ["heartbeat 1 s", { type: "heartbeat", interval: 1.0, report_delay: 0.02 }],
  ["heartbeat 5 s", { type: "heartbeat", interval: 5.0, report_delay: 0.02 }],
  ["heartbeat 15 s", { type: "heartbeat", interval: 15.0, report_delay: 0.02 }],
];

let cmpCells = null; // last comparison result, for CSV export

function renderCompareControls() {
  const allocBox = $("#cmp-allocators");
  allocBox.innerHTML = "";
  for (const name of Object.keys(schema.registries.allocators)) {
    const cb = el("input", { type: "checkbox", value: name });
    allocBox.append(el("label", { class: "row" }, cb, name));
  }
  const obsBox = $("#cmp-observability");
  obsBox.innerHTML = "";
  OBS_PRESETS.forEach(([label], i) => {
    const cb = el("input", { type: "checkbox", value: String(i) });
    obsBox.append(el("label", { class: "row" }, cb, label));
  });
}

function cmpVariants() {
  const variants = [];
  for (const cb of document.querySelectorAll("#cmp-allocators input:checked")) {
    variants.push({ label: cb.value, allocator: { type: cb.value } });
  }
  for (const cb of document.querySelectorAll("#cmp-observability input:checked")) {
    const [label, spec] = OBS_PRESETS[Number(cb.value)];
    variants.push({ label, observability: spec });
  }
  return variants;
}

function fmtPlacement(placement) {
  return Object.entries(placement || {}).map(([k, v]) => `${k}:${v}`).join(", ");
}

async function doCompare() {
  const out = $("#cmp-result");
  const variants = cmpVariants();
  if (variants.length === 0) {
    out.innerHTML = "";
    out.append(resultBox(false, "Pick at least one variant", ""));
    return;
  }
  const seeds = ($("#cmp-seeds").value || "")
    .split(",").map((s) => s.trim()).filter(Boolean).map(Number);

  const text = await syncedYaml();
  if (text === null) return yamlSyncFailed(out);
  $("#cmp-busy").hidden = false;
  try {
    const r = await api("/api/compare", {
      yaml: text,
      variants,
      seeds: seeds.length ? seeds : null,
    });
    out.innerHTML = "";
    if (!r.ok) {
      out.append(resultBox(false, `Comparison failed (${r.stage || "request"})`, r.error));
      cmpCells = null;
      $("#cmp-export").hidden = true;
      return;
    }
    cmpCells = r.cells;
    $("#cmp-export").hidden = false;

    const t = el("table", { class: "mini" });
    t.append(
      el("tr", {},
        el("th", { text: "variant" }), el("th", { text: "seed" }),
        el("th", { text: "succeeded" }), el("th", { text: "lost" }),
        el("th", { text: "success %" }), el("th", { text: "mean latency" }),
        el("th", { text: "placement" }))
    );
    const bySeedCount = new Set(r.cells.map((c) => c.seed)).size;
    let lastLabel = null;
    const group = []; // cells of the current variant, for the mean row
    const flushMean = () => {
      if (bySeedCount < 2 || group.length < 2) { group.length = 0; return; }
      const mean = (f) => group.reduce((a, c) => a + f(c.summary), 0) / group.length;
      const tr = el("tr", {},
        el("td", {}, el("b", { text: `${group[0].label} - mean` })),
        el("td", { text: `${group.length} seeds` }),
        el("td", { text: mean((s) => s.tasks_succeeded).toFixed(1) }),
        el("td", { text: mean((s) => s.tasks_lost).toFixed(1) }),
        el("td", {}, el("b", { text: `${mean((s) => s.success_rate).toFixed(1)}%` })),
        el("td", {}, el("b", { text: `${mean((s) => s.mean_latency_s).toFixed(3)} s` })),
        el("td", { text: "" }));
      t.append(tr);
      group.length = 0;
    };
    for (const cell of r.cells) {
      if (lastLabel !== null && cell.label !== lastLabel) flushMean();
      lastLabel = cell.label;
      group.push(cell);
      const s = cell.summary;
      t.append(
        el("tr", {},
          el("td", { text: cell.label }),
          el("td", { text: cell.seed }),
          el("td", { text: `${s.tasks_succeeded}/${s.tasks_generated}` }),
          el("td", { text: s.tasks_lost }),
          el("td", { text: `${s.success_rate.toFixed(1)}%` }),
          el("td", { text: `${s.mean_latency_s.toFixed(3)} s` }),
          el("td", { text: fmtPlacement(s.placement) }))
      );
    }
    flushMean();
    out.append(t);
  } finally {
    $("#cmp-busy").hidden = true;
  }
}

function cmpExportCsv() {
  if (!cmpCells) return;
  const header = "variant,seed,tasks_generated,tasks_succeeded,tasks_lost,tasks_late,tasks_unfinished,success_rate,median_latency_s,p95_latency_s,mean_latency_s,mean_lateness_s,max_lateness_s,offload_ratio,load_fairness,placement";
  const lines = cmpCells.map((c) => {
    const s = c.summary;
    const placement = fmtPlacement(s.placement).replaceAll(",", ";");
    return [
      JSON.stringify(c.label), c.seed, s.tasks_generated, s.tasks_succeeded,
      s.tasks_lost, s.tasks_late, s.tasks_unfinished, s.success_rate.toFixed(2),
      s.median_latency_s.toFixed(4), s.p95_latency_s.toFixed(4),
      s.mean_latency_s.toFixed(4), s.mean_lateness_s.toFixed(4),
      s.max_lateness_s.toFixed(4), s.offload_ratio.toFixed(2),
      s.load_fairness.toFixed(4),
      JSON.stringify(placement),
    ].join(",");
  });
  downloadCsv("comparison.csv", [header, ...lines]);
}

function bindCompare() {
  $("#cmp-run").addEventListener("click", doCompare);
  $("#cmp-export").addEventListener("click", cmpExportCsv);
}

/* ------------------------------------------------------------------ *
 * config bar + tabs + boot
 * ------------------------------------------------------------------ */

async function loadConfigsList() {
  const r = await api("/api/configs");
  const sel = $("#config-select");
  sel.innerHTML = "";
  for (const name of r.configs) sel.append(el("option", { value: name, text: name }));
}

/** Reflect the current config in the file dropdown; unsaved drafts get a
 * temporary "(unsaved)" entry until they're saved (the list refresh drops it). */
function setConfigSelect(name, unsaved = false) {
  const sel = $("#config-select");
  if (![...sel.options].some((o) => o.value === name)) {
    sel.append(el("option", { value: name, text: unsaved ? `${name} (unsaved)` : name }));
  }
  sel.value = name;
}

async function loadConfig(name) {
  const r = await api(`/api/configs/${name}`);
  if (r.error) return;
  const parsed = await api("/api/yaml/parse", { yaml: r.yaml });
  if (!parsed.ok) {
    setYamlStatus(false, parsed.error);
    return;
  }
  cfg = parsed.data;
  currentName = name;
  setConfigSelect(name);
  loadLayout();
  renderBuild();
  drawTopology();
  // show the original file text (keeps its comments until first form edit)
  yamlText.value = r.yaml;
  setYamlStatus(true, `loaded ${name}`);
}

function newConfig() {
  cfg = {
    seed: 724,
    sim_duration: 300.0,
    dt: 0.01,
    controllers: [
      { id: "ctrl_main", allocator: { type: "load_aware" }, manages: ["node_1", "node_2"], parent: null },
    ],
    nodes: [
      {
        id: "node_1",
        type: "source",
        cpu_capacity: 1.0,
        memory_capacity: 4.0,
        tier: "edge",
        source: { generator: { type: "poisson", rate: 0.5 } },
      },
      { id: "node_2", type: "helper", cpu_capacity: 4.0, memory_capacity: 8.0, tier: "edge" },
    ],
    logging: { output_dir: "logs/ui_draft", log_state_every: 1.0 },
  };
  currentName = "untitled.yaml";
  setConfigSelect(currentName, true);
  layout = {};
  renderBuild();
  drawTopology();
  refreshYaml();
}

async function saveConfig() {
  let name = prompt("Save as (in configs/):", currentName);
  if (!name) return;
  if (!name.endsWith(".yaml") && !name.endsWith(".yml")) name += ".yaml";
  // syncedYaml, not yamlText.value: the panel lags the form by up to 300ms,
  // so saving raw panel text could write a file missing the last edit.
  const text = await syncedYaml();
  if (text === null) {
    setYamlStatus(false, "the YAML panel is not valid YAML - not saved");
    return;
  }
  const r = await api(`/api/configs/${name}`, { yaml: text });
  if (r.ok) {
    currentName = name;
    await loadConfigsList();
    setConfigSelect(name);
    setYamlStatus(true, `saved ${name}${r.overwrote ? " (overwrote)" : ""}`);
  } else {
    setYamlStatus(false, r.error);
  }
}

function bindHeader() {
  $("#btn-load").addEventListener("click", () => loadConfig($("#config-select").value));
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-new").addEventListener("click", newConfig);
  $("#btn-yaml").addEventListener("click", () => document.body.classList.toggle("yaml-open"));
  $("#btn-validate").addEventListener("click", doValidate);
  $("#btn-run").addEventListener("click", doRun);

  // map toolbar: add devices, open the network / simulation settings popups
  $("#map-add-node").addEventListener("click", () => openNodeModal(addNode()));
  $("#map-add-controller").addEventListener("click", () => openControllerModal(addController()));
  $("#map-network").addEventListener("click", () => openModal("network", networkCard));
  $("#map-sim").addEventListener("click", () => openModal("simulation settings", simCard));
  $("#map-geo").addEventListener("click", openGeometryModal);

  $("#modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal(); // click outside the window
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });

  for (const btn of document.querySelectorAll("#tabs button")) {
    btn.addEventListener("click", () => {
      for (const b of document.querySelectorAll("#tabs button")) b.classList.remove("active");
      btn.classList.add("active");
      for (const tab of document.querySelectorAll(".tab")) tab.classList.remove("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "map") {
        drawTopology();
        refreshRunsList();
      }
      if (btn.dataset.tab === "replay") onReplayTab();
    });
  }
  bindReplay();
  bindCompare();
  renderCompareControls();
}

(async function boot() {
  schema = await api("/api/schema");
  await loadConfigsList();
  bindHeader();
  const sel = $("#config-select");
  const preferred = "heterogeneous.yaml";
  if ([...sel.options].some((o) => o.value === preferred)) sel.value = preferred;
  if (sel.value) await loadConfig(sel.value);
  else newConfig();
})();
