/* Edge Simulator UI — vanilla JS, no build step.
 *
 * Principles:
 * - Every dropdown/field is generated from /api/schema (registry
 *   introspection). Nothing plugin-specific is hardcoded here.
 * - The YAML panel is the truth the simulator sees: form edits are
 *   serialized server-side (/api/yaml/dump), panel edits are parsed
 *   server-side (/api/yaml/parse) — the browser never parses YAML.
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
  dirty();
}

yamlText.addEventListener(
  "input",
  debounce(async () => {
    const r = await api("/api/yaml/parse", { yaml: yamlText.value });
    if (r.ok) {
      cfg = r.data;
      renderBuild();
      drawTopology();
      setYamlStatus(true, "parsed — form updated");
    } else {
      setYamlStatus(false, r.error);
    }
  }, 500)
);

/* ------------------------------------------------------------------ *
 * generic widgets
 * ------------------------------------------------------------------ */

function field(labelText, inputEl, { required = false, wide = false } = {}) {
  const lab = el("label", { text: labelText });
  if (required) lab.append(el("span", { class: "req", text: " *" }));
  return el("div", { class: "field" + (wide ? " wide" : "") }, lab, inputEl);
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
  if (ctx.rateFields && ctx.rateFields.includes(name)) {
    return field(name, specWidget(name, obj, "pattern", "rate_patterns"), {
      required: spec.required,
      wide: true,
    });
  }
  if (ctx.distFields && ctx.distFields.includes(name)) {
    return field(name, specWidget(name, obj, "dist", "distributions"), {
      required: spec.required,
      wide: true,
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
    }), { required: spec.required });
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
      return field(name, input, { required: spec.required, wide: true });
  }
  return field(name, input, { required: spec.required });
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
        selInput(Object.keys(registry), spec[key], (type) => {
          obj[name] = { [key]: type }; // params of the old type don't apply
          render();
          dirty();
        })
      );
      wrap.append(
        paramsBlock(registry[spec[key]]?.params, spec, { registry: registryName })
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
  fields.append(
    field("seed", numInput(cfg.seed, (v) => { setOrDelete(cfg, "seed", v); dirty(); }, { integer: true }), { required: true }),
    field("sim_duration (s)", numInput(cfg.sim_duration, (v) => { setOrDelete(cfg, "sim_duration", v); dirty(); }), { required: true }),
    field("dt (s)", numInput(cfg.dt, (v) => { setOrDelete(cfg, "dt", v); dirty(); }, { placeholder: "0.01" }), { required: true }),
    field("logging output_dir", txtInput(cfg.logging.output_dir, (v) => { setOrDelete(cfg.logging, "output_dir", v); dirty(); }), { required: true, wide: true }),
    field("log_state_every (s)", numInput(cfg.logging.log_state_every, (v) => { setOrDelete(cfg.logging, "log_state_every", v); dirty(); }))
  );
  card.append(fields);
  return card;
}

