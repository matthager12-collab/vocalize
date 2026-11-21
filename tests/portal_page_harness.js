/* Drives vocalize/assets/portal.js without a browser.
 *
 * The script is loaded into this process over a stub `document`, `window`
 * and `fetch`; a scenario calls into `window.portal` and answers the
 * requests the page makes, in the order it chooses. Exit 0 is a pass;
 * anything else prints why. `boot()` never runs — `DOMContentLoaded` never
 * fires here — so the one-time code exchange is not touched.
 *
 *     node tests/portal_page_harness.js vocalize/assets/portal.js <scenario>
 *
 * Run by tests/test_portal_assets.py, one process per scenario, because the
 * page keeps module state (`dead`, the fingerprint, the chain draft).
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

// --- the smallest DOM the page's plumbing touches -------------------------

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
  fire(type) {
    (this.listeners[type] || []).forEach((fn) => fn({ target: this }));
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
// The three banners start hidden, as portal.html declares them.
["alert", "config-alert", "chain-alert"].forEach((id) => {
  byId[id] = new Node("div");
  byId[id].hidden = true;
});
const main = new Node("main");
// The five tabs, as portal.html declares them: Chain selected, each
// controlling its panel. `selectTab` flips these and hides the panels.
const tabs = {};
["chain", "providers", "local", "setup"].forEach((name) => {
  const tab = new Node("button");
  tab.id = "tab-" + name;
  tab.attrs["aria-controls"] = "panel-" + name;
  tab.attrs["aria-selected"] = name === "chain" ? "true" : "false";
  tabs[name] = tab;
});
const TAB = '[role="tab"]';
const SELECTED_TAB = '[role="tab"][aria-selected="true"]';

global.document = {
  body: new Node("body"),
  activeElement: null,
  getElementById(id) {
    return (byId[id] = byId[id] || new Node("div"));
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
    if (selector === "main") return main;
    if (selector === SELECTED_TAB) {
      return Object.values(tabs).find((tab) => tab.attrs["aria-selected"] === "true") || null;
    }
    return null;
  },
  querySelectorAll(selector) {
    return selector === TAB ? Object.values(tabs) : [];
  },
  addEventListener() {}
};
global.window = {
  location: { host: "127.0.0.1:1", port: "1", hash: "", pathname: "/" },
  history: { replaceState() {} },
  sessionStorage: {
    getItem() {
      return null;
    },
    setItem() {}
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

const answer = (data) => ({
  ok: true,
  status: 200,
  json: async () => data,
  headers: { get: () => "" }
});
const refusal = (status, error) => ({
  ok: false,
  status: status,
  json: async () => ({ error: error }),
  headers: { get: () => "" }
});

/** Let every continuation waiting on a settled promise run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function statePayload(fingerprint, chain, providers) {
  return {
    rows: [],
    chain: chain,
    chain_source: "config file",
    providers: providers || {},
    stt: {},
    app: {},
    config_path: "/tmp/config.toml",
    config_error: null,
    fingerprint: fingerprint
  };
}

/** One `providers` entry as `/api/state` shapes it. */
function providerEntry(label, key, voice) {
  return {
    label: label,
    in_chain: true,
    budget: null,
    used: 0,
    exhausted: false,
    settings: { voice: voice || "", model: "", speed: null, language: null, region: null, profile: null },
    error: null,
    key: key || { source: "not found", masked: null }
  };
}

/** The controls of every `field()` under `root` whose label reads `text`. */
function controls(root, text) {
  return root
    .findAll((node) => node.className === "field" && node.children[0].textContent === text)
    .map((wrap) => wrap.children[1]);
}

/** The `field()` wrapper holding `control`. */
function wrapperOf(root, control) {
  return root.find((node) => node.className === "field" && node.children[1] === control);
}

/** Choose `item` in a stub `<select>`, the way a browser leaves the DOM. */
function choose(select, item) {
  select.children.forEach((option) => {
    option.selected = option === item;
  });
  select.value = item.value;
  select.fire("change");
}

function noBanner() {
  assert.strictEqual(document.getElementById("alert").hidden, true, "no banner");
}

// --- scenarios ------------------------------------------------------------

