/* Drives vocalize/assets/portal.js without a browser.
 *
 * The script is loaded into this process over a stub `document`, `window`
 * and `fetch`; a scenario calls into `window.portal`, fires events on the
 * nodes the page built, and answers the requests the page makes, in the
 * order it chooses. Exit 0 is a pass; anything else prints why. `boot()`
 * never runs — `DOMContentLoaded` never fires here — so the one-time code
 * exchange is not touched; a scenario starts the page with `portal.start()`.
 *
 *     node tests/portal_page_harness.js vocalize/assets/portal.js <scenario> \
 *         <schema.json> [<controls.json>]
 *
 * `schema.json` is the real `settings_registry.schema()`, written by the
 * Python test; the actions in it are the fixture below, because the page
 * is coded against the API contract rather than one build of the actions.
 * Run by tests/test_portal_assets.py, one process per scenario, because the
 * page keeps module state (`dead`, the fingerprint, the drafts).
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

// --- the smallest DOM the page touches ----------------------------------

class Node {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.inert = false;
    this.textContent = "";
    this.className = "";
    this.value = "";
    this.classList = { add() {}, remove() {} };
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    // What a live region announces depends on whether it was visible when
    // its content changed, so the stub remembers.
    this.filledWhileHidden = this.hidden;
    this.children = children;
  }
  setAttribute(name, value) {
    this.attrs[name] = value;
  }
  getAttribute(name) {
    return this.attrs[name];
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  fire(type, extra) {
    const event = Object.assign(
      {
        target: this,
        defaultPrevented: false,
        preventDefault() {
          event.defaultPrevented = true;
        }
      },
      extra || {}
    );
    (this.listeners[type] || []).forEach((fn) => fn(event));
    return event;
  }
  focus() {
    document.activeElement = this;
    this.fire("focus");
  }
  find(pred) {
    if (pred(this)) return this;
    for (const child of this.children) {
      const hit = child.find(pred);
      if (hit) return hit;
    }
    return null;
  }
  findAll(pred, into) {
    into = into || [];
    if (pred(this)) into.push(this);
    for (const child of this.children) child.findAll(pred, into);
    return into;
  }
  texts() {
    return this.findAll(() => true).map((node) => node.textContent);
  }
  // The raw-markup sinks do not exist on this stub: a page that reached
  // for one would fail here before it could ever fail in a browser.
  set innerHTML(_value) {
    throw new Error("innerHTML is not a sink this page may use");
  }
  set outerHTML(_value) {
    throw new Error("outerHTML is not a sink this page may use");
  }
}

const byId = {};
// The ids portal.html itself declares. Any other id is looked up in the
// nodes the page built, as a browser would, and is null when it is gone.
const STATIC_IDS = new Set([
  "alert", "config-alert", "conflict", "notices", "save-note", "status", "save", "discard",
  "recheck", "config-path", "page-address", "rows", "rows-note", "groups", "toc", "profile"
]);
// The banners start hidden, as portal.html declares them.
["alert", "config-alert", "conflict", "notices", "save-note"].forEach((id) => {
  byId[id] = new Node("div");
  byId[id].id = id;
  byId[id].hidden = true;
});
const main = new Node("main");

global.document = {
  body: new Node("body"),
  activeElement: null,
  getElementById(id) {
    if (byId[id]) return byId[id];
    for (const root of Object.values(byId)) {
      const hit = root.find((node) => node.id === id);
      if (hit) return hit;
    }
    if (!STATIC_IDS.has(id)) return null;
    byId[id] = new Node("div");
    byId[id].id = id;
    return byId[id];
  },
  createElement(tag) {
    return new Node(tag);
  },
  createTextNode(text) {
    const node = new Node("#text");
    node.textContent = text;
    return node;
  },
  querySelector(selector) {
    return selector === "main" ? main : null;
  },
  querySelectorAll() {
    return [];
  },
  addEventListener() {}
};
const asked = []; // every question window.confirm was given
let confirmAnswer = true;
global.window = {
  location: { host: "127.0.0.1:1", port: "1", hash: "", pathname: "/" },
  history: { replaceState() {} },
  sessionStorage: {
    getItem() {
      return null;
    },
    setItem() {}
  },
  confirm(question) {
    asked.push(question);
    return confirmAnswer;
  }
};

// --- fetch, answered by hand -----------------------------------------------

const pending = [];
const log = []; // every request the page ever made, answered or not
global.fetch = (path, options) =>
  new Promise((resolve) => {
    const request = { key: (options.method || "GET") + " " + path, options, resolve };
    pending.push(request);
    log.push(request);
  });

function take(key) {
  const index = pending.findIndex((request) => request.key === key);
  assert.notStrictEqual(
    index,
    -1,
    "expected a pending " + key + "; pending: [" + pending.map((r) => r.key).join(", ") + "]"
  );
  return pending.splice(index, 1)[0];
}

function sent(request) {
  return JSON.parse(request.options.body);
}

const answer = (data) => ({ ok: true, status: 200, json: async () => data, headers: { get: () => "" } });
const refusal = (status, body) => ({
  ok: false,
  status: status,
  json: async () => (typeof body === "string" ? { error: body } : body),
  headers: { get: () => "" }
});

/** Let every continuation waiting on a settled promise run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// --- fixtures --------------------------------------------------------------

const [scriptPath, scenarioName, schemaPath, controlsPath] = process.argv.slice(2);
const REAL = schemaPath ? JSON.parse(fs.readFileSync(schemaPath, "utf8")) : { groups: [], settings: [] };

/** The actions, as the contract shapes them. */
const ACTIONS = [
  {
    id: "preview_voice", label: "Preview a voice", help: "Speak one sentence.", group: "speaking",
    kind: "job", confirm: false, danger: false,
    params: [
      { name: "engine", label: "Engine", type: "enum", choices: null, choices_from: "tts_engines", required: true },
      { name: "voice", label: "Voice", type: "str", choices: null, choices_from: null, required: false }
    ]
  },
  {
    id: "install_engine", label: "Install an engine", help: "Downloads it.", group: "engines",
    kind: "job", confirm: true, danger: false,
    params: [{ name: "engine", label: "Engine", type: "enum", choices: ["parakeet", "whisper"], choices_from: null, required: true }]
  },
  {
    id: "remove_model", label: "Remove a model", help: "Deletes the files.", group: "engines",
    kind: "instant", confirm: false, danger: true,
    params: [{ name: "model", label: "Model", type: "enum", choices: null, choices_from: "whisper_models", required: true }]
  },
  {
    id: "clear_audio_cache", label: "Clear the audio cache", help: "Frees disk space.", group: "advanced",
    kind: "instant", confirm: false, danger: false, params: []
  },
  {
    id: "app_restart", label: "Restart the menu-bar app", help: "", group: "hotkeys",
    kind: "instant", confirm: false, danger: false, params: []
  },
  {
    id: "apply_profile", label: "Apply this profile", help: "Sets the engines for this Mac.", group: "engines",
    kind: "instant", confirm: true, danger: false,
    params: [{ name: "profile", label: "Profile", type: "enum", choices: null, choices_from: null, required: true }]
  },
  {
    id: "teleport", label: "Something newer", help: "A kind this page has never heard of.", group: "advanced",
    kind: "wormhole", confirm: false, danger: false,
    params: [{ name: "where", label: "Where", type: "colour", choices: null, choices_from: null, required: false }]
  },
  {
    id: "stray", label: "An action with no group", help: "", group: "nowhere",
    kind: "instant", confirm: false, danger: false, params: []
  }
];