function nodeFieldWidget(obj, name) {
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
    return field(name, input, { wide: true });
  }
  if (name === "tier") {
    return field(name, txtInput(obj[name], set, { placeholder: "edge" }));
  }
  if (name === "queue_limit") {
    return field(name, numInput(obj[name], set, { integer: true, placeholder: "unlimited" }));
  }
  return field(name, numInput(obj[name], set));
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

  const profiles = cfg.node_profiles || {};
  if (Object.keys(profiles).length === 0) {
    card.append(el("div", { class: "muted", text: "No profiles — nodes define their hardware inline." }));
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
    field(
      "generator",
      selInput(Object.keys(registry), gen.type, (type) => {
        const fresh = { type };
        // carry over params the new generator also understands
        for (const k of Object.keys(gen)) {
          if (k !== "type" && registry[type].params[k] !== undefined) fresh[k] = gen[k];
        }
        node.source.generator = fresh;
        rebuild();
      })
    )
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

function nodesCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Nodes" });
  h.append(
    el("button", {
      class: "tiny",
      text: "+ add node",
      onclick: () => {
        cfg.nodes = cfg.nodes || [];
        cfg.nodes.push({
          id: `node_${cfg.nodes.length + 1}`,
          type: "helper",
          cpu_capacity: 1.0,
          memory_capacity: 4.0,
          tier: "edge",
        });
        rebuild();
      },
    })
  );
  card.append(h);

  (cfg.nodes || []).forEach((node, i) => {
    const sub = el("div", { class: "subcard" });
    const head = el("div", { class: "subhead" });
    head.append(
      el("b", { text: node.id || `#${i + 1}` }),
      el("span", { class: "pill", text: node.type || "?" }),
      el("button", {
        class: "tiny danger",
        text: "remove",
        onclick: () => {
          cfg.nodes.splice(i, 1);
          rebuild();
        },
      })
    );
    sub.append(head);

    const fields = el("div", { class: "fields" });
    fields.append(
      field("id", txtInput(node.id, (v) => { setOrDelete(node, "id", v); rebuild(); }), { required: true }),
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

    if (node.type === "source") sub.append(generatorSection(node));
    card.append(sub);
  });
  return card;
}

function controllersCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Controllers" });
  h.append(
    el("button", {
      class: "tiny",
      text: "+ add controller",
      onclick: () => {
        cfg.controllers = cfg.controllers || [];
        cfg.controllers.push({
          id: `ctrl_${cfg.controllers.length + 1}`,
          allocator: { type: "load_aware" },
          manages: [],
          parent: null,
        });
        rebuild();
      },
    })
  );
  card.append(h);

  (cfg.controllers || []).forEach((ctrl, i) => {
    const sub = el("div", { class: "subcard" });
    const head = el("div", { class: "subhead" }, el("b", { text: ctrl.id || `#${i + 1}` }));
    head.append(
      el("button", {
        class: "tiny danger",
        text: "remove",
        onclick: () => {
          cfg.controllers.splice(i, 1);
          rebuild();
        },
      })
    );
    sub.append(head);

    const base = el("div", { class: "fields" });
    base.append(
      field("id", txtInput(ctrl.id, (v) => { setOrDelete(ctrl, "id", v); rebuild(); }), { required: true }),
      field(
        "scheduling_delay (s)",
        numInput(ctrl.scheduling_delay, (v) => { setOrDelete(ctrl, "scheduling_delay", v); dirty(); }, { placeholder: "0" })
      )
    );
    sub.append(base);

    // allocator — the strategy under study
    sub.append(el("h3", { text: "allocator" }));
    ctrl.allocator = ctrl.allocator || { type: "load_aware" };
    const allocReg = schema.registries.allocators;
    sub.append(
      field(
        "strategy",
        selInput(Object.keys(allocReg), ctrl.allocator.type, (type) => {
          ctrl.allocator = { type };
          rebuild();
        })
      )
    );
    sub.append(
      paramsBlock(allocReg[ctrl.allocator.type]?.params, ctrl.allocator, {
        registry: "allocators",
        pluginType: ctrl.allocator.type,
      })
    );

    // observability — what the controller knows
    sub.append(el("h3", { text: "observability (what the controller knows)" }));
    const obsReg = schema.registries.observability_models;
    const obsCurrent = ctrl.observability?.type || "perfect";
    sub.append(
      field(
        "model",
        selInput(
          Object.keys(obsReg).map((n) => [n, n === "perfect" ? "perfect (live truth)" : n]),
          obsCurrent,
          (type) => {
            if (type === "perfect") delete ctrl.observability; // the default
            else ctrl.observability = { type };
            rebuild();
          }
        )
      )
    );
    if (ctrl.observability) {
      sub.append(
        paramsBlock(obsReg[ctrl.observability.type]?.params, ctrl.observability, {
          registry: "observability_models",
          pluginType: ctrl.observability.type,
        })
      );
    }

    // manages — checkbox per node
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
    card.append(sub);
  });
  return card;
}

