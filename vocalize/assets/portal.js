/* The config portal page: the session exchange, one fetch helper, the state
 * cache with its config fingerprint, the readiness sidebar, and one place
 * that shows an error. The five tab panels are filled in from `renderers`.
 *
 * Two rules run through the whole file:
 *
 *   1. The one-time code is exchanged EXACTLY ONCE, on load, and the
 *      fragment is stripped before the request goes out. Every failed
 *      exchange — a wrong code, a replayed one, a malformed body — counts
 *      toward a five-strike lockout that closes the server, so a page that
 *      retries bricks the portal in five goes. There is no retry here: not
 *      on failure, not on reload, not on focus, not in a catch block.
 *   2. The session token travels in the `X-Vocalize-Token` header and
 *      nowhere else. A token in a query string is refused by the server
 *      before it routes, deliberately.
 */

"use strict";

/* Requests are same-origin and relative, always: the server pins `Host` to
 * its own loopback address and refuses a foreign `Origin`, so a rewritten
 * base URL or an alias hostname is a flat 403 on every route. */

var GONE =
  "The portal has closed. Run `vocalize portal` again to get a fresh link.";
var STALE =
  "This page's session is over. Run `vocalize portal` again to get a fresh link.";

/* Ping every 30 s. The server closes itself after 900 s with no
 * token-authenticated request, so 30 s is a thirtieth of the budget and
 * keeps an open page alive with room to spare; it is also how long the page
 * can take to notice the CLI stopping, which is short enough to feel
 * immediate. `/api/state` is NOT polled on a timer: it costs up to two 2 s
 * probe batches and can wake a keychain dialog. It is fetched on load,
 * after every successful write, and when the user asks. */
var PING_MS = 30000;

var token = null;
var dead = false;
var state = null; // the last /api/state payload
var fingerprint = null; // the config file's fingerprint from that payload
var pingTimer = null;
var polling = null; // the in-flight refresh, so polls never stack
/* Bumped by every poll and every successful write. A poll applies its
 * answer only if nothing newer started while it was out: an older poll's
 * read predates a write that has since landed, and the page already holds
 * that write's fingerprint from the write's own answer. */
var generation = 0;

/** Tab panel renderers, filled in by the tab code: `renderers.chain =
 *  function (panel, state) {...}`. Called for the visible tab on every
 *  refresh and on every tab switch. */
var renderers = {};

// --- elements ---------------------------------------------------------

function $(id) {
  return document.getElementById(id);
}

function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== "") node.textContent = text;
  return node;
}

// --- the one place an error is shown ----------------------------------

/** Show one line of trouble. Every tab calls this rather than inventing
 *  its own banner. `fatal` marks the page unusable and stops the polling. */
function showError(message, fatal) {
  var box = $("alert");
  // Shown first, then filled: it is a live region, and a change made while
  // it is hidden is not announced.
  box.hidden = false;
  box.replaceChildren(el("strong", null, fatal ? "This page cannot continue" : "That did not work"));
  box.appendChild(el("p", null, message));
  if (fatal) {
    dead = true;
    document.body.classList.add("dead");
    document.querySelector("main").inert = true; // out of the tab order, not only the mouse's reach
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }
}

function clearError() {
  if (!dead) $("alert").hidden = true;
}

function banner(id, heading, message, mild) {
  var box = $(id);
  if (!message) {
    box.hidden = true;
    return;
  }
  box.className = mild ? "banner mild" : "banner";
  box.replaceChildren(el("strong", null, heading));
  box.appendChild(el("p", null, message));
  box.hidden = false;
}

// --- the fetch helper -------------------------------------------------

/* Status to result kind. 403 is "your session is over" — a wrong token, a
 * refused Host or a refused Origin all land here and all mean this page can
 * do nothing more. 409 is the compare-and-swap refusal: the file moved
 * under us. 400/404/413 are refusals of what was sent. 503 is either the
 * lockout (the whole server is going away) or a preview already running,
 * told apart by the message below. */
var KINDS = {
  400: "invalid",
  402: "budget",
  403: "auth",
  404: "missing",
  409: "conflict",
  413: "invalid",
  500: "error",
  502: "error",
  503: "busy"
};

/**
 * One request. Returns `{ok: true, data}` or `{ok: false, kind, status,
 * message}` with `kind` one of: auth (session over — the page is dead),
 * gone (the portal is not there at all), conflict (409, reload before
 * writing again), invalid (the request was refused), budget, missing, busy,
 * error.
 *
 * `raw` asks for the response body as a Blob instead of JSON — the preview
 * route answers with audio bytes, not JSON.
 */