const scenarios = {
  /* A second save landing while the first save's poll is still out. That
   * older poll read the file before the second write, so its answer must
   * not overwrite what the page just wrote — on screen or in the
   * fingerprint the next save carries. */
  async race(portal) {
    portal.refresh();
    take("GET /api/state").resolve(answer(statePayload("f0", ["say"])));
    await settle();

    const first = portal.save("/api/chain", { order: ["a"] });
    await settle();
    const write1 = take("POST /api/chain");
    assert.strictEqual(sent(write1).fingerprint, "f0");
    write1.resolve(answer({ ok: true, fingerprint: "f1" }));
    await settle();
    const stalePoll = take("GET /api/state"); // the first save's poll, still in flight

    const second = portal.save("/api/chain", { order: ["b"] });
    await settle();
    const write2 = take("POST /api/chain");
    assert.strictEqual(sent(write2).fingerprint, "f1", "the second save carries the first write's fingerprint");
    write2.resolve(answer({ ok: true, fingerprint: "f2" }));
    await settle();

    // The older poll answers last, with what the file held before the second write.
    stalePoll.resolve(answer(statePayload("f1", ["a"])));
    await settle();
    // A fresh poll, if the page started one, answers with the current file.
    const fresh = pending.find((request) => request.key === "GET /api/state");
    if (fresh) {
      take(fresh.key).resolve(answer(statePayload("f2", ["b"])));
      await settle();
    }
    await first;
    await second;

    assert.deepStrictEqual(portal.state().chain, ["b"], "the screen shows the second save");
    portal.save("/api/chain", { order: ["c"] });
    await settle();
    assert.strictEqual(
      sent(take("POST /api/chain")).fingerprint,
      "f2",
      "the next save carries the second write's fingerprint"
    );
    assert.deepStrictEqual(pending, [], "nothing else is in flight");
  },

  /* A refused key does not stay in the field, and the next keystroke in the
   * form clears the banner that quoted the refusal. */
  async fatal(portal) {
    portal.showError("The portal has closed.", true);
    const alert = document.getElementById("alert");
    assert.strictEqual(alert.hidden, false);
    assert.strictEqual(alert.filledWhileHidden, false, "a live region filled while hidden is not announced");
    assert.strictEqual(main.inert, true, "a dead page still takes keyboard focus");
  },

  /* The voice control. A list comes from a third-party API through the
   * server, and while none has arrived the field is a text box for an id.
   * When one lands it becomes a `<select>`: every voice as a created
   * `<option>` (`.value` the id, `.textContent` the name, verbatim and
   * inert), the box's current id selected — prepended as its own option if
   * the list does not know it, so nothing changes under him — and a last
   * "Type a voice id…" that brings the text box back. Choosing fetches
   * nothing and saves nothing: Save reads the box the select filled. A good
   * list is asked for once per provider per page load, on the first focus;
   * a 502 or an "unavailable" answer leaves the text box, is not remembered,
   * and raises no banner; storing a key asks for that provider's list again.
   *
   * Why the datalist had to go: a `<datalist>` filters its options by the
   * field's current text, so a Kokoro field already holding "af_heart"
   * offered one entry, or none. The old scenario rendered every field
   * empty and never saw it. */
  async voices(portal) {
    const HOSTILE = ["<img src=x onerror=alert(1)>", "javascript:alert(1)"];
    const data = statePayload("f0", ["elevenlabs"], {
      elevenlabs: providerEntry("ElevenLabs"),
      google: providerEntry("Google Cloud"),
      say: providerEntry("macOS say"),
      kokoro: providerEntry("Kokoro", null, "af_heart"),
      openai: providerEntry("OpenAI", null, "my-cloned-voice")
    });
    // A loaded page, so Save is allowed (it needs the fingerprint).
    portal.refresh();
    take("GET /api/state").resolve(answer(data));
    await settle();

    const panel = new Node("section");
    portal.renderers.providers(panel, data);
    const boxes = controls(panel, "Voice id");
    const picks = controls(panel, "Voice");
    assert.strictEqual(boxes.length, 5, "every card has a text box for an id");
    assert.strictEqual(picks.length, 5, "and a select waiting for a list");
    picks.forEach((pick) => assert.strictEqual(pick.tag, "select"));
    boxes.forEach((box) => assert.strictEqual(wrapperOf(panel, box).hidden, false, "no list yet: the box shows"));
    picks.forEach((pick) => assert.strictEqual(wrapperOf(panel, pick).hidden, true, "no list yet: no select"));
    assert.deepStrictEqual(pending, [], "nothing is fetched until a field is focused");
    const hints = panel.findAll((node) => node.tag === "p" && node.className === "hint");

    // ElevenLabs: two hostile names, one fetch for two focuses, an empty
    // current value, and the focus he gave the box lands on the select.
    boxes[0].focus();
    boxes[0].focus();
    const first = take("GET /api/voices/elevenlabs");
    assert.strictEqual(pending.length, 0, "one fetch per provider, not one per focus");
    first.resolve(answer({
      voices: [{ id: HOSTILE[0], name: HOSTILE[0] }, { id: HOSTILE[1], name: "Voice " + HOSTILE[1] }],
      source: "live"
    }));
    await settle();
    assert.strictEqual(wrapperOf(panel, picks[0]).hidden, false, "the list arrived: the select shows");
    assert.strictEqual(wrapperOf(panel, boxes[0]).hidden, true, "and the box is put away");
    assert.strictEqual(document.activeElement, picks[0], "focus moves with the field");
    const options = picks[0].children;
    assert.strictEqual(options.length, 4, "default + two voices + type-your-own");
    options.forEach((item) => assert.strictEqual(item.tag, "option"));
    assert.strictEqual(options[0].value, "", "an empty current value is offered as itself");
    assert.strictEqual(options[0].textContent, "vocalize's default");
    assert.strictEqual(options[0].selected, true, "and it is the one selected");
    assert.strictEqual(options[1].value, HOSTILE[0], "the value is the id, verbatim");
    assert.strictEqual(options[1].textContent, HOSTILE[0], "the text is the name, verbatim, as text");
    assert.strictEqual(options[1].label, undefined, "nothing goes through an attribute");
    assert.strictEqual(options[2].value, HOSTILE[1]);
    assert.strictEqual(options[2].textContent, "Voice " + HOSTILE[1]);
    assert.strictEqual(options[2].selected, false);
    assert.strictEqual(options[3].textContent, "Type a voice id…");
    assert.ok(hints.some((node) => node.textContent.indexOf("live from ElevenLabs") !== -1), "the source is said in words");

    // Choosing sets the box and nothing else: no fetch, no save.
    choose(picks[0], options[2]);
    assert.strictEqual(boxes[0].value, HOSTILE[1], "the pick lands in the box Save reads");
    assert.deepStrictEqual(pending, [], "choosing a voice fetches nothing and saves nothing");
    assert.strictEqual(wrapperOf(panel, boxes[0]).hidden, true);

    // "Type a voice id…" brings the box back, with focus, keeping its value.
    choose(picks[0], options[3]);
    assert.strictEqual(wrapperOf(panel, boxes[0]).hidden, false, "the box is revealed");
    assert.strictEqual(document.activeElement, boxes[0], "and focused, so a keyboard user can type");
    assert.strictEqual(boxes[0].value, HOSTILE[1], "revealing the box does not change its value");
    assert.deepStrictEqual(pending, []);

    // Save is the only write, and it carries what the select put in the box.
    panel.find((node) => node.textContent === "Save ElevenLabs").fire("click");
    await settle();
    const write = take("POST /api/provider/elevenlabs");
    assert.strictEqual(sent(write).settings.voice, HOSTILE[1]);
    write.resolve(answer({ ok: true, fingerprint: "f1" }));
    await settle();
    take("GET /api/state").resolve(answer(data));
    await settle();
    assert.deepStrictEqual(pending, []);

    // Kokoro: the field already holds "af_heart" — the case the datalist
    // lost. The list knows it, so it is selected and nothing is prepended.
    boxes[3].focus();
    take("GET /api/voices/kokoro").resolve(answer({
      voices: [{ id: "af_heart", name: "Heart" }, { id: "af_bella", name: "Bella" }],
      source: "builtin"
    }));
    await settle();
    const kokoro = picks[3].children;
    assert.strictEqual(kokoro.length, 3, "two voices + type-your-own, nothing prepended");
    assert.strictEqual(kokoro[0].value, "af_heart");
    assert.strictEqual(kokoro[0].textContent, "Heart");
    assert.strictEqual(kokoro[0].selected, true, "the current voice is the one selected");
    assert.strictEqual(kokoro[1].selected, false);
    assert.strictEqual(boxes[3].value, "af_heart", "showing the list changes nothing");

    // OpenAI: a current value the list does not know is kept, first and selected.
    boxes[4].focus();
    take("GET /api/voices/openai").resolve(answer({ voices: [{ id: "alloy", name: "Alloy" }], source: "builtin" }));
    await settle();
    const openai = picks[4].children;
    assert.strictEqual(openai.length, 3);
    assert.strictEqual(openai[0].value, "my-cloned-voice", "an off-list current value is prepended");
    assert.strictEqual(openai[0].textContent, "my-cloned-voice (not in this list)");
    assert.strictEqual(openai[0].selected, true);
    assert.strictEqual(openai[1].value, "alloy");
    assert.strictEqual(boxes[4].value, "my-cloned-voice");

    // Google: a 502 is normal — words beside the field, the box stays, no banner.
    boxes[1].focus();
    take("GET /api/voices/google").resolve(refusal(502, "Could not fetch this provider's voice list."));
    await settle();
    assert.ok(hints.some((node) => node.textContent.indexOf("type a voice id") !== -1), "a failed list says to type an id");
    assert.strictEqual(wrapperOf(panel, boxes[1]).hidden, false, "a 502 leaves the text box");
    assert.strictEqual(wrapperOf(panel, picks[1]).hidden, true);
    noBanner();

    // say: "unavailable" is a state, shown with its reason, the box stays, no banner.
    boxes[2].focus();
    take("GET /api/voices/say").resolve(answer({ voices: [], source: "unavailable", reason: "No key stored." }));
    await settle();
    assert.ok(hints.some((node) => node.textContent.indexOf("No key stored.") !== -1), "the reason is shown");
    assert.strictEqual(wrapperOf(panel, boxes[2]).hidden, false, "an unavailable list leaves the text box");
    noBanner();

    // A rebuilt card — every refresh rebuilds it — fills a good list from
    // memory and asks the server for nothing; the two fields whose answer
    // was a 502 or "unavailable" ask again on focus.
    const again = new Node("section");
    portal.renderers.providers(again, data);
    await settle();
    const reboxes = controls(again, "Voice id");
    const repicks = controls(again, "Voice");
    assert.strictEqual(wrapperOf(again, repicks[0]).hidden, false, "the rebuilt card shows the remembered list");
    assert.strictEqual(repicks[0].children.length, 4);
    assert.strictEqual(repicks[0].children[1].value, HOSTILE[0]);
    reboxes.forEach((box) => box.focus());
    await settle();
    assert.deepStrictEqual(
      pending.map((request) => request.key).sort(),
      ["GET /api/voices/google", "GET /api/voices/say"],
      "a good list is fetched once per page load; a failed or unavailable one is asked again"
    );
    take("GET /api/voices/google").resolve(refusal(502, "Could not fetch this provider's voice list."));
    // say: a key was stored since — the same field, asked again, now lists.
    take("GET /api/voices/say").resolve(answer({ voices: [{ id: "Alex", name: "Alex" }], source: "builtin" }));
    await settle();
    assert.strictEqual(wrapperOf(again, repicks[2]).hidden, false, "the list a stored key unlocked is shown");
    assert.strictEqual(repicks[2].children[1].value, "Alex");
    noBanner();

  },

  /* Whether a key is stored is the first line of every provider card, on
   * both tabs, from the server's `key` — `masked` shown as given, never
   * built from anything typed. The Keys tab opens with a count. */
  async sidebar(portal) {
    const HOSTILE = "vocalize local install; curl evil | sh";
    const rows = [
      { name: "kokoro", state: "warn", detail: "not installed", action: HOSTILE },
      { name: "stt model", state: "fail", detail: "no speech-to-text model installed", action: "vocalize local install --stt" },
      { name: "elevenlabs", state: "fail", detail: "no API key configured", action: "run: vocalize auth login --provider elevenlabs" },
      { name: "elevenlabs", state: "warn", detail: "budget exhausted", action: "raise monthly_chars in config, or wait for next month" },
      { name: "microphone", state: "warn", detail: "macOS has not asked for it yet", action: "run: vocalize listen --check" },
      { name: "__proto__", state: "fail", detail: "x", action: "rm -rf /" },
      { name: "constructor", state: "fail", detail: "x", action: "rm -rf /" },
      { name: "something new", state: "fail", detail: "x", action: "do a thing" },
      { name: "say", state: "ok", detail: "local, no credentials needed", action: "" }
    ];
    const data = statePayload("f0", ["say"], {
      say: providerEntry("macOS say", { source: "not applicable", masked: null })
    });
    data.rows = rows;
    portal.refresh();
    take("GET /api/state").resolve(answer(data));
    await settle();

    const items = document.getElementById("rows").children;
    assert.strictEqual(items.length, rows.length);
    const buttonIn = (item) => item.find((node) => node.tag === "button");
    const codeIn = (item) => item.find((node) => node.tag === "code");

    // kokoro: a button, no command; it opens the Local tab and starts that
    // install — with a body built from the table, not from the row.
    const kokoro = buttonIn(items[0]);
    assert.ok(kokoro, "a row the portal can act on gets a button");
    assert.strictEqual(kokoro.textContent, "Install Kokoro");
    assert.strictEqual(codeIn(items[0]), null, "and no command to copy");
    kokoro.fire("click");
    await settle();
    assert.strictEqual(tabs.local.attrs["aria-selected"], "true", "the Local tab is opened");
    assert.strictEqual(document.getElementById("panel-local").hidden, false);
    take("GET /api/local/install/status"); // the Local tab's own poll, left in flight
    assert.deepStrictEqual(sent(take("POST /api/local/install/start")), { target: "kokoro" });
    assert.strictEqual(kokoro.disabled, true, "pressed once");

    // stt model: the same install the Local tab's button runs, with its model.
    const stt = buttonIn(items[1]);
    assert.strictEqual(stt.textContent, "Install speech to text");
    stt.fire("click");
    await settle();
    assert.deepStrictEqual(sent(take("POST /api/local/install/start")), { target: "stt", model: "large-v3-turbo-q5_0" });

    [2, 3].forEach((index) => {
      assert.strictEqual(buttonIn(items[index]), null);
      assert.strictEqual(codeIn(items[index]).textContent, rows[index].action);
    });

    // The microphone needs macOS's own dialog: the command, and why.
    assert.strictEqual(buttonIn(items[4]), null);
    assert.strictEqual(codeIn(items[4]).textContent, "run: vocalize listen --check");
    const why = items[4].find((node) => node.className === "hint");
    assert.ok(why && why.textContent.indexOf("terminal") !== -1, "it says a terminal is needed, and why");

    // A name not in the table — prototype names included — is text, never a button.
    [5, 6, 7].forEach((index) => {
      assert.strictEqual(buttonIn(items[index]), null, rows[index].name + " gets no button");
      assert.strictEqual(codeIn(items[index]).textContent, rows[index].action);
    });
    // No action: nothing at all.
    assert.strictEqual(buttonIn(items[8]), null);
    assert.strictEqual(codeIn(items[8]), null);

    // Nothing from any row's action string reached a request.
    const actions = rows.map((row) => row.action).filter(Boolean);
    log.forEach((request) => {
      const wire = request.key + " " + (request.options.body || "");
      actions.forEach((action) => assert.strictEqual(wire.indexOf(action), -1, "an action string reached the wire: " + action));
    });
    assert.deepStrictEqual(pending, [], "nothing else is in flight");
  },

  /* The Setup tab: its two buttons post the routes the Local tab already
   * uses (target "stt" for dictation, the new target "app"), and a hostile
   * row action reaches the page only inside a <code> node's text — the
   * same guarantee `sidebar` pins, proven again through this tab's own
   * lookup-by-name. */
  async setup(portal) {
    const HOSTILE = '<img onerror="alert(1)"> ; rm -rf /';
    const rows = [
      { name: "recorder", state: "ok", detail: "Vocalize Recorder is built", action: "" },
      { name: "app", state: "warn", detail: "stale — run: vocalize app install", action: HOSTILE },
      { name: "app agent", state: "fail", detail: "not running", action: "vocalize app install" },
      { name: "stt model", state: "fail", detail: "no speech-to-text model installed", action: "vocalize local install --stt" },
      { name: "microphone", state: "warn", detail: "macOS has not asked for it yet", action: "run: vocalize listen --check" },
      { name: "accessibility", state: "warn", detail: "not granted", action: "grant Accessibility to Vocalize in System Settings" }
    ];
    const data = statePayload("f0", ["say"], { say: providerEntry("macOS say") });
    data.rows = rows;
    data.app = {
      bundle: "stale",
      agent: "not running",
      accessibility: "not granted",
      hotkeys: "control-option-command-D/V",
      hotkey_backend: "carbon",
      vocalize: "/opt/homebrew/bin/vocalize"
    };

    const panel = new Node("section");
    portal.renderers.setup(panel, data);

    const buttons = panel.findAll((node) => node.tag === "button");
    const dictate = buttons.find((b) => b.textContent === "Install dictation");
    const installApp = buttons.find((b) => b.textContent === "Install the app");
    assert.ok(dictate, "step 2 offers to install dictation");
    assert.ok(installApp, "step 2 offers to install the app");

    dictate.fire("click");
    await settle();
    assert.deepStrictEqual(sent(take("POST /api/local/install/start")), {
      target: "stt",
      model: "large-v3-turbo-q5_0"
    });

    installApp.fire("click");
    await settle();
    assert.deepStrictEqual(sent(take("POST /api/local/install/start")), { target: "app" });

    // The "app" row's hostile action reached only a <code> node's text.
    const codeNodes = panel.findAll((node) => node.tag === "code");
    assert.ok(
      codeNodes.some((node) => node.textContent === HOSTILE),
      "the hostile action reached a <code> node as text"
    );
    assert.strictEqual(panel.find((node) => node.tag === "img"), null, "never parsed as markup");

    take("GET /api/local/install/status"); // the poll the render arms, so an
    // install already running when the tab opens paints here
    assert.deepStrictEqual(pending, [], "nothing else is in flight");
  },

  /* An install started from the Setup tab paints where the user is
   * looking. The worker's step, its `note` — the Accessibility re-grant
   * warning after a rebuild drops the grant — and any `error` all reach
   * the Setup panel, and the "app" target is named as itself, not as
   * Kokoro. */
  async setup_progress(portal) {
    const NOTE = "Vocalize.app was rebuilt — re-grant it Accessibility in System Settings.";
    const data = statePayload("f0", ["say"], { say: providerEntry("macOS say") });
    data.rows = [{ name: "app", state: "warn", detail: "stale", action: "vocalize app install" }];

    const panel = new Node("section");
    portal.renderers.setup(panel, data);

    const installApp = panel.find((node) => node.tag === "button" && node.textContent === "Install the app");
    installApp.fire("click");
    await settle();
    const started = take("POST /api/local/install/start");
    assert.deepStrictEqual(sent(started), { target: "app" });
    started.resolve(
      answer({
        running: true,
        target: "app",
        step: "building the app",
        downloaded: 0,
        total: 0,
        done: false,
        error: "",
        note: NOTE
      })
    );
    await settle();

    const texts = panel.findAll(() => true).map((node) => node.textContent);
    assert.ok(texts.includes("The app: building the app"), "the step shows in the Setup panel: " + texts.join(" | "));
    assert.ok(texts.includes(NOTE), "the re-grant note shows in the Setup panel");

    // The poll the render armed: a failure reaches the same panel.
    take("GET /api/local/install/status").resolve(
      answer({
        running: false,
        target: "app",
        step: "failed",
        downloaded: 0,
        total: 0,
        done: false,
        error: "xcodebuild exited 65",
        note: ""
      })
    );
    await settle();
    assert.ok(
      panel.findAll(() => true).some((node) => node.textContent === "xcodebuild exited 65"),
      "the failure shows in the Setup panel"
    );

    take("GET /api/state"); // the refresh the finished install triggers
    assert.deepStrictEqual(pending, [], "nothing else is in flight");
  },

  /* The lists the page builds under `list-style: none` keep their role —
   * Safari drops list semantics with the markers. */
  async lists(portal) {
    const panel = new Node("section");
    portal.renderers.chain(
      panel,
      statePayload("f0", ["say"], { say: { label: "macOS say" }, kokoro: { label: "Kokoro" } })
    );
    for (const tag of ["ol", "ul"]) {
      const list = panel.find((node) => node.tag === tag);
      assert.ok(list, "the chain panel builds a <" + tag + ">");
      assert.strictEqual(list.attrs.role, "list", "<" + tag + "> keeps its list role");
    }
  }
};

async function run() {
  const [scriptPath, name] = process.argv.slice(2);
  const scenario = scenarios[name];
  assert.ok(scenario, "unknown scenario " + name + "; one of: " + Object.keys(scenarios).join(", "));
  vm.runInThisContext(fs.readFileSync(scriptPath, "utf8"), { filename: scriptPath });
  await scenario(window.portal);
}

run().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