function schemaFixture(extraSettings) {
  return {
    groups: REAL.groups,
    settings: REAL.settings.concat(extraSettings || []),
    actions: ACTIONS
  };
}

function defaults(schema) {
  const out = {};
  schema.settings.forEach((setting) => {
    out[setting.key] = Array.isArray(setting.default) ? setting.default.slice() : setting.default;
  });
  return out;
}

const SOURCES = {
  kokoro_voices: [{ value: "af_heart", label: "Heart" }, { value: "af_bella", label: "Bella" }],
  piper_voices: [{ value: "lessac", label: "Lessac" }],
  say_voices: [{ value: "Samantha", label: "Samantha" }],
  input_devices: [{ value: "MacBook Pro Microphone", label: "MacBook Pro Microphone" }],
  whisper_models: [
    { value: "base.en", label: "base.en" },
    { value: "large-v3-turbo-q5_0", label: "large-v3-turbo-q5_0" }
  ],
  tts_engines: [
    { value: "kokoro", label: "Kokoro" },
    { value: "piper", label: "Piper" },
    { value: "say", label: "macOS say" }
  ],
  stt_engines: [
    { value: "auto", label: "Auto" },
    { value: "parakeet", label: "Parakeet" },
    { value: "whisper", label: "Whisper" }
  ]
};

function statePayload(schema, fingerprint, values) {
  return {
    rows: [],
    config_path: "/tmp/config.toml",
    config_error: null,
    values: Object.assign(defaults(schema), values || {}),
    fingerprint: fingerprint,
    notices: [],
    sources: SOURCES,
    actions: {
      clear_audio_cache: { available: true, reason: "", status: "Holds 120 MB." },
      remove_model: { available: false, reason: "no model is installed", status: "" }
    },
    profile: { detected: "studio", names: ["laptop", "studio"] }
  };
}

/** Start the page: the schema, then the state. */
async function startPage(portal, schema, data) {
  portal.start();
  await settle();
  take("GET /api/schema").resolve(answer(schema));
  await settle();
  take("GET /api/state").resolve(answer(data));
  await settle();
  assert.deepStrictEqual(pending, [], "nothing else is in flight after start");
}

const groupsRoot = () => document.getElementById("groups");
const cardOf = (key) => {
  const cards = groupsRoot().findAll((node) => node.attrs["data-setting"] === key);
  assert.strictEqual(cards.length, 1, "exactly one card for " + key);
  return cards[0];
};
const controlOf = (key) => {
  const found = groupsRoot().findAll((node) => node.attrs["data-control"] === key);
  assert.strictEqual(found.length, 1, "exactly one control for " + key);
  return found[0];
};
const buttonIn = (root, text) => {
  const hit = root.find((node) => node.tag === "button" && node.textContent === text);
  assert.ok(hit, "a button reading " + text);
  return hit;
};

/** Choose the option whose value is `value` in a stub `<select>`. */
function choose(select, value) {
  const item = select.children.find((option) => option.value === value);
  assert.ok(item, "an option " + value + " in " + select.id + ": " + select.children.map((o) => o.value).join(", "));
  select.children.forEach((option) => {
    option.selected = option === item;
  });
  select.value = item.value;
  select.fire("change");
}

function typeInto(box, text) {
  box.value = text;
  box.fire("input");
  box.fire("change");
}

/** Press Save; return the request it sent. */
async function pressSave() {
  document.getElementById("save").fire("click");
  await settle();
  return take("POST /api/settings");
}

/** Answer a save with 200 and the poll after it with the new values. */
async function saved(schema, write, fingerprint, values) {
  const data = statePayload(schema, fingerprint, values);
  write.resolve(answer({ values: data.values, fingerprint: fingerprint, notices: [], restart: [] }));
  await settle();
  take("GET /api/state").resolve(answer(data));
  await settle();
}

// --- scenarios ------------------------------------------------------------