async function api(method, path, body, raw) {
  if (dead) return { ok: false, kind: "gone", status: 0, message: GONE };
  var headers = { "X-Vocalize-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  var response;
  try {
    response = await fetch(path, {
      method: method,
      headers: headers,
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch (err) {
    // The socket is gone: idle timeout, Ctrl-C, or the process exited.
    // Never an invitation to re-exchange the one-time code.
    showError(GONE, true);
    return { ok: false, kind: "gone", status: 0, message: GONE };
  }

  if (response.ok) {
    if (raw) {
      return {
        ok: true,
        data: await response.blob(),
        type: response.headers.get("Content-Type") || ""
      };
    }
    return { ok: true, data: await response.json() };
  }

  var message = "";
  try {
    message = (await response.json()).error || "";
  } catch (err) {
    message = "";
  }
  var kind = KINDS[response.status] || "error";
  if (response.status === 503 && message.indexOf("was shut down") !== -1) {
    kind = "gone"; // the lockout message: the server is closing its socket
  }
  if (kind === "gone") showError(message || GONE, true);
  if (kind === "auth") showError(STALE, true);
  return {
    ok: false,
    kind: kind,
    status: response.status,
    message: message || "The portal refused that (" + response.status + ")."
  };
}

// --- state, and the fingerprint the writes need ------------------------

/** Fetch `/api/state`, cache it, redraw. Polls never stack: a second call
 *  while one is in flight waits for that one. */
function refresh() {
  if (polling === null) polling = poll();
  return polling;
}

/** A fresh poll whatever is in flight. After a write, an in-flight poll's
 *  read predates it, so joining that poll would show the file as it was. */
function poll() {
  var mine = load().finally(function () {
    if (polling === mine) polling = null;
  });
  polling = mine;
  return mine;
}

async function load() {
  var mine = ++generation;
  $("recheck").disabled = true;
  var result = await api("GET", "/api/state");
  // Superseded: a write, or a newer poll, started while this one was out,
  // so what it read is older than what the page already knows.
  if (mine !== generation) return result;
  $("recheck").disabled = dead;
  if (!result.ok) {
    if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
    return result;
  }
  state = result.data;
  // `fingerprint` is taken by the server before it parses the file, and a
  // write hands it straight back. null means the path could not be read at
  // all: nothing can be written until a later poll returns one.
  fingerprint = state.fingerprint;
  render();
  return result;
}

/** True when a write can be attempted at all. */
function canWrite() {
  return !dead && fingerprint !== null;
}

/**
 * Write to a config route (`/api/chain`, `/api/provider/<name>`,
 * `/api/stt`), carrying the fingerprint the page was given and taking the
 * next one from the answer, so consecutive writes need no poll in between.
 *
 * On 409 the file moved under us: the page reloads the state and the caller
 * must let the user look at the new values and confirm again. It never
 * re-sends the same write with the fresh fingerprint — that would overwrite
 * a change nobody saw, which is the whole point of the refusal.
 */
async function save(path, body) {
  if (!canWrite()) {
    var why = dead ? GONE : "The config file could not be read, so nothing can be saved yet.";
    showError(why, dead);
    return { ok: false, kind: "invalid", status: 0, message: why };
  }
  var payload = Object.assign({}, body, { fingerprint: fingerprint });
  var result = await api("POST", path, payload);
  if (result.ok) {
    clearError();
    // From the write's own answer, and nothing older may replace it: a
    // poll already in flight read the file before this write landed.
    generation += 1;
    fingerprint = result.data.fingerprint;
    await poll(); // the sidebar is the point of the page: redraw it now
    return result;
  }
  if (result.kind === "conflict") {
    await poll();
    showError(
      "The config file changed on disk while you were editing, so nothing was saved. " +
        "The page has reloaded it — check the values and try again.",
      false
    );
  } else if (result.kind !== "gone" && result.kind !== "auth") {
    showError(result.message, false);
  }
  return result;
}

// --- rendering --------------------------------------------------------

var CHAIN_SOURCES = ["default", "config file", "environment", "flag"];

function render() {
  $("config-path").textContent = state.config_path;

  // A config_error means the whole file was discarded and every other value
  // on this page is a default, not the user's setting. It outranks
  // everything else here.
  banner(
    "config-alert",
    "The config file did not load — every value below is a default",
    state.config_error ? state.config_error + " Fix the file at " + state.config_path + "." : ""
  );

  // chain_source is overloaded: either a provenance word or an error about
  // VOCALIZE_CHAIN. Tested by membership, never by matching the text.
  var envError = CHAIN_SOURCES.indexOf(state.chain_source) === -1 ? state.chain_source : "";
  banner("chain-alert", "VOCALIZE_CHAIN in the environment is not usable", envError, true);

  renderRows();
  renderPanel(currentTab());
}

function renderRows() {
  var list = $("rows");
  var rows = state.rows || [];
  list.replaceChildren();
  rows.forEach(function (row) {
    var known = row.state === "ok" || row.state === "warn" || row.state === "fail";
    var item = el("li", known ? row.state : "warn");
    var head = el("div", "row-head");
    head.appendChild(el("span", "dot"));
    head.appendChild(el("span", "row-name", row.name));
    head.appendChild(el("span", "row-state", row.state));
    item.appendChild(head);
    if (row.detail) item.appendChild(el("p", "row-detail", row.detail));
    if (row.action) item.appendChild(rowAction(row));
    list.appendChild(item);
  });

  var note = "";
  if (!rows.length) note = "Nothing to check yet.";
  else if (fingerprint === null) note = "Saving is off: the config file could not be read.";
  else if (fingerprint === "absent") note = "No config file yet — the first save creates it.";
  $("rows-note").textContent = note;
  $("rows-note").hidden = note === "";
}

/* One line of good news, waiting for the panel that earned it. A save
 * redraws its panel from the fresh state, so a handler cannot leave a
 * message on a node it is holding — the node is gone by then. `showError`
 * stays the only place trouble is shown; this is only the other half. */
var flash = null;

function setFlash(panel, text) {
  flash = { panel: panel, text: text };
}

function renderPanel(name) {
  var panel = $("panel-" + name);
  if (!panel) return;
  if (!state) return;
  var draw = renderers[name];
  if (!draw) {
    panel.replaceChildren(el("p", "empty", "Nothing here yet."));
    return;
  }
  draw(panel, state);
  if (flash !== null && flash.panel === name) {
    panel.appendChild(el("p", "flash", flash.text));
    flash = null; // said once, on the panel it belongs to
  }
}

// --- tabs -------------------------------------------------------------

function currentTab() {
  var selected = document.querySelector('[role="tab"][aria-selected="true"]');
  return selected ? selected.id.slice(4) : "chain";
}

function selectTab(name) {
  document.querySelectorAll('[role="tab"]').forEach(function (tab) {
    var mine = tab.id === "tab-" + name;
    tab.setAttribute("aria-selected", mine ? "true" : "false");
    $(tab.getAttribute("aria-controls")).hidden = !mine;
  });
  renderPanel(name);
}

// --- boot: the one-time exchange, once --------------------------------

/** Read the code out of the fragment and strip the fragment, whatever
 *  happens next. Stripped before the exchange goes out, so a reload cannot
 *  resend a code that has already been spent. */
function takeCode() {
  var match = /^#code=([A-Za-z0-9_-]+)$/.exec(window.location.hash);
  window.history.replaceState(null, "", window.location.pathname);
  return match ? match[1] : null;
}

function storageKey() {
  return "vocalize-portal-token:" + window.location.port;
}

function remember(value) {
  try {
    window.sessionStorage.setItem(storageKey(), value);
  } catch (err) {
    /* private mode, or storage disabled — the page just will not survive a reload */
  }
}

function remembered() {
  try {
    return window.sessionStorage.getItem(storageKey());
  } catch (err) {
    return null;
  }
}

async function exchange(code) {
  var response;
  try {
    response = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });
  } catch (err) {
    showError(GONE, true);
    return null;
  }
  if (!response.ok) {
    var message = "";
    try {
      message = (await response.json()).error || "";
    } catch (err2) {
      message = "";
    }
    // No retry. Ever. Each failed exchange counts toward the five that shut
    // the portal down, and the attempts-left count is deliberately not shown
    // here: it reads as an invitation to try again, and trying again is the
    // one thing that must not happen.
    showError(
      (message || "That one-time code was refused.") +
        " Run `vocalize portal` again to get a fresh link.",
      true
    );
    return null;
  }
  return (await response.json()).token;
}

async function boot() {
  $("machine").textContent = window.location.host;
  $("recheck").addEventListener("click", function () {
    clearError();
    refresh();
  });
  document.querySelectorAll('[role="tab"]').forEach(function (tab) {
    tab.addEventListener("click", function () {
      selectTab(tab.id.slice(4));
    });
  });

  var code = takeCode();
  if (code !== null) {
    token = await exchange(code); // exactly one call, and only from a fragment
    if (token === null) return;
    remember(token);
  } else {
    // A reload loses the fragment. The token kept for this tab is the only
    // way back; a stale one answers 403 and ends the page, which is the
    // right answer — re-exchanging is what must never happen.
    token = remembered();
    if (token === null) {
      showError(
        "This page has no one-time code in its address. Run `vocalize portal` again to get a fresh link.",
        true
      );
      return;
    }
  }

  await refresh();
  if (!dead) pingTimer = setInterval(ping, PING_MS);
}

function ping() {
  // The result is handled inside `api`: a closed portal shows the banner and
  // stops this timer. Nothing here retries anything.
  api("GET", "/api/ping");
}

/** What the tab code uses. One fetch helper, one error surface, one cache. */
window.portal = {
  api: api,
  save: save,
  refresh: refresh,
  canWrite: canWrite,
  showError: showError,
  clearError: clearError,
  renderers: renderers,
  selectTab: selectTab,
  state: function () {
    return state;
  }
};

document.addEventListener("DOMContentLoaded", boot);

// ======================================================================
// The five tab panels.
//
// Everything here goes through the plumbing above: `api` for a request,
// `save` for a config write (it carries the fingerprint and refuses to
// re-send after a 409), `showError` for anything that failed. A panel is
// rebuilt from scratch on every refresh and every tab switch, so nothing
// is remembered in the DOM — the one exception is the chain's draft order,
// which is held here and thrown away the moment the saved chain changes.
//
// Nothing on this page writes until a button is pressed. There is no
// save-on-blur and no autosave: a config file is somebody's setup.
// ======================================================================

/** What a provider costs, in words. The chain is the one screen where the
 *  difference between "free and offline" and "billed per character"
 *  decides the order, so it is said there rather than implied. */
var COST = {
  elevenlabs: "Paid. Every character comes out of your ElevenLabs plan.",
  openai: "Paid. Billed to your OpenAI account.",
  google: "Paid. Billed to your Google Cloud project.",
  polly: "Paid. Billed to your AWS account.",
  say: "Free. The voice built into macOS. Works with no network.",
  kokoro: "Free. Runs on this Mac once installed. Works with no network."
};

/** The speech-to-text models, allowlisted by the server too. */
// `config.STT_CLEANUP_BACKENDS`, in its order. "local" is not built yet
// (0.13); the two cloud ones send the transcript off this Mac.
var STT_CLEANUP = ["off", "local", "claude-cli", "anthropic"];

var STT_MODELS = ["base.en", "small.en", "large-v3-turbo-q5_0", "large-v3-turbo-q8_0"];

/** Where a stored API key was found. Eight values, not five: "checking"
 *  means the probe thread is still going and a later poll may answer,
 *  "error" means it finished by raising and never will. */
var KEY_SOURCE = {
  keychain: "In the system keychain",
  environment: "In the environment",
  ".env file": "In a .env file",
  "not found": "No key found",
  checking: "Still checking — press Re-check to look again",
  error: "The check for this key failed",
  "not applicable": "Needs no API key"
};

/** The three sources that mean a key is there. */
var KEY_STORED = ["keychain", "environment", ".env file"];

function hasKey(entry) {
  return Boolean(entry.key) && KEY_STORED.indexOf(entry.key.source) !== -1;
}

/** One line at the top of a provider's card: is there a key. `masked`
 *  arrives from the server already masked and is shown as given; nothing
 *  typed on this page is ever put into this line. With `linkToKeys`, a
 *  missing key gets a way to the Keys tab. */
function keyStatus(name, entry, linkToKeys) {
  var key = entry.key || { source: "error", masked: null };
  var line = el("p", "key-status");
  if (key.source === "not applicable") {
    line.textContent =
      name === "polly"
        ? "Uses AWS credentials, not a key — set them in your shell or ~/.aws."
        : "No key needed";
  } else if (hasKey(entry)) {
    line.textContent = "Key stored" + (key.masked ? " · starts with " + key.masked : "");
  } else if (key.source === "not found") {
    line.className = "key-status none";
    line.textContent = linkToKeys ? "No key stored — " : "No key stored";
    if (linkToKeys) {
      var go = button("add one on the Keys tab", function () {
        selectTab("keys");
      });
      go.className = "link";
      line.appendChild(go);
    }
  } else {
    line.textContent = KEY_SOURCE[key.source] || key.source; // checking, error
  }
  return line;
}

// --- small builders ---------------------------------------------------

var uid = 0;

/** A labelled control. Every input on this page goes through here, so
 *  every input has a real label bound to it by id. */
function field(text, control, hint, extra) {
  uid += 1;
  control.id = "f" + uid;
  // A refusal quoting what was typed should not outlive the correction.
  control.addEventListener("input", clearError);
  var wrap = el("div", "field");
  var tag = el("label", null, text);
  tag.htmlFor = control.id;
  wrap.appendChild(tag);
  wrap.appendChild(control);
  if (extra) wrap.appendChild(extra);
  if (hint) wrap.appendChild(el("p", "hint", hint));
  return wrap;
}

/** The config value as the text a box shows: null and undefined are empty.
 *  A hand-edited file can hold a number where a name belongs, and it
 *  arrives here as that number. */
function asText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function textBox(value) {
  var box = el("input");
  box.type = "text";
  box.value = asText(value);
  box.autocomplete = "off";
  box.spellcheck = false;
  return box;
}

function option(value, text, selected) {
  var item = el("option");
  item.value = value;
  item.textContent = text;
  item.selected = selected;
  return item;
}

/** Put a voice list into a `<select>`, `current` selected. Every voice came
 *  from a third-party API, so each goes in through `.value` and
 *  `.textContent` — properties on a created node, never markup and never
 *  an attribute string. A `current` the list does not know is prepended as
 *  its own option, so the box never shows a voice the file does not hold.
 *  Returns the closing "Type a voice id…" option. */
function offer(pick, voices, current) {
  pick.replaceChildren();
  var known = voices.some(function (voice) {
    return String(voice.id) === current;
  });
  if (!known) {
    pick.appendChild(
      option(current, current === "" ? "vocalize's default" : current + " (not in this list)", true)
    );
  }
  voices.forEach(function (voice) {
    var id = String(voice.id);
    pick.appendChild(option(id, voice.name ? String(voice.name) : id, id === current));
  });
  var typeOne = option("", "Type a voice id…", false);
  pick.appendChild(typeOne);
  return typeOne;
}

/** The voice control: a text box for an id, and a `<select>` that takes
 *  its place once a list arrives. Choosing in the select only sets the
 *  box — nothing is fetched and nothing is saved until Save is pressed.
 *  The last option brings the box back for an id the list does not know. */
function voicePicker(name, label, current) {
  var box = textBox(current);
  var typed = field("Voice id", box, null);
  var pick = el("select");
  var picked = field("Voice", pick, null);
  picked.hidden = true;
  var note = el("p", "hint", "Any voice id this provider knows. Empty means vocalize's default.");
  var typeOne = null;

  pick.addEventListener("change", function () {
    if (typeOne !== null && typeOne.selected) {
      typed.hidden = false;
      box.focus();
      return;
    }
    box.value = pick.value;
    typed.hidden = true;
  });

  function show(voices) {
    // The box's text at this moment, not the value the card was drawn
    // with: a keystroke may have landed while the list was on its way.
    typeOne = offer(pick, voices, box.value);
    picked.hidden = false;
    typed.hidden = true;
    if (document.activeElement === box) pick.focus(); // the field he clicked is now the select
  }
  var ask = function () {
    offerVoices(name, label, show, note);
  };
  box.addEventListener("focus", ask);
  if (voiceLists[name]) ask(); // a rebuilt card is filled from memory

  var wrap = el("div");
  wrap.appendChild(picked);
  wrap.appendChild(typed);
  wrap.appendChild(note);
  return { node: wrap, box: box };
}

function numberBox(value, min, max, step) {
  var box = el("input");
  box.type = "number";
  box.min = String(min);
  if (max !== null) box.max = String(max);
  box.step = step;
  box.value = asText(value);
  return box;
}

function selectBox(values, current) {
  var box = el("select");
  values.forEach(function (value) {
    var option = el("option", null, value);
    option.value = value;
    if (value === current) option.selected = true;
    box.appendChild(option);
  });
  return box;
}

function button(text, onClick) {
  var node = el("button", "action", text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function card(title, badge) {
  var box = el("section", "card");
  var head = el("div", "card-head");
  head.appendChild(el("h3", null, title));
  if (badge) head.appendChild(el("span", "badge", badge));
  box.appendChild(head);
  return box;
}

function number(value) {
  return typeof value === "number" ? value.toLocaleString() : asText(value);
}

/** Collect the changed fields of a settings form.
 *
 *  Only what the user actually touched is sent: the payload the server
 *  returns has every default filled in, so saving the lot would write
 *  vocalize's defaults into the file as if they were choices. An emptied
 *  box sends null, which is how a key is deleted.
 *
 *  Returns null when a number box holds something that is not a number —
 *  `JSON.stringify(NaN)` is `null`, which would silently delete the key.
 */
function changed(fields, status) {
  var settings = {};
  var bad = false;
  fields.forEach(function (entry) {
    var now = entry.box.value.trim();
    if (now === entry.initial) return;
    if (now === "") {
      settings[entry.key] = null;
      return;
    }
    if (entry.number) {
      var value = Number(now);
      if (!isFinite(value)) {
        status.textContent = "The " + entry.key + " has to be a number.";
        bad = true;
        return;
      }
      settings[entry.key] = value;
      return;
    }
    settings[entry.key] = now;
  });
  if (bad) return null;
  if (Object.keys(settings).length === 0) {
    status.textContent = "Nothing changed.";
    return null;
  }
  return settings;
}


/** Write, and if it lands say so on the panel it came from.
 *
 *  `save` refreshes the state on its way out, which rebuilds the panel and
 *  detaches whatever status node the handler was holding — so the good news
 *  is parked in `flash` and painted by the redraw instead of set on a node
 *  nobody can see any more.
 */
async function saveFrom(name, path, body, done) {
  var result = await save(path, body);
  if (result.ok) {
    setFlash(name, done);
    renderPanel(name);
  }
  return result;
}

// --- Chain ------------------------------------------------------------

/* The order being edited, and the saved order it was taken from. When the
 * saved chain changes — someone else wrote the file, or this page just
 * saved — the draft is dropped rather than replayed over the new value. */
var chainDraft = null;
var chainDraftOf = null;

function chainKey(order) {
  return order.join(" ");
}

function chainOrder(saved) {
  if (chainDraftOf !== chainKey(saved)) {
    chainDraft = saved.slice();
    chainDraftOf = chainKey(saved);
  }
  return chainDraft;
}

function providerLabel(data, name) {
  var entry = data.providers[name];
  return entry ? entry.label : name;
}

function chainSourceWords(data) {
  if (data.chain_source === "config file") return "the config file";
  if (data.chain_source === "environment") return "the VOCALIZE_CHAIN environment variable";
  if (data.chain_source === "default") return "vocalize's built-in default";
  if (data.chain_source === "flag") return "a command-line flag";
  return "VOCALIZE_CHAIN, which vocalize could not use";
}

renderers.chain = function (panel, data) {
  var order = chainOrder(data.chain);
  var saved = chainKey(order) === chainKey(data.chain);

  panel.replaceChildren(el("h2", null, "Fallback chain"));
  panel.appendChild(
    el(
      "p",
      "hint",
      "vocalize speaks with the first provider that works and falls down the " +
        "list from there. This order comes from " + chainSourceWords(data) + "."
    )
  );

  var list = el("ol", "chain");
  list.setAttribute("role", "list"); // `list-style: none` drops the role in Safari
  order.forEach(function (name, index) {
    var item = el("li");
    var head = el("div", "row-head");
    head.appendChild(el("span", "row-name", String(index + 1) + ". " + providerLabel(data, name)));

    var controls = el("div", "controls");
    controls.appendChild(chainMove(data, name, index, -1, index === 0));
    controls.appendChild(chainMove(data, name, index, 1, index === order.length - 1));
    var drop = button("Remove", function () {
      order.splice(index, 1);
      renderPanel("chain");
    });
    drop.setAttribute("aria-label", "Remove " + providerLabel(data, name) + " from the chain");
    drop.disabled = order.length === 1;
    controls.appendChild(drop);
    head.appendChild(controls);

    item.appendChild(head);
    item.appendChild(el("p", "hint", COST[name] || ""));
    list.appendChild(item);
  });
  panel.appendChild(list);
  if (order.length === 1) {
    panel.appendChild(el("p", "hint", "A chain needs at least one provider."));
  }

  var spare = Object.keys(data.providers).filter(function (name) {
    return order.indexOf(name) === -1;
  });
  if (spare.length) {
    panel.appendChild(el("h3", null, "Not in the chain"));
    var rest = el("ul", "plain");
    rest.setAttribute("role", "list");
    spare.forEach(function (name) {
      var item = el("li");
      item.appendChild(el("span", "row-name", providerLabel(data, name)));
      item.appendChild(el("span", "hint", COST[name] || ""));
      var add = button("Add", function () {
        order.push(name);
        renderPanel("chain");
      });
      add.setAttribute("aria-label", "Add " + providerLabel(data, name) + " to the end of the chain");
      var controls = el("div", "controls");
      controls.appendChild(add);
      item.appendChild(controls);
      rest.appendChild(item);
    });
    panel.appendChild(rest);
  }

  var actions = el("div", "actions");
  var status = el("p", "status", saved ? "" : "Not saved yet.");
  var keep = button("Save this order", async function () {
    keep.disabled = true;
    status.textContent = "Saving…";
    var result = await saveFrom(
      "chain",
      "/api/chain",
      { order: order.slice() },
      "Saved the new order."
    );
    if (!result.ok) {
      keep.disabled = false;
      status.textContent = "";
    }
  });
  keep.disabled = !canWrite() || saved;
  actions.appendChild(keep);
  if (!canWrite()) {
    actions.appendChild(el("span", "hint", "The config file cannot be written yet."));
  }
  panel.appendChild(actions);
  panel.appendChild(status);

  if (CHAIN_SOURCES.indexOf(data.chain_source) === -1 || data.chain_source === "environment") {
    panel.appendChild(
      el(
        "p",
        "hint",
        "VOCALIZE_CHAIN in the environment beats the config file, so a saved " +
          "order here has no effect until that variable is unset."
      )
    );
  }
};

function chainMove(data, name, index, step, atEnd) {
  var word = step < 0 ? "Up" : "Down";
  var move = button(word, function () {
    var order = chainDraft;
    var other = order[index + step];
    order[index + step] = order[index];
    order[index] = other;
    renderPanel("chain");
  });
  move.setAttribute("aria-label", "Move " + providerLabel(data, name) + " " + word.toLowerCase());
  move.disabled = atEnd;
  return move;
}

// --- Providers --------------------------------------------------------

var previewUrl = null;

/* One voice list per provider per page load, asked for on the first focus
 * of that provider's voice field and remembered here, because the card is
 * rebuilt on every refresh and every tab switch. Only a good list is kept:
 * a 502 or an "unavailable" answer is forgotten as it lands, so the next
 * focus asks again — the usual reason is a key not stored yet, and a
 * reload cannot be the refresh (the one-time code is spent). Neither
 * raises the error banner: the field is free text either way. Storing a
 * key asks for that provider's list again at once. The list itself lives
 * on the server, which holds the keys; this page never fetches one from a
 * provider. */
var voiceLists = {};

function askVoices(name) {
  var asked = api("GET", "/api/voices/" + name);
  voiceLists[name] = asked;
  asked.then(function (result) {
    if ((!result.ok || result.data.source === "unavailable") && voiceLists[name] === asked) {
      delete voiceLists[name];
    }
  });
  return asked;
}

function voiceWords(source, label) {
  if (source === "live") return "The list is live from " + label + ". Pick one, or type any id it knows.";
  if (source === "cached") {
    return "The list is from " + label + ", fetched earlier by this portal. Pick one, or type any id.";
  }
  return "The list is built in. Pick one, or type any id it knows.";
}

function offerVoices(name, label, show, note) {
  (voiceLists[name] || askVoices(name)).then(function (result) {
    if (!result.ok) {
      note.textContent = "Couldn't fetch the list — type a voice id.";
      return;
    }
    if (result.data.source === "unavailable") {
      note.textContent = "No list: " + result.data.reason + " You can still type a voice id.";
      return;
    }
    show(result.data.voices);
    note.textContent = voiceWords(result.data.source, label);
  });
}

renderers.providers = function (panel, data) {
  panel.replaceChildren(el("h2", null, "Provider settings"));
  panel.appendChild(
    el(
      "p",
      "hint",
      "vocalize checks the speed when you save. It does not check a voice or " +
        "model name — a wrong one fails when the provider next speaks, not here."
    )
  );
  Object.keys(data.providers).forEach(function (name) {
    panel.appendChild(providerCard(data, name, data.providers[name]));
  });
};

function providerCard(data, name, entry) {
  var box = card(entry.label, entry.in_chain ? "in the chain" : null);
  var status = el("p", "status");
  var fields = [];

  box.appendChild(keyStatus(name, entry, true));

  // This provider's own trouble, in the CLI's words. `settings: null` is
  // the shape that comes with it — there is nothing to edit until it is
  // fixed in the file.
  if (entry.error) box.appendChild(el("p", "bad", entry.error));

  if (entry.settings === null) {
    box.appendChild(
      el(
        "p",
        "hint",
        "vocalize could not resolve this provider's settings, so the voice, " +
          "model and speed cannot be edited here. Fix it in " + data.config_path + "."
      )
    );
  } else {
    var voice = voicePicker(name, entry.label, entry.settings.voice);
    box.appendChild(voice.node);
    fields.push({ key: "voice", box: voice.box, initial: asText(entry.settings.voice) });

    var model = textBox(entry.settings.model);
    box.appendChild(field("Model", model, "Empty means vocalize's default."));
    fields.push({ key: "model", box: model, initial: asText(entry.settings.model) });

    var speed = numberBox(entry.settings.speed, 0.7, 1.2, "0.05");
    box.appendChild(field("Speed", speed, "Between 0.7 and 1.2. Empty means normal."));
    fields.push({ key: "speed", box: speed, initial: asText(entry.settings.speed), number: true });
  }

  var budget = numberBox(entry.budget, 0, null, "1");
  box.appendChild(
    field(
      "Monthly budget (characters)",
      budget,
      "vocalize refuses this provider once the month's characters pass this. " +
        "Empty means no limit."
    )
  );
  fields.push({ key: "monthly_chars", box: budget, initial: asText(entry.budget), number: true });

  var actions = el("div", "actions");
  var keep = button("Save " + entry.label, async function () {
    var settings = changed(fields, status);
    if (settings === null) return;
    keep.disabled = true;
    status.textContent = "Saving…";
    var result = await saveFrom(
      "providers",
      "/api/provider/" + name,
      { settings: settings },
      "Saved " + entry.label + "."
    );
    keep.disabled = false;
    status.textContent = "";
    return result;
  });
  keep.disabled = !canWrite();
  actions.appendChild(keep);

  var audio = el("audio");
  audio.controls = true;
  audio.hidden = true;
  var listen = button("Preview", async function () {
    // One click, one synthesis: on a paid provider this is billed, and the
    // server holds a lock for 30 s before it refuses a second one, so the
    // button stays disabled for the whole request rather than queueing.
    listen.disabled = true;
    status.textContent = "Speaking the preview sentence…";
    var result = await api("POST", "/api/voices/" + name + "/preview", undefined, true);
    listen.disabled = false;
    if (!result.ok) {
      status.textContent = "";
      if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
      return;
    }
    if (previewUrl !== null) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(result.data);
    audio.src = previewUrl;
    audio.hidden = false;
    status.textContent = "Playing the preview.";
    var playing = audio.play();
    if (playing && playing.catch) {
      playing.catch(function () {
        status.textContent = "Ready — press play.";
      });
    }
  });
  listen.setAttribute("aria-label", "Preview " + entry.label);
  actions.appendChild(listen);
  actions.appendChild(el("span", "hint", "Speaks one short sentence, with what is saved."));

  box.appendChild(actions);
  box.appendChild(audio);
  box.appendChild(status);
  return box;
}

// --- Keys -------------------------------------------------------------

/* The slots this tab lists: `data.keys` (every slot that stores a key —
 * the Anthropic one is a key without being a voice), each reshaped to the
 * card's `{label, key}` form; a server without it falls back to the
 * provider entries, which carry the same `key` object. */
function keySlots(data) {
  var slots = {};
  if (data.keys) {
    Object.keys(data.keys).forEach(function (name) {
      var slot = data.keys[name];
      slots[name] = {
        label: slot.label,
        key: { source: slot.source, masked: slot.masked, validated: slot.validated || null },
        // A slot that is also a voice provider has a voice list to refresh
        // after its key changes; the Anthropic slot has none to ask for.
        voice: !!(data.providers && data.providers[name])
      };
    });
    return slots;
  }
  return data.providers;
}

renderers.keys = function (panel, data) {
  panel.replaceChildren(el("h2", null, "API keys"));
  var slots = keySlots(data);
  var stored = Object.keys(slots).filter(function (name) {
    return hasKey(slots[name]);
  });
  panel.appendChild(
    el(
      "p",
      "summary",
      stored.length
        ? stored.length +
            (stored.length === 1 ? " key stored: " : " keys stored: ") +
            stored
              .map(function (name) {
                return slots[name].label;
              })
              .join(", ")
        : "No keys stored yet"
    )
  );
  panel.appendChild(
    el(
      "p",
      "hint",
      "A key is checked with the provider before it is stored in the system " +
        "keychain, so saving one takes a few seconds. vocalize never shows a " +
        "stored key again. Remove forgets one here; " +
        "vocalize auth logout --provider <name> does the same from a terminal. " +
        "Anthropic is the dictation cleanup and notes backend, not a voice."
    )
  );
  Object.keys(slots).forEach(function (name) {
    panel.appendChild(keyCard(name, slots[name]));
  });
};

function keyCard(name, entry) {
  var box = card(entry.label);
  var source = entry.key.source;
  box.appendChild(keyStatus(name, entry, false));
  if (hasKey(entry)) box.appendChild(el("p", "hint", KEY_SOURCE[source]));
  // The stamp is the keychain item's comment, written when the key was
  // last checked with the provider (macOS only) — a date, never the key.
  if (entry.key.validated) {
    box.appendChild(el("p", "hint stamp", "Last checked with " + entry.label + " on " + entry.key.validated + "."));
  }

  if (source === "not applicable") return box;

  var status = el("p", "status");
  // No <form> anywhere on this page. A form with no action submits to the
  // current URL as a GET, which would put the API key in the address bar,
  // in history, and in the one place the server refuses to read a secret.
  var keyBox = el("input");
  keyBox.type = "password";
  // "new-password", not "off": Safari and Chrome ignore "off" on a password
  // field and offer to save what was typed; "new-password" is the value
  // both honour, and neither fills it from the browser's store.
  keyBox.autocomplete = "new-password";
  keyBox.spellcheck = false;
  box.appendChild(
    field(
      entry.label + " API key",
      keyBox,
      "Paste it here (copy from the provider's console, then Cmd-V). It is " +
        "checked with " + entry.label + " and stored in the keychain, or " +
        "only checked. Clear your clipboard afterwards: copy something else."
    )
  );

  var actions = el("div", "actions");
  // Check a key without storing it — the answer is a verdict, and the
  // server keeps nothing. A good key stays in the field so "Store this
  // key" is the next click; a refused one is cleared like a refused store.
  var test = button("Test without storing", async function () {
    var key = keyBox.value;
    if (!key) {
      status.textContent = "Paste the key first.";
      return;
    }
    test.disabled = true;
    keyBox.disabled = true;
    status.textContent = "Checking the key with " + entry.label + "…";
    var result = await api("POST", "/api/auth/test/" + name, { key: key });
    key = null;
    test.disabled = false;
    keyBox.disabled = false;
    if (!result.ok) {
      // A refusal from this portal (a malformed key, 4xx) clears the field;
      // a check that could not happen (the provider unreachable, 502)
      // keeps it, so the retry is one click.
      if (result.status < 500) keyBox.value = "";
      status.textContent = "";
      if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
      return;
    }
    if (!result.data.valid) keyBox.value = "";
    status.textContent = result.data.message;
  });
  test.disabled = dead;
  actions.appendChild(test);
  var keep = button("Store this key", async function () {
    var key = keyBox.value;
    if (!key) {
      status.textContent = "Paste the key first.";
      return;
    }
    keep.disabled = true;
    keyBox.disabled = true;
    status.textContent = "Checking the key with " + entry.label + "…";
    var result = await api("POST", "/api/auth/login", { provider: name, key: key });
    key = null;
    keep.disabled = false;
    keyBox.disabled = false;
    keyBox.value = ""; // stored or refused, the key does not stay in the DOM
    if (!result.ok) {
      // The server's wording, scrubbed of the key on its side. The key
      // itself is never put into a message here.
      status.textContent = "";
      if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
      return;
    }
    if (entry.voice !== false) askVoices(name); // the list on file was fetched without this key, or with another
    await poll(); // the masked state and the readiness rows both move, and an older poll shows neither
    setFlash("keys", result.data.message);
    renderPanel("keys");
  });
  keep.disabled = dead;
  actions.appendChild(keep);
  // Only a key in the keychain can be forgotten from here; one from the
  // environment or a .env file is the shell's to remove. Two clicks: the
  // first arms the button, so a slip does not cost a key.
  if (source === "keychain") {
    var armed = false;
    var drop = button("Remove stored key", async function () {
      if (!armed) {
        armed = true;
        drop.textContent = "Really remove it?";
        return;
      }
      drop.disabled = true;
      status.textContent = "Removing…";
      var result = await api("POST", "/api/auth/remove/" + name, {});
      drop.disabled = false;
      if (!result.ok) {
        status.textContent = "";
        if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
        return;
      }
      if (entry.voice !== false) askVoices(name); // the list on file was fetched with the key just removed
      await poll();
      setFlash("keys", result.data.message);
      renderPanel("keys");
    });
    drop.disabled = dead;
    actions.appendChild(drop);
  }
  box.appendChild(actions);
  box.appendChild(status);
  return box;
}

// --- Usage ------------------------------------------------------------

renderers.usage = function (panel, data) {
  panel.replaceChildren(el("h2", null, "This month"));
  panel.appendChild(
    el(
      "p",
      "hint",
      "Characters vocalize has spoken through each provider this month, from " +
        "its own ledger. Set a budget on the Providers tab."
    )
  );

  var table = el("table", "grid");
  var head = el("tr");
  ["Provider", "Used", "Budget", "Left"].forEach(function (title, index) {
    var cell = el("th", index ? "num" : null, title);
    cell.scope = "col";
    head.appendChild(cell);
  });
  var thead = el("thead");
  thead.appendChild(head);
  table.appendChild(thead);

  var body = el("tbody");
  Object.keys(data.providers).forEach(function (name) {
    var entry = data.providers[name];
    var row = el("tr", entry.exhausted ? "fail" : null);
    var head_ = el("th", null, entry.label);
    head_.scope = "row";
    row.appendChild(head_);
    row.appendChild(el("td", "num", number(entry.used)));
    row.appendChild(el("td", "num", entry.budget === null ? "no limit" : number(entry.budget)));
    row.appendChild(
      el(
        "td",
        "num",
        entry.budget === null
          ? "—"
          : entry.exhausted
            ? "out of budget"
            : number(Math.max(0, entry.budget - entry.used))
      )
    );
    body.appendChild(row);
  });
  table.appendChild(body);
  panel.appendChild(table);
};

// --- Local ------------------------------------------------------------

/* The install runs in the CLI, on its own thread, and outlives this page.
 * `installProgress` is the last status seen; `installMine` records that
 * this page started the run, because the status dict is never reset to
 * idle — `done: true` sits there until the next install claims the slot. */
var installProgress = null;
var installTimer = null;
var installPolling = false;
var installMine = false;
/* The speech-to-text model picked on the Local tab. Held here so the
 * sidebar's Install button installs what the tab shows, and so the choice
 * survives the tab being rebuilt. */
var sttChoice = "large-v3-turbo-q5_0";

function megabytes(bytes) {
  return (bytes / 1048576).toFixed(1) + " MB";
}

async function watchInstall() {
  if (installPolling) return;
  installPolling = true;
  installTimer = null;
  var result = await api("GET", "/api/local/install/status");
  installPolling = false;
  if (!result.ok) return; // a closed portal is already on screen
  installProgress = result.data;
  paintInstall();
  if (installProgress.running) {
    installTimer = setTimeout(watchInstall, 1000);
  } else if (installMine) {
    installMine = false;
    refresh(); // the sidebar has a row for what just landed
  }
}

function pollInstall() {
  if (installTimer === null && !installPolling) installTimer = setTimeout(watchInstall, 0);
}

function paintInstall() {
  var node = $("install-progress");
  if (!node) return; // the user is on another tab; the download carries on
  node.replaceChildren();
  var progress = installProgress;
  if (progress === null) {
    node.appendChild(el("p", "hint", "Checking…"));
    return;
  }
  if (progress.step === "idle") {
    node.appendChild(el("p", "hint", "Nothing downloading."));
    return;
  }

  var what = progress.target === "stt" ? "Speech to text" : "Kokoro";
  node.appendChild(el("p", "row-name", what + ": " + progress.step));
  if (progress.total > 0) {
    var bar = el("progress");
    bar.max = progress.total;
    bar.value = progress.downloaded;
    bar.setAttribute("aria-label", what + " download");
    node.appendChild(bar);
    node.appendChild(
      el("p", "hint", megabytes(progress.downloaded) + " of " + megabytes(progress.total))
    );
  } else if (progress.downloaded > 0) {
    node.appendChild(el("p", "hint", megabytes(progress.downloaded) + " so far"));
  }
  if (progress.error) node.appendChild(el("p", "bad", progress.error));
  else if (progress.done) node.appendChild(el("p", "status", "Finished."));
  if (progress.note) node.appendChild(el("p", "note-strong", progress.note));
}

renderers.local = function (panel, data) {
  panel.replaceChildren(el("h2", null, "On this Mac"));

  var installs = card("Downloads");
  installs.appendChild(
    el(
      "p",
      "hint",
      "Kokoro is the offline voice; the speech-to-text model is what " +
        "`vocalize listen` transcribes with. Each is a few hundred megabytes " +
        "and takes minutes. The CLI does the downloading — closing this page " +
        "does not stop it, and neither does moving to another tab."
    )
  );

  var running = installProgress !== null && installProgress.running;
  var kokoro = button("Install Kokoro", function () {
    startInstall({ target: "kokoro" }, kokoro);
  });
  kokoro.disabled = running || dead;
  var kokoroRow = el("div", "actions");
  kokoroRow.appendChild(kokoro);
  installs.appendChild(kokoroRow);

  var model = selectBox(STT_MODELS, sttChoice);
  model.addEventListener("change", function () {
    sttChoice = model.value;
  });
  installs.appendChild(
    field(
      "Speech-to-text model to install",
      model,
      "base.en is the smallest and quickest; the large-v3-turbo models are the most accurate, q8_0 the least lossy for 16 GB machines."
    )
  );
  var stt = button("Install speech to text", function () {
    startInstall({ target: "stt", model: sttChoice }, stt);
  });
  stt.disabled = running || dead;
  var sttRow = el("div", "actions");
  sttRow.appendChild(stt);
  installs.appendChild(sttRow);

  var progress = el("div");
  progress.id = "install-progress";
  installs.appendChild(progress);
  panel.appendChild(installs);

  panel.appendChild(sttCard(data));

  paintInstall();
  pollInstall();
};

async function startInstall(body, pressed) {
  pressed.disabled = true;
  var result = await api("POST", "/api/local/install/start", body);
  if (!result.ok) {
    pressed.disabled = false;
    if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
    return;
  }
  installMine = true;
  installProgress = result.data;
  paintInstall();
  pollInstall();
}

// --- the sidebar's buttons --------------------------------------------

function installStt(pressed) {
  selectTab("local");
  startInstall({ target: "stt", model: sttChoice }, pressed);
}

function openKeys() {
  selectTab("keys");
}

/* What the sidebar can do about a readiness row, looked up by the row's
 * NAME — never by reading its action, which is text from a probe and is
 * shown, not interpreted. `state` narrows an entry to the one row state it
 * fits: a provider row is "fail" with no key and "warn" over budget, and
 * only the first is the Keys tab's to fix. A `why` entry is a job this page
 * cannot do — a terminal, a TTY, or a macOS dialog — and says so under the
 * command. A name not here shows its action as text and nothing else. */
var ROW_ACTIONS = {
  kokoro: {
    label: "Install Kokoro",
    run: function (pressed) {
      selectTab("local");
      startInstall({ target: "kokoro" }, pressed);
    }
  },
  "stt model": { label: "Install speech to text", run: installStt },
  recorder: { label: "Install speech to text", run: installStt },
  "input device": { state: "warn", label: "Install speech to text", run: installStt },
  elevenlabs: { state: "fail", label: "Open Keys", run: openKeys },
  openai: { state: "fail", label: "Open Keys", run: openKeys },
  google: { state: "fail", label: "Open Keys", run: openKeys },
  microphone: {
    why: "This page cannot ask macOS for the microphone. Run it in a terminal, or use System Settings."
  },
  polly: {
    why: "AWS credentials are set in a terminal or in ~/.aws. This page does not store them."
  }
};

function rowAction(row) {
  var entry = Object.prototype.hasOwnProperty.call(ROW_ACTIONS, row.name) ? ROW_ACTIONS[row.name] : null;
  var wrap = el("div", "row-do");
  if (entry && entry.run && (!entry.state || entry.state === row.state)) {
    var go = button(entry.label, function () {
      entry.run(go);
    });
    go.disabled = dead || (installProgress !== null && installProgress.running);
    wrap.appendChild(go);
    return wrap;
  }
  wrap.appendChild(el("code", "row-action", row.action));
  if (entry && entry.why) wrap.appendChild(el("p", "hint", entry.why));
  return wrap;
}

function sttCard(data) {
  var box = card("Dictation settings");
  var status = el("p", "status");
  var fields = [];

  var model = selectBox(STT_MODELS, data.stt.model);
  box.appendChild(
    field(
      "Model",
      model,
      "The model `vocalize listen` transcribes with. It has to be installed above."
    )
  );
  fields.push({ key: "model", box: model, initial: asText(data.stt.model) });

  var language = textBox(data.stt.language);
  box.appendChild(
    field(
      "Language",
      language,
      "A whisper.cpp language code such as en, fr or de. An English-only " +
        "model (base.en, small.en) has to stay on en."
    )
  );
  fields.push({ key: "language", box: language, initial: asText(data.stt.language) });

  var cleanup = selectBox(STT_CLEANUP, data.stt.cleanup);
  box.appendChild(
    field(
      "Cleanup",
      cleanup,
      "What tidies a dictated take after transcription. off keeps the words " +
        "as spoken; local runs on this Mac (not built yet — it is skipped " +
        "with a note); claude-cli and anthropic send the transcript off this " +
        "Mac and say so on stderr. The anthropic one needs a key on the Keys tab."
    )
  );
  fields.push({ key: "cleanup", box: cleanup, initial: asText(data.stt.cleanup) });

  var device = textBox(data.stt.input_device);
  box.appendChild(
    field(
      "Input device",
      device,
      "Empty means the system default. Run `vocalize listen --list-devices` " +
        "in a terminal to see the names on this Mac."
    )
  );
  fields.push({ key: "input_device", box: device, initial: asText(data.stt.input_device) });

  var actions = el("div", "actions");
  var keep = button("Save dictation settings", async function () {
    var settings = changed(fields, status);
    if (settings === null) return;
    keep.disabled = true;
    status.textContent = "Saving…";
    await saveFrom("local", "/api/stt", { settings: settings }, "Saved the dictation settings.");
    keep.disabled = false;
    status.textContent = "";
  });
  keep.disabled = !canWrite();
  actions.appendChild(keep);
  box.appendChild(actions);
  box.appendChild(status);
  return box;
}