function networkCard() {
  const card = el("div", { class: "card" });
  const h = el("h2", { text: "Network" });
  card.append(h);

  if (!cfg.network) {
    card.append(
      el("div", { class: "row" },
        el("span", { class: "muted", text: "No network block — every transfer is instant." }),
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
  fields.append(
    field(
      "model",
      selInput(Object.keys(netReg), net.type, (type) => {
        net.type = type;
        rebuild();
      }),
      { required: true }
    ),
    field(
      "default_profile",
      selInput(Object.keys(schema.network_profiles), net.default_profile || "wifi", (v) => {
        net.default_profile = v;
        dirty();
      })
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

  // links — per-pair profile overrides
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
      field(
        "type",
        selInput(Object.keys(scnReg), scn.type, (type) => {
          cfg.scenarios[i] = { type, node: scn.node };
          rebuild();
        })
      )
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
    card.append(el("div", { class: "muted", text: "None — the world stays stable." }));
  }
  return card;
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

function defaultPositions() {
  const nodes = cfg.nodes || [];
  const ctrls = cfg.controllers || [];
  nodes.forEach((n, i) => {
    const key = `node:${n.id}`;
    if (!layout[key]) {
      const a = (2 * Math.PI * i) / Math.max(nodes.length, 1) - Math.PI / 2;
      layout[key] = { x: 400 + 280 * Math.cos(a), y: 105 + 70 * Math.sin(a) };
    }
  });
  ctrls.forEach((c, i) => {
    const key = `ctrl:${c.id}`;
    if (!layout[key]) {
      layout[key] = { x: 150 + (500 * (i + 1)) / (ctrls.length + 1), y: 225 };
    }
  });
}

function drawTopology() {
  const svg = $("#topology");
  svg.innerHTML = "";
  if (!cfg) return;
  defaultPositions();

  const pos = (key) => layout[key] || { x: 400, y: 130 };

  // manages (dashed) under everything
  for (const ctrl of cfg.controllers || []) {
    const c = pos(`ctrl:${ctrl.id}`);
    for (const id of ctrl.manages || []) {
      const n = pos(`node:${id}`);
      svg.append(svgEl("line", { class: "topo-manage", x1: c.x, y1: c.y, x2: n.x, y2: n.y }));
    }
  }
  // links (solid) with profile labels
  for (const link of cfg.network?.links || []) {
    const a = pos(`node:${link.from}`);
    const b = pos(`node:${link.to}`);
    svg.append(svgEl("line", { class: "topo-link", x1: a.x, y1: a.y, x2: b.x, y2: b.y }));
    svg.append(
      svgEl("text", {
        class: "topo-link-label",
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2 - 4,
        "text-anchor": "middle",
        text: link.profile || "",
      })
    );
  }

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

  // dragging
  for (const [g, key] of draggable) {
    g.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      const svgPoint = (e) => {
        const r = svg.getBoundingClientRect();
        return {
          x: ((e.clientX - r.left) / r.width) * 800,
          y: ((e.clientY - r.top) / r.height) * 260,
        };
      };
      const move = (e) => {
        layout[key] = svgPoint(e);
        drawTopology();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        localStorage.setItem(layoutKey(), JSON.stringify(layout));
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }
}

/* ------------------------------------------------------------------ *
 * run tab
 * ------------------------------------------------------------------ */

async function currentYaml() {
  const r = await api("/api/yaml/dump", { data: cfg });
  return r.yaml;
}

function resultBox(ok, title, detail) {
  const box = el("div", { class: `result-box ${ok ? "ok" : "bad"}` }, el("b", { text: title }));
  if (detail) box.append(el("pre", { text: detail }));
  return box;
}

async function doValidate() {
  const out = $("#validate-result");
  out.innerHTML = "";
  const r = await api("/api/validate", { yaml: await currentYaml() });
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
  busy.hidden = false;
  try {
    const r = await api("/api/run", { yaml: await currentYaml() });
    out.innerHTML = "";
    if (!r.ok) {
      out.append(resultBox(false, `Run failed (${r.stage || "request"})`, r.error));
      return;
    }
    const s = r.summary;
    out.append(resultBox(true, `Run ${r.run_id} finished`, `logs: ${r.log_dir}`));
    const cards = el("div", { class: "summary-cards" });
    cards.append(
      statCard("tasks generated", s.tasks_generated),
      statCard("completed", s.tasks_completed),
      statCard("lost", s.tasks_lost),
      statCard("deadlines met", `${s.deadline_pct.toFixed(1)}%`),
      statCard("mean latency", `${s.mean_latency_s.toFixed(3)} s`),
      statCard("simulated", `${s.final_time.toFixed(0)} s`),
      statCard("wall clock", `${s.wall_seconds.toFixed(3)} s`)
    );
    out.append(cards);
    out.append(
      miniTable("tasks per node", Object.entries(s.placement)),
      miniTable("max queue per node", Object.entries(s.max_queue))
    );
    refreshRunsList();
  } finally {
    busy.hidden = true;
  }
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
      el("th", { text: "deadline %" }), el("th", { text: "mean latency" }))
  );
  for (const [id, entry] of entries.reverse()) {
    const s = entry.summary || {};
    t.append(
      el("tr", {},
        el("td", { text: id }),
        el("td", { text: s.tasks_generated ?? "…" }),
        el("td", { text: s.deadline_pct !== undefined ? s.deadline_pct.toFixed(1) : "…" }),
        el("td", { text: s.mean_latency_s !== undefined ? `${s.mean_latency_s.toFixed(3)} s` : "…" }))
    );
  }
  box.append(t);
}

/* ------------------------------------------------------------------ *
 * replay tab — animate a finished run from its timeline
 * ------------------------------------------------------------------ */

const MIN_VISIBLE_TRANSFER = 0.15; // sim-seconds; visual stretch only

const replay = {
  data: null,
  t: 0,
  playing: false,
  raf: null,
  lastTs: null,
  pos: {}, // id -> {x, y} on the replay canvas
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
      : { x: 400 + 290 * Math.cos(a), y: 140 + 90 * Math.sin(a) };
  });
  (replay.data.controllers || []).forEach((c, i) => {
    const saved = layout[`ctrl:${c.id}`];
    replay.pos[`ctrl:${c.id}`] = saved || {
      x: 150 + (500 * (i + 1)) / (replay.data.controllers.length + 1),
      y: 300,
    };
  });
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
    svg.append(
      svgEl("rect", {
        x: c.x - 9, y: c.y - 9, width: 18, height: 18, rx: 3,
        fill: "#fef3c7", stroke: "#d4a373", "stroke-width": 1.5,
        transform: `rotate(45 ${c.x} ${c.y})`,
      }),
      svgEl("text", { x: c.x, y: c.y + 26, "text-anchor": "middle", class: "topo-link-label", text: ctrl.id })
    );
  }

  // configured links
  for (const link of data.links || []) {
    const a = replay.pos[link.from];
    const b = replay.pos[link.to];
    if (!a || !b) continue;
    svg.append(svgEl("line", { class: "topo-link", x1: a.x, y1: a.y, x2: b.x, y2: b.y }));
    svg.append(
      svgEl("text", {
        class: "topo-link-label",
        x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 4,
        "text-anchor": "middle", text: link.profile || "",
      })
    );
  }

  // nodes with live state
  for (const node of data.nodes) {
    const p = replay.pos[node.id];
    const st = stateAt(node.id, t);
    const failure = st ? st[4] : "normal";
    const queue = st ? st[1] : 0;

    let fill = node.type === "source" ? "#dbe7ff" : "#dcf5e4";
    let stroke = node.type === "source" ? "#2563eb" : "#15803d";
    if (failure === "failed") { fill = "#e5e7eb"; stroke = "#6b7280"; }
    else if (failure === "recovering") stroke = "#d97706";

    svg.append(
      svgEl("circle", { cx: p.x, cy: p.y, r: 16, fill, stroke, "stroke-width": failure === "recovering" ? 3.5 : 2 }),
      svgEl("text", { x: p.x, y: p.y + 4, "text-anchor": "middle", "font-size": 10, fill: "#1d2733", text: queue }),
      svgEl("text", { x: p.x, y: p.y + 32, "text-anchor": "middle", "font-size": 11, fill: "#1d2733", text: node.id })
    );
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

  // tasks in flight
  let generated = 0, completedSoFar = 0, metSoFar = 0, lostSoFar = 0;
  for (const task of data.tasks) {
    if (task.decision > t) break; // tasks are sorted by decision time
    generated += 1;
    if (task.lost) {
      lostSoFar += 1;
      if (t - task.decision <= 1.0) {
        const p = replay.pos[task.source];
        if (p) svg.append(svgEl("text", {
          x: p.x + 14, y: p.y - 14, "font-size": 14, fill: "#b91c1c", text: "✗",
        }));
      }
      continue;
    }
    const end = task.ret ?? task.done;
    if (end !== null && end <= t) {
      completedSoFar += 1;
      if (task.met) metSoFar += 1;
      if (t - end <= 0.5) { // brief arrival flash
        const home = task.ret !== null ? task.source : task.node;
        const p = replay.pos[home];
        if (p) svg.append(svgEl("circle", {
          cx: p.x, cy: p.y, r: 16 + 6 * (t - end),
          fill: "none", stroke: task.met ? "#15803d" : "#b91c1c",
          "stroke-width": 2, opacity: 1 - (t - end) / 0.5,
        }));
      }
      continue;
    }
    const src = replay.pos[task.source];
    const dst = replay.pos[task.node];
    if (!src || !dst) continue;

    const remote = task.node !== task.source;
    if (remote && task.t_start !== null && t >= task.t_start) {
      const upEnd = Math.max(task.t_end ?? task.t_start, task.t_start + MIN_VISIBLE_TRANSFER);
      if (t < upEnd) { // payload on the wire
        const p = lerp(src, dst, (t - task.t_start) / (upEnd - task.t_start));
        svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 4, fill: "#2563eb" }));
        continue;
      }
    }
    if (remote && task.done !== null && t >= task.done && task.ret !== null) {
      const downEnd = Math.max(task.ret, task.done + MIN_VISIBLE_TRANSFER);
      if (t < downEnd) { // result heading home
        const p = lerp(dst, src, (t - task.done) / (downEnd - task.done));
        svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 4, fill: "#15803d" }));
      }
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
    statCard("deadlines met", completedSoFar ? `${((100 * metSoFar) / completedSoFar).toFixed(1)}%` : "–")
  );

  $("#replay-time").textContent = `t = ${t.toFixed(1)} s / ${data.duration.toFixed(0)} s`;
  const scrub = $("#replay-scrub");
  if (document.activeElement !== scrub) {
    scrub.value = Math.round((t / data.duration) * 1000);
  }
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
 * compare tab — grid runs over the identical base world
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

  $("#cmp-busy").hidden = false;
  try {
    const r = await api("/api/compare", {
      yaml: await currentYaml(),
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
        el("th", { text: "done" }), el("th", { text: "lost" }),
        el("th", { text: "deadline %" }), el("th", { text: "mean latency" }),
        el("th", { text: "placement" }))
    );
    const bySeedCount = new Set(r.cells.map((c) => c.seed)).size;
    let lastLabel = null;
    const group = []; // cells of the current variant, for the mean row
    const flushMean = () => {
      if (bySeedCount < 2 || group.length < 2) { group.length = 0; return; }
      const mean = (f) => group.reduce((a, c) => a + f(c.summary), 0) / group.length;
      const tr = el("tr", {},
        el("td", {}, el("b", { text: `${group[0].label} — mean` })),
        el("td", { text: `${group.length} seeds` }),
        el("td", { text: mean((s) => s.tasks_completed).toFixed(1) }),
        el("td", { text: mean((s) => s.tasks_lost).toFixed(1) }),
        el("td", {}, el("b", { text: `${mean((s) => s.deadline_pct).toFixed(1)}%` })),
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
          el("td", { text: `${s.tasks_completed}/${s.tasks_generated}` }),
          el("td", { text: s.tasks_lost }),
          el("td", { text: `${s.deadline_pct.toFixed(1)}%` }),
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
  const header = "variant,seed,tasks_generated,tasks_completed,tasks_lost,deadline_pct,mean_latency_s,placement";
  const lines = cmpCells.map((c) => {
    const s = c.summary;
    const placement = fmtPlacement(s.placement).replaceAll(",", ";");
    return [
      JSON.stringify(c.label), c.seed, s.tasks_generated, s.tasks_completed,
      s.tasks_lost, s.deadline_pct.toFixed(2), s.mean_latency_s.toFixed(4),
      JSON.stringify(placement),
    ].join(",");
  });
  const blob = new Blob([[header, ...lines].join("\n")], { type: "text/csv" });
  const a = el("a", { href: URL.createObjectURL(blob), download: "comparison.csv" });
  a.click();
  URL.revokeObjectURL(a.href);
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
  $("#config-name").textContent = name;
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
  $("#config-name").textContent = currentName + " (unsaved)";
  layout = {};
  renderBuild();
  drawTopology();
  refreshYaml();
}

async function saveConfig() {
  let name = prompt("Save as (in configs/):", currentName);
  if (!name) return;
  if (!name.endsWith(".yaml") && !name.endsWith(".yml")) name += ".yaml";
  const r = await api(`/api/configs/${name}`, { yaml: yamlText.value || (await currentYaml()) });
  if (r.ok) {
    currentName = name;
    $("#config-name").textContent = name;
    await loadConfigsList();
    $("#config-select").value = name;
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

  for (const btn of document.querySelectorAll("#tabs button")) {
    btn.addEventListener("click", () => {
      for (const b of document.querySelectorAll("#tabs button")) b.classList.remove("active");
      btn.classList.add("active");
      for (const tab of document.querySelectorAll(".tab")) tab.classList.remove("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "run") refreshRunsList();
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