const scenarios = {
  /* Every setting the registry has ends up with exactly one card and one
   * control, under its group's section, in the groups' order, with its
   * label tied to it and its help shown; actions follow the settings. */
  async every_setting(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0"));
    assert.ok(schema.settings.length >= 40, "the real registry: " + schema.settings.length);

    const sections = groupsRoot().children;
    const order = schema.groups.slice().sort((a, b) => a.order - b.order).map((g) => g.id);
    assert.deepStrictEqual(
      sections.map((s) => s.attrs["data-group"]),
      order.concat(["__other__"]),
      "one section per group, in order, then one for what has no group"
    );
    schema.groups.forEach((group) => {
      const section = sections.find((s) => s.attrs["data-group"] === group.id);
      assert.strictEqual(section.children[0].textContent, group.label);
      assert.ok(section.texts().includes(group.help), "the group's help is shown");
    });

    schema.settings.forEach((setting) => {
      const card = cardOf(setting.key);
      const control = controlOf(setting.key);
      const section = sections.find((s) => s.attrs["data-group"] === setting.group);
      assert.ok(section.find((node) => node === card), setting.key + " is in its group's section");
      // A real label tied to the control, or a legend for a group of them.
      if (control.tag === "fieldset") {
        assert.strictEqual(control.children[0].tag, "legend");
        assert.strictEqual(control.children[0].textContent, setting.label);
      } else {
        const label = card.find((node) => node.tag === "label" && node.htmlFor === control.id);
        assert.ok(label && control.id, setting.key + " has a label tied to its control");
        assert.strictEqual(label.textContent, setting.label);
      }
      // Every interactive part has a label or an accessible name.
      card.findAll((node) => node.tag === "input" || node.tag === "select").forEach((input) => {
        assert.ok(
          card.find((node) => node.tag === "label" && node.htmlFor === input.id),
          setting.key + ": an input with no label: " + input.id
        );
      });
      assert.ok(card.texts().includes(setting.help), setting.key + " shows its help");
      assert.ok(card.find((node) => node.attrs["data-reset"] === setting.key), setting.key + " has a reset button");
      assert.ok(
        card.texts().some((t) => t === "Default value" || t.indexOf("Changed from the default") === 0),
        setting.key + " says whether it holds the default"
      );
    });

    // Inside a section the settings come first, then the actions.
    const engines = sections.find((s) => s.attrs["data-group"] === "engines");
    const kinds = engines.children.map((node) => (node.attrs["data-setting"] ? "s" : node.attrs["data-action"] ? "a" : "-"));
    assert.ok(kinds.join("").replace(/-/g, "").match(/^s+a+$/), "settings, then actions: " + kinds.join(""));

    // Nothing here calls the routes that went away.
    log.forEach((request) => {
      assert.ok(!/\/api\/(chain|provider|stt|voices|local)/.test(request.key), "a removed route: " + request.key);
    });
  },

  /* The renderer map covers every control kind the registry can produce
   * (the list comes from `settings_registry.CONTROLS`, via the Python test)
   * and every kind this schema uses. */
  async kinds(portal) {
    const controls = JSON.parse(fs.readFileSync(controlsPath, "utf8"));
    assert.ok(controls.length >= 10);
    controls.forEach((kind) => assert.strictEqual(typeof portal.renderers[kind], "function", "no renderer for " + kind));
    REAL.settings.forEach((setting) => assert.ok(portal.renderers[setting.control], setting.control));
  },

  /* One change per control kind, saved one at a time: each save sends
   * exactly that key, with a value of the right type, and the fingerprint. */
  async round_trip(portal) {
    const schema = schemaFixture();
    let values = {};
    let fp = 0;
    await startPage(portal, schema, statePayload(schema, "f0"));

    const steps = [
      ["toggle", "stt.paste", () => {
        const box = controlOf("stt.paste");
        box.checked = true;
        box.fire("change");
      }, true],
      ["select", "overflow", () => choose(controlOf("overflow"), "never"), "never"],
      ["number", "stt.max_seconds", () => typeInto(controlOf("stt.max_seconds"), "90"), 90],
      ["number", "providers.kokoro.speed", () => typeInto(controlOf("providers.kokoro.speed"), "1.15"), 1.15],
      ["text", "providers.kokoro.language", () => typeInto(controlOf("providers.kokoro.language"), "en-gb"), "en-gb"],
      ["chord", "app.dictate", () => {
        const box = controlOf("app.dictate");
        // Option held changes `key`; the page reads the physical `code`.
        const event = box.fire("keydown", { code: "KeyJ", key: "∆", ctrlKey: true, altKey: true, metaKey: true, shiftKey: false });
        assert.strictEqual(event.defaultPrevented, true, "a captured chord does not also type");
        assert.strictEqual(box.value, "ctrl+alt+cmd+j", "shown in vocalize's spelling");
        const plain = box.fire("keydown", { code: "KeyA", key: "a" });
        assert.strictEqual(plain.defaultPrevented, false, "plain typing is left alone");
      }, "ctrl+alt+cmd+j"],
      ["chord", "app.speak", () => typeInto(controlOf("app.speak"), "Ctrl+Cmd+K"), "ctrl+cmd+k"],
      ["device", "stt.input_device", () => {
        const pick = controlOf("stt.input_device");
        assert.strictEqual(pick.children[0].value, "", "the system default is the first choice");
        choose(pick, "MacBook Pro Microphone");
      }, "MacBook Pro Microphone"],
      ["list", "stt.vocabulary", () => {
        const add = cardOf("stt.vocabulary").find((n) => n.tag === "input" && n.id === "add-stt-vocabulary");
        add.value = "Tungsten";
        buttonIn(cardOf("stt.vocabulary"), "Add").fire("click");
      }, ["Tungsten"]],
      ["voice", "providers.kokoro.voice", () => choose(controlOf("providers.kokoro.voice"), "af_bella"), "af_bella"],
      ["folder", "transcribe.folder", () => typeInto(controlOf("transcribe.folder"), "~/Notes"), "~/Notes"],
      ["chain", "chain", () => {
        const first = cardOf("chain").find((n) => n.tag === "button" && n.id === "down-chain-0");
        first.fire("click");
      }, schema.settings.find((s) => s.key === "chain").default.slice().reverse()]
    ];
    const covered = new Set(steps.map((step) => step[0]));
    REAL.settings.forEach((setting) => assert.ok(covered.has(setting.control), "no round trip for " + setting.control));

    for (const [kind, key, act, expected] of steps) {
      act();
      assert.deepStrictEqual(portal.collect(), { [key]: expected }, kind + ": only the changed key is staged");
      const write = await pressSave();
      const body = sent(write);
      assert.strictEqual(body.fingerprint, "f" + fp, kind + ": the fingerprint goes with it");
      assert.deepStrictEqual(Object.keys(body.changes), [key], kind + ": exactly that key is sent");
      assert.deepStrictEqual(body.changes[key], expected, kind + ": the value");
      assert.strictEqual(typeof body.changes[key], typeof expected, kind + ": the value's type");
      values = Object.assign({}, values, { [key]: expected });
      fp += 1;
      await saved(schema, write, "f" + fp, values);
      assert.deepStrictEqual(portal.collect(), {}, kind + ": nothing is left to save");
    }
    assert.ok(document.getElementById("status").textContent.indexOf("Saved 1 setting") === 0);
    assert.deepStrictEqual(pending, []);
  },

  /* Reset sends null for that key — and only that key. */
  async reset(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0", { "stt.max_seconds": 90, "stt.paste": true }));
    const card = cardOf("stt.max_seconds");
    assert.ok(card.texts().includes("Changed from the default (120)"));
    card.find((n) => n.attrs["data-reset"] === "stt.max_seconds").fire("click");
    assert.strictEqual(controlOf("stt.max_seconds").value, "120", "the default is shown");
    const write = await pressSave();
    assert.deepStrictEqual(sent(write).changes, { "stt.max_seconds": null });
    await saved(schema, write, "f1", { "stt.paste": true });
    assert.ok(cardOf("stt.max_seconds").texts().includes("Default value"));
  },

  /* A 400 shows each error by its control, saves nothing, keeps the edits. */
  async errors(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0"));
    typeInto(controlOf("stt.max_seconds"), "99999");
    // The page refuses a number outside the range before it is sent.
    document.getElementById("save").fire("click");
    await settle();
    assert.deepStrictEqual(pending, [], "an out-of-range number is not sent");
    assert.ok(cardOf("stt.max_seconds").find((n) => n.className === "bad"), "and the reason is by the field");
    typeInto(controlOf("stt.max_seconds"), "100");
    typeInto(controlOf("providers.kokoro.language"), "bad language");
    const write = await pressSave();
    write.resolve(refusal(400, { errors: { "providers.kokoro.language": "Invalid language: no spaces." } }));
    await settle();
    const card = cardOf("providers.kokoro.language");
    const bad = card.find((n) => n.className === "bad");
    assert.ok(bad, "the error is shown in the card");
    assert.strictEqual(bad.textContent, "Invalid language: no spaces.");
    const control = controlOf("providers.kokoro.language");
    assert.strictEqual(control.attrs["aria-invalid"], "true");
    assert.ok(control.attrs["aria-describedby"].split(" ").includes(bad.id), "the error describes the control");
    assert.strictEqual(control.value, "bad language", "the edit is kept");
    assert.strictEqual(controlOf("stt.max_seconds").value, "100");
    assert.deepStrictEqual(portal.collect(), { "stt.max_seconds": 100, "providers.kokoro.language": "bad language" });
    assert.deepStrictEqual(pending, [], "a 400 starts nothing else");
  },

  /* A 409: the user is told, offered a reload, and nothing is discarded. */
  async conflict(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0"));
    typeInto(controlOf("transcribe.folder"), "~/Elsewhere");
    const write = await pressSave();
    const theirs = statePayload(schema, "f9", { "stt.paste": true });
    write.resolve(refusal(409, { error: "changed", fingerprint: "f9", values: theirs.values }));
    await settle();
    const box = document.getElementById("conflict");
    assert.strictEqual(box.hidden, false, "the user is told");
    assert.ok(box.texts().some((t) => t.indexOf("changed on disk") !== -1));
    assert.deepStrictEqual(pending, [], "nothing is reloaded or re-sent behind the user's back");
    assert.strictEqual(controlOf("transcribe.folder").value, "~/Elsewhere", "the edit is kept");

    buttonIn(box, "Reload the file, keep my edits").fire("click");
    await settle();
    take("GET /api/state").resolve(answer(theirs));
    await settle();
    assert.strictEqual(box.hidden, true);
    assert.strictEqual(controlOf("stt.paste").checked, true, "the file's new value is shown");
    assert.strictEqual(controlOf("transcribe.folder").value, "~/Elsewhere", "the edit survives the reload");
    const again = await pressSave();
    assert.deepStrictEqual(sent(again), { changes: { "transcribe.folder": "~/Elsewhere" }, fingerprint: "f9" });
  },

  /* Every action gets one button with its label and help; confirm and
   * danger ask first; unavailable ones are disabled with the reason; a job
   * is followed to its end and then the state is fetched again. */
  async actions(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0"));
    const page = [groupsRoot(), document.getElementById("profile")];
    const runOf = (id) => {
      const found = [].concat(...page.map((root) => root.findAll((n) => n.attrs["data-run"] === id)));
      assert.strictEqual(found.length, 1, "exactly one button for " + id);
      return found[0];
    };
    ACTIONS.forEach((action) => {
      const button = runOf(action.id);
      assert.strictEqual(button.textContent, action.label);
      if (action.help) {
        const card = [].concat(...page.map((root) => root.findAll((n) => n.attrs["data-action"] === action.id)))[0];
        assert.ok(card.texts().includes(action.help), action.id + " shows its help");
      }
    });

    // Unavailable: disabled, with the reason in words. Status from the state.
    assert.strictEqual(runOf("remove_model").disabled, true);
    assert.ok(groupsRoot().texts().includes("Not available: no model is installed."));
    assert.ok(groupsRoot().texts().includes("Now: Holds 120 MB."));

    // An unknown kind and an unknown param type still render, with a note.
    const odd = groupsRoot().find((n) => n.attrs["data-action"] === "teleport");
    assert.ok(odd.texts().some((t) => t.indexOf("does not know the action kind") !== -1));
    assert.ok(odd.find((n) => n.tag === "input"), "the unknown param is a text field");
    assert.ok(groupsRoot().find((n) => n.attrs["data-group"] === "__other__").find((n) => n.attrs["data-run"] === "stray"));

    // Instant, no confirm: posts straight away, then the state is fetched.
    runOf("clear_audio_cache").fire("click");
    await settle();
    assert.strictEqual(asked.length, 0, "no question for an action that does not need one");
    const clear = take("POST /api/action/clear_audio_cache");
    assert.deepStrictEqual(sent(clear), { params: {} });
    clear.resolve(answer({ ok: true, job: null, message: "Cleared 120 MB." }));
    await settle();
    take("GET /api/state").resolve(answer(statePayload(schema, "f0")));
    await settle();
    assert.ok(groupsRoot().texts().includes("Cleared 120 MB."));

    // confirm: asked; a "no" sends nothing.
    confirmAnswer = false;
    runOf("install_engine").fire("click");
    await settle();
    assert.strictEqual(asked.length, 1, "confirm asks first");
    assert.deepStrictEqual(pending, [], "a declined action sends nothing");

    // A job: started, followed, finished, then the state.
    confirmAnswer = true;
    choose(groupsRoot().find((n) => n.id === "param-install_engine-engine"), "whisper");
    runOf("install_engine").fire("click");
    await settle();
    assert.strictEqual(asked.length, 2);
    const install = take("POST /api/action/install_engine");
    assert.deepStrictEqual(sent(install), { params: { engine: "whisper" } });
    install.resolve(answer({ ok: true, job: "j1", message: "Downloading." }));
    await settle();
    take("GET /api/action/status").resolve(
      answer({ jobs: [{ id: "j1", action: "install_engine", state: "running", progress: 0.5, message: "Half way" }] })
    );
    await settle();
    const bar = groupsRoot().find((n) => n.tag === "progress");
    assert.ok(bar, "a running job shows progress");
    assert.strictEqual(bar.value, 0.5);
    assert.ok(groupsRoot().texts().includes("Install an engine is running (50% done): Half way"));
    portal.pollJobs();
    await settle();
    take("GET /api/action/status").resolve(
      answer({ jobs: [{ id: "j1", action: "install_engine", state: "done", progress: 1, message: "Installed" }] })
    );
    await settle();
    take("GET /api/state").resolve(answer(statePayload(schema, "f0")));
    await settle();
    assert.strictEqual(document.getElementById("status").textContent, "Install an engine finished: Installed");
    assert.ok(groupsRoot().texts().includes("Install an engine finished: Installed"));

    // A 409 busy is said in words by the action.
    runOf("app_restart").fire("click");
    await settle();
    take("POST /api/action/app_restart").resolve(refusal(409, "busy"));
    await settle();
    assert.ok(groupsRoot().texts().includes("Another action is running. Try again when it finishes."));

    // The machine profile: shown, and apply_profile offered with it.
    const profile = document.getElementById("profile");
    assert.ok(profile.texts().includes("This Mac was detected as: studio."));
    assert.strictEqual(profile.find((n) => n.tag === "select").value, "studio", "the detected profile is chosen");
    runOf("apply_profile").fire("click");
    await settle();
    assert.strictEqual(asked.length, 3, "apply_profile asks first");
    assert.deepStrictEqual(sent(take("POST /api/action/apply_profile")), { params: { profile: "studio" } });

    // Preview from a voice control: the engine from the key, the voice shown.
    buttonIn(cardOf("providers.kokoro.voice"), "Preview").fire("click");
    await settle();
    assert.deepStrictEqual(sent(take("POST /api/action/preview_voice")), {
      params: { engine: "kokoro", voice: "af_heart" }
    });
    assert.deepStrictEqual(pending, []);
  },

  /* A control kind the page does not know still renders, as a text field
   * with a note, and saves a value of the setting's type. An unmet
   * applies_when disables the setting and names what it depends on. */
  async unknown(portal) {
    const strange = {
      key: "future.knob", type: "int", default: 3, label: "A future knob", help: "From a newer registry.",
      group: "advanced", control: "slider", choices: null, choices_from: null, minimum: 0, maximum: 9,
      step: 1, max_length: null, max_items: null, applies_when: null, restart: null, printed: true, print_name: null
    };
    const schema = schemaFixture([strange]);
    await startPage(portal, schema, statePayload(schema, "f0", { "speech.furniture": false }));
    const control = controlOf("future.knob");
    assert.strictEqual(control.tag, "input");
    assert.strictEqual(control.type, "text");
    assert.ok(cardOf("future.knob").texts().some((t) => t.indexOf("does not know the control “slider”") !== -1));
    typeInto(control, "7");
    const write = await pressSave();
    assert.deepStrictEqual(sent(write).changes, { "future.knob": 7 });

    const dependent = controlOf("speech.furniture_max_chars");
    assert.strictEqual(dependent.disabled, true, "an unmet applies_when disables the control");
    assert.ok(
      cardOf("speech.furniture_max_chars").texts().some((t) => t.indexOf("“Skip page furniture” is on") !== -1),
      "and names the setting it depends on"
    );
  },

  /* Notices from the state are shown at the top with what the next save
   * does; a saved setting with a restart field says what must restart. */
  async notices(portal) {
    const schema = schemaFixture();
    const data = statePayload(schema, "f0");
    data.notices = ["'monthly_limit' is retired; delete it."];
    await startPage(portal, schema, data);
    const box = document.getElementById("notices");
    assert.strictEqual(box.hidden, false);
    assert.ok(box.texts().includes("'monthly_limit' is retired; delete it."));
    assert.ok(box.texts().some((t) => t.indexOf("next save removes") !== -1));
    choose(controlOf("app.dictate_mode"), "hold");
    const write = await pressSave();
    write.resolve(answer({ values: data.values, fingerprint: "f1", notices: [], restart: ["the menu-bar app"] }));
    await settle();
    assert.ok(
      cardOf("app.dictate_mode").find((n) => n.className === "restart"),
      "the restart need is shown by the setting after the save"
    );
    assert.ok(document.getElementById("status").textContent.indexOf("Still to restart: the menu-bar app") !== -1);
    assert.strictEqual(box.hidden, true, "the save's own notices replace the old ones");
    take("GET /api/state");
  },

  /* A second save landing while the first save's poll is still out. That
   * older poll read the file before the second write, so its answer must
   * not overwrite what the page just wrote — on screen or in the
   * fingerprint the next save carries. */
  async race(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0"));
    typeInto(controlOf("transcribe.folder"), "~/A");
    const write1 = await pressSave();
    assert.strictEqual(sent(write1).fingerprint, "f0");
    write1.resolve(answer({ values: statePayload(schema, "f1", { "transcribe.folder": "~/A" }).values, fingerprint: "f1", notices: [], restart: [] }));
    await settle();
    const stalePoll = take("GET /api/state");

    typeInto(controlOf("transcribe.folder"), "~/B");
    const write2 = await pressSave();
    assert.strictEqual(sent(write2).fingerprint, "f1", "the second save carries the first write's fingerprint");
    write2.resolve(answer({ values: statePayload(schema, "f2", { "transcribe.folder": "~/B" }).values, fingerprint: "f2", notices: [], restart: [] }));
    await settle();

    stalePoll.resolve(answer(statePayload(schema, "f1", { "transcribe.folder": "~/A" })));
    await settle();
    take("GET /api/state").resolve(answer(statePayload(schema, "f2", { "transcribe.folder": "~/B" })));
    await settle();
    assert.strictEqual(portal.state().values["transcribe.folder"], "~/B", "the page shows the second save");
    assert.strictEqual(controlOf("transcribe.folder").value, "~/B");

    typeInto(controlOf("transcribe.folder"), "~/C");
    assert.strictEqual(sent(await pressSave()).fingerprint, "f2", "the next save carries the second write's fingerprint");
    assert.deepStrictEqual(pending, [], "nothing else is in flight");
  },

  /* A fatal error fills the live region while it is visible and takes the
   * dead page out of the keyboard's reach. */
  async fatal(portal) {
    portal.showError("The portal has closed.", true);
    const alert = document.getElementById("alert");
    assert.strictEqual(alert.hidden, false);
    assert.strictEqual(alert.filledWhileHidden, false, "a live region filled while hidden is not announced");
    assert.strictEqual(main.inert, true, "a dead page still takes keyboard focus");
  },

  /* A setting with both `choices` and `choices_from` offers exactly its
   * choices, in their order; the source only labels the values it knows.
   * An empty choice the source does not label is named by the setting's
   * help, never "empty (not in the list)". */
  async empty_choice(portal) {
    const schema = schemaFixture();
    const withHelp = (key) => schema.settings.find((s) => s.key === key);
    const both = schema.settings.filter((s) => Array.isArray(s.choices) && s.choices_from);
    assert.ok(both.length >= 3, "the registry has settings with both: " + both.length);

    // The source as the server sends it: an entry for the empty value.
    const data = statePayload(schema, "f0");
    data.sources = Object.assign({}, SOURCES, {
      whisper_models: [{ value: "", label: "Use the dictation model" }].concat(SOURCES.whisper_models)
    });
    await startPage(portal, schema, data);
    both.forEach((setting) => {
      const pick = setting.control === "chain" ? null : controlOf(setting.key);
      if (pick === null) return;
      const values = pick.children.map((o) => o.value).filter((v) => v !== "__vocalize_unset__");
      assert.deepStrictEqual(values, setting.choices.map(String), setting.key + ": its choices, in order");
      pick.children.forEach((o) => {
        assert.ok(o.textContent.indexOf("not in the list") === -1, setting.key + ": " + o.textContent);
        assert.ok(o.textContent !== "" && o.textContent !== "empty", setting.key + ": an option with no words");
      });
    });
    const model = withHelp("transcribe.model");
    const transcribe = controlOf(model.key);
    assert.strictEqual(transcribe.children[0].value, "");
    assert.strictEqual(transcribe.children[0].textContent, "Use the dictation model");
    assert.strictEqual(transcribe.children[1].textContent, "base.en", "a value the source knows keeps its label");
    // A choice the source does not list still appears, under its own name.
    assert.ok(transcribe.children.some((o) => o.value === model.choices[model.choices.length - 1]));
    // A setting whose choices hold no empty value never offers one.
    assert.ok(!controlOf("stt.model").children.some((o) => o.value === ""), "the dictation model has no empty choice");
    // An action param with choices_from only keeps the source's list.
    const remove = groupsRoot().find((n) => n.id === "param-remove_model-model");
    assert.ok(remove.children.some((o) => o.value === "base.en"));
  },

  /* Without a label from the source, the empty choice is named by the help. */
  async empty_choice_help(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0"));
    const model = schema.settings.find((s) => s.key === "transcribe.model");
    const first = controlOf(model.key).children[0];
    assert.strictEqual(first.value, "");
    assert.strictEqual(first.textContent, model.help);
  },

  /* What a save says: restart needs in words (with the app's restart
   * button), an unknown need as it is, and the backup once; focus lands on
   * the save's message rather than on a Save button that turned off. */
  async save_message(portal) {
    const schema = schemaFixture();
    const data = statePayload(schema, "f0");
    data.actions.app_restart = { available: true, reason: "", status: "installed" };
    await startPage(portal, schema, data);
    choose(controlOf("app.dictate_mode"), "hold");
    document.getElementById("save").focus();
    const write = await pressSave();
    write.resolve(
      answer({ values: data.values, fingerprint: "f1", notices: [], restart: ["app", "warm", "later"], backup: "/tmp/v/config.toml.bak" })
    );
    await settle();
    const status = document.getElementById("status").textContent;
    assert.ok(status.indexOf("The menu-bar app must be restarted") !== -1, status);
    assert.ok(status.indexOf("dictation model loaded now is let go") !== -1, status);
    assert.ok(status.indexOf("Still to restart: later.") !== -1, "an unknown need is shown as it is: " + status);
    assert.ok(!/restart: (app|warm)\b/.test(status), "no raw token: " + status);
    assert.strictEqual(status.split("/tmp/v/config.toml.bak").length, 2, "the backup is said once: " + status);
    assert.ok(status.indexOf("with its comments") !== -1);
    const note = document.getElementById("save-note");
    assert.strictEqual(note.hidden, false);
    assert.strictEqual(document.activeElement, note, "focus moves to the save's message");
    assert.ok(note.texts().some((t) => t.indexOf("/tmp/v/config.toml.bak") !== -1));
    const cardTexts = cardOf("app.dictate_mode").texts().join(" ");
    assert.ok(cardTexts.indexOf("The menu-bar app must be restarted") !== -1, "the card says it in words");
    take("GET /api/state").resolve(answer(data));
    await settle();
    assert.strictEqual(document.activeElement, note, "the poll after the save keeps the focus");
    buttonIn(note, "Restart the menu-bar app").fire("click");
    await settle();
    take("POST /api/action/app_restart");
    // A save with nothing to restart and no backup says neither.
    typeInto(controlOf("transcribe.folder"), "~/Elsewhere");
    const again = await pressSave();
    again.resolve(answer({ values: data.values, fingerprint: "f2", notices: [], restart: [], backup: null }));
    await settle();
    const later = document.getElementById("status").textContent;
    assert.ok(later.indexOf("config.toml.bak") === -1 && later.indexOf("restart") === -1, later);
    assert.ok(!note.find((n) => n.tag === "button"), "no restart button when nothing needs it");
  },

  /* A variable in the server's environment overrides a setting: its card
   * says so in plain words, names the variable, and says the page edits
   * the file. A card without one says nothing of the kind, and a name the
   * page does not expect is not shown. */
  async overrides(portal) {
    const schema = schemaFixture();
    const data = statePayload(schema, "f0");
    data.overrides = { max_chars: "VOCALIZE_MAX_CHARS", chain: "VOCALIZE_CHAIN", overflow: "<b>x</b>" };
    await startPage(portal, schema, data);
    const said = cardOf("max_chars").texts().join(" ");
    assert.ok(said.indexOf("VOCALIZE_MAX_CHARS overrides this value for commands run from a shell where it is set") !== -1, said);
    assert.ok(said.indexOf("This page edits the config file") !== -1, said);
    assert.ok(cardOf("chain").texts().join(" ").indexOf("VOCALIZE_CHAIN overrides") !== -1);
    const other = cardOf("stt.max_seconds").texts().join(" ");
    assert.ok(other.indexOf("overrides this value") === -1, other);
    assert.ok(cardOf("overflow").texts().join(" ").indexOf("overrides this value") === -1, "an unexpected name is not shown");
    // The note is part of what the control is described by.
    const note = cardOf("max_chars").find((n) => n.id === "env-max_chars");
    assert.ok(note, "the note has an id");
    assert.ok((controlOf("max_chars").attrs["aria-describedby"] || "").split(" ").indexOf(note.id) !== -1);
    // A later state with none set takes the note away.
    portal.refresh();
    await settle();
    const later = statePayload(schema, "f0");
    take("GET /api/state").resolve(answer(later));
    await settle();
    assert.ok(cardOf("max_chars").texts().join(" ").indexOf("overrides this value") === -1);
  },

  /* L5: a backup that still holds settings the save removed says so once,
   * with what to do; one without them says nothing more. */
  async save_message_retired(portal) {
    const schema = schemaFixture();
    const data = statePayload(schema, "f0");
    await startPage(portal, schema, data);
    choose(controlOf("app.dictate_mode"), "hold");
    const write = await pressSave();
    write.resolve(answer({ values: data.values, fingerprint: "f1", notices: [], restart: [],
                           backup: "/tmp/v/config.toml.bak", backup_has_retired: true }));
    await settle();
    const status = document.getElementById("status").textContent;
    const warning = "The copy still holds the old settings that were removed, which can include a service key from an older version, so delete the copy once it is no longer needed.";
    assert.strictEqual(status.split(warning).length, 2, "said once: " + status);
    assert.ok(status.indexOf("/tmp/v/config.toml.bak") < status.indexOf(warning));
    take("GET /api/state").resolve(answer(data));
    await settle();
    typeInto(controlOf("transcribe.folder"), "~/Elsewhere");
    const again = await pressSave();
    again.resolve(answer({ values: data.values, fingerprint: "f2", notices: [], restart: [],
                           backup: "/tmp/v/config.toml.bak", backup_has_retired: false }));
    await settle();
    assert.ok(document.getElementById("status").textContent.indexOf("service key") === -1);
  },

  /* Keyboard and screen reader: errors are tied to their control and
   * announced, focus survives a removal, a move and a reset, and a
   * dangerous action's question names it. */
  async keyboard(portal) {
    const schema = schemaFixture();
    await startPage(portal, schema, statePayload(schema, "f0", { "stt.vocabulary": ["one", "two"] }));
    const status = () => document.getElementById("status").textContent;

    // A number the page refuses: said, tied to the field.
    const seconds = controlOf("stt.max_seconds");
    seconds.focus();
    typeInto(seconds, "99999");
    assert.ok(status().indexOf("Longest recording: Enter") === 0, status());
    const again = controlOf("stt.max_seconds");
    const bad = cardOf("stt.max_seconds").find((n) => n.className === "bad");
    assert.ok(again.attrs["aria-describedby"].split(" ").includes(bad.id));
    assert.strictEqual(document.activeElement, again, "focus stays on the field");
    typeInto(controlOf("stt.max_seconds"), "100");

    // A 400 from the server: focus goes to the first bad control, said with its reason.
    typeInto(controlOf("providers.kokoro.language"), "bad language");
    const write = await pressSave();
    write.resolve(refusal(400, { errors: { "providers.kokoro.language": "Invalid language: no spaces." } }));
    await settle();
    assert.strictEqual(document.activeElement, controlOf("providers.kokoro.language"));
    assert.ok(status().indexOf("Invalid language: no spaces.") !== -1, status());

    // Removing a list item hands focus to the one that took its place.
    cardOf("stt.vocabulary").find((n) => n.id === "remove-stt-vocabulary-0").fire("click");
    assert.strictEqual(document.activeElement && document.activeElement.id, "remove-stt-vocabulary-0");
    assert.strictEqual(cardOf("stt.vocabulary").find((n) => n.id === "item-stt-vocabulary-0").value, "two");
    cardOf("stt.vocabulary").find((n) => n.id === "remove-stt-vocabulary-0").fire("click");
    assert.strictEqual(document.activeElement && document.activeElement.id, "add-stt-vocabulary", "the last one: the adder");

    // Moving an engine to the top follows it to the button it still has.
    const chain = schema.settings.find((s) => s.key === "chain").default;
    if (chain.length >= 2) {
      cardOf("chain").find((n) => n.id === "up-chain-1").fire("click");
      assert.strictEqual(document.activeElement && document.activeElement.id, "down-chain-0");
    }

    // A reset turns its own button off: focus goes to the card, not the body.
    const reset = cardOf("transcribe.folder").find((n) => n.attrs["data-reset"] === "transcribe.folder");
    reset.focus();
    reset.fire("click");
    assert.strictEqual(document.activeElement, cardOf("transcribe.folder"));
    assert.strictEqual(cardOf("transcribe.folder").attrs.tabindex, "-1");

    // A dangerous action's question names it; its error describes its button.
    const run = groupsRoot().find((n) => n.attrs["data-run"] === "clear_audio_cache");
    run.fire("click");
    await settle();
    take("POST /api/action/clear_audio_cache").resolve(refusal(400, "The cache folder is unreadable."));
    await settle();
    const card = groupsRoot().find((n) => n.attrs["data-action"] === "clear_audio_cache");
    const told = card.find((n) => n.className === "bad");
    const button = card.find((n) => n.attrs["data-run"] === "clear_audio_cache");
    assert.ok(button.attrs["aria-describedby"].split(" ").includes(told.id), "the error describes the button");
    assert.ok(status().indexOf("Clear the audio cache did not run: The cache folder is unreadable.") === 0, status());
    // remove_model is danger: true in the fixture; enable it to ask.
    const removeCard = () => groupsRoot().find((n) => n.attrs["data-action"] === "remove_model");
    const removeButton = removeCard().find((n) => n.attrs["data-run"] === "remove_model");
    removeButton.disabled = false;
    confirmAnswer = false;
    removeButton.fire("click");
    await settle();
    const question = asked[asked.length - 1];
    assert.ok(question.indexOf("“Remove a model”") !== -1, "the question names the action: " + question);
    assert.ok(question.indexOf("cannot be undone") !== -1);
    assert.ok(status().indexOf("Remove a model was not run.") === 0);
    confirmAnswer = true;
  },

  /* An empty microphone list says why and what to do, tied to the control. */
  async devices(portal) {
    const schema = schemaFixture();
    const data = statePayload(schema, "f0");
    data.sources = Object.assign({}, SOURCES, { input_devices: [] });
    data.source_notes = { input_devices: "Install a dictation engine to list microphones." };
    await startPage(portal, schema, data);
    const pick = controlOf("stt.input_device");
    assert.deepStrictEqual(pick.children.map((o) => o.value), [""], "only the system default");
    const note = cardOf("stt.input_device").find((n) => n.textContent === "Install a dictation engine to list microphones.");
    assert.ok(note, "the reason is shown");
    assert.ok(pick.attrs["aria-describedby"].split(" ").includes(note.id), "and describes the control");
  },

  /* With no reason from the server, an empty list still says it is empty. */
  async devices_unexplained(portal) {
    const schema = schemaFixture();
    const data = statePayload(schema, "f0");
    data.sources = Object.assign({}, SOURCES, { input_devices: [] });
    await startPage(portal, schema, data);
    assert.ok(cardOf("stt.input_device").texts().some((t) => t.indexOf("No microphones are listed") === 0));
  },

  /* The lists the page builds under `list-style: none` keep their role —
   * Safari drops list semantics with the markers — and hostile text from
   * the state is shown as text. */
  async lists(portal) {
    const schema = schemaFixture();
    const HOSTILE = "<img src=x onerror=alert(1)>";
    await startPage(portal, schema, statePayload(schema, "f0", { "stt.vocabulary": [HOSTILE] }));
    for (const key of ["chain", "stt.vocabulary"]) {
      const list = cardOf(key).find((node) => node.tag === "ol");
      assert.ok(list, key + " builds an <ol>");
      assert.strictEqual(list.attrs.role, "list", key + ": the <ol> keeps its list role");
    }
    assert.strictEqual(cardOf("stt.vocabulary").find((n) => n.id === "item-stt-vocabulary-0").value, HOSTILE);
    assert.strictEqual(groupsRoot().find((n) => n.tag === "img"), null, "never parsed as markup");
  }
};

async function run() {
  const scenario = scenarios[scenarioName];
  assert.ok(scenario, "unknown scenario " + scenarioName + "; one of: " + Object.keys(scenarios).join(", "));
  vm.runInThisContext(fs.readFileSync(scriptPath, "utf8"), { filename: scriptPath });
  await scenario(window.portal);
}

run().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
