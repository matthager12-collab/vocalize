/* The vocalize settings page: the session exchange, one request helper,
 * the state cache with its config fingerprint, and a page drawn entirely
 * from the schema the server sends. Nothing about any one setting is
 * written here: a setting added to the registry appears on this page with
 * no change to this file, drawn by the renderer its `control` names.
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
 *
 * Everything shown is put in as text (`textContent`, `.value`), never as
 * markup: labels, help, values and messages all come from a config file,
 * the registry or the server, and a hand-edited file is somebody's text.
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
 * token-authenticated request, so 30 s keeps an open page alive with room
 * to spare. `/api/state` is NOT polled on a timer: it is fetched on load,
 * after every write and finished action, and when the user asks. */
var PING_MS = 30000;
/* How often a running job is asked about. */
var JOB_POLL_MS = 1000;

var token = null;
var dead = false;
var schema = null; // the /api/schema payload: groups, settings, actions
var state = null; // the last /api/state payload
var fingerprint = null; // the config file's fingerprint from that payload
var pingTimer = null;
var polling = null; // the in-flight refresh, so polls never stack
/* Bumped by every poll and every successful write. A poll applies its
 * answer only if nothing newer started while it was out: an older poll's
 * read predates a write that has since landed, and the page already holds
 * that write's fingerprint from the write's own answer. */
var generation = 0;

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

/** Say something through the polite live region. Every status change the
 *  page makes — saved, refused, started, finished — is announced here. */
function announce(text) {
  $("status").textContent = text;
}

// --- the one place an error is shown ----------------------------------

/** Show one line of trouble. `fatal` marks the page unusable and stops
 *  the polling. */
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

// --- the request helper -----------------------------------------------

/* Status to result kind. 403 is "your session is over" — a wrong token, a
 * refused Host or a refused Origin all land here and all mean this page can
 * do nothing more. 409 is a refusal because something moved: the config
 * file changed on disk, or another action is running. 400/404/413 are
 * refusals of what was sent. 503 is either the lockout (the whole server
 * is going away) or the server being busy, told apart by the message. */
var KINDS = {
  400: "invalid",
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
 * message, body}` with `kind` one of: auth (session over — the page is
 * dead), gone (the portal is not there at all), conflict (409), invalid
 * (the request was refused), missing, busy, error. `body` is the parsed
 * refusal, so a caller can read `errors`, `fingerprint` or `values`.
 */
async function api(method, path, body) {
  if (dead) return { ok: false, kind: "gone", status: 0, message: GONE, body: null };
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
    return { ok: false, kind: "gone", status: 0, message: GONE, body: null };
  }

  if (response.ok) return { ok: true, data: await response.json() };

  var refused = null;
  try {
    refused = await response.json();
  } catch (err) {
    refused = null;
  }
  var message = refused && typeof refused.error === "string" ? refused.error : "";
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
    message: message || "The portal refused that (" + response.status + ").",
    body: refused
  };
}

// --- schema, state, and the fingerprint the writes need ----------------

/** After the session exists: the schema once, then the state. */
async function start() {
  var result = await api("GET", "/api/schema");
  if (!result.ok) {
    if (result.kind !== "gone" && result.kind !== "auth") {
      showError("The list of settings could not be loaded: " + result.message, false);
    }
    return result;
  }
  schema = result.data || {};
  schema.groups = Array.isArray(schema.groups) ? schema.groups : [];
  schema.settings = Array.isArray(schema.settings) ? schema.settings : [];
  schema.actions = Array.isArray(schema.actions) ? schema.actions : [];
  return refresh();
}

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
  // null means the path could not be read at all: nothing can be written
  // until a later poll returns one.
  fingerprint = state.fingerprint === undefined ? null : state.fingerprint;
  render();
  return result;
}

/** True when a write can be attempted at all. */
function canWrite() {
  return !dead && fingerprint !== null;
}

// --- the edits held on the page ----------------------------------------
//
// Nothing writes until Save is pressed: no save-on-blur, no autosave. An
// edit lives in `drafts` (key to the value to save) or `resets` (key to
// true: send null, so the default applies). A redraw — every poll — keeps
// both, so a reload after a 409 discards nothing.

var drafts = {};
var resets = {};
var fieldErrors = {}; // the server's 400, key to message
var localErrors = {}; // what the page itself refuses to send, key to message
var restartNotes = {}; // key to what must restart, after a save
var UNSET = "__vocalize_unset__"; // the option that means "no value: the default"

function has(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function same(a, b) {
  return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
}

function copyOf(value) {
  return Array.isArray(value) ? value.slice() : value;
}

function settingOf(key) {
  var found = null;
  (schema ? schema.settings : []).forEach(function (setting) {
    if (setting.key === key) found = setting;
  });
  return found;
}

/** What the file holds now, or the default when the state says nothing. */
function saved(setting) {
  var values = (state && state.values) || {};
  return has(values, setting.key) ? values[setting.key] : setting.default;
}

/** What the page shows: the edit if there is one, else the saved value. */
function current(setting) {
  if (resets[setting.key]) return copyOf(setting.default);
  if (has(drafts, setting.key)) return copyOf(drafts[setting.key]);
  return copyOf(saved(setting));
}

function setDraft(setting, value) {
  delete resets[setting.key];
  delete fieldErrors[setting.key];
  delete restartNotes[setting.key];
  if (same(value, saved(setting))) delete drafts[setting.key];
  else drafts[setting.key] = copyOf(value);
  updateBar();
}

/** Stage a reset: on save the key is sent as null and the default applies. */
function resetTo(setting) {
  delete drafts[setting.key];
  delete localErrors[setting.key];
  delete fieldErrors[setting.key];
  resets[setting.key] = true;
  updateBar();
}

/** Only the keys that changed: every reset, and every edit that differs
 *  from what the file holds. */
function collect() {
  var changes = {};
  Object.keys(resets).forEach(function (key) {
    changes[key] = null;
  });
  Object.keys(drafts).forEach(function (key) {
    var setting = settingOf(key);
    if (setting && !same(drafts[key], saved(setting))) changes[key] = copyOf(drafts[key]);
  });
  return changes;
}

function updateBar() {
  var count = Object.keys(collect()).length;
  var save = $("save");
  save.textContent = count === 0 ? "Save changes" : "Save " + count + (count === 1 ? " change" : " changes");
  save.disabled = count === 0 || !canWrite();
  $("discard").disabled = count === 0;
}

function discardEdits() {
  drafts = {};
  resets = {};
  localErrors = {};
  fieldErrors = {};
  render();
  announce("Your unsaved edits were undone. The page shows what the file holds.");
}

function labelOf(key) {
  var setting = settingOf(key);
  return setting ? setting.label : key;
}

/* A setting's `restart` field, in words: "app" and "warm" are the two the
 * registry uses; anything newer is shown as it is. */
var RESTART_WORDS = {
  app: "The menu-bar app must be restarted before this change takes effect.",
  warm: "This applies once the dictation model loaded now is let go; until then dictation keeps using it."
};
var APP_RESTART = "app_restart"; // the action that restarts the menu-bar app

function restartWords(restart) {
  return has(RESTART_WORDS, restart) ? RESTART_WORDS[restart] : "Still to restart: " + restart + ".";
}

/** Focus `node` if it can take it; true when it did. */
function focusOn(node) {
  if (!node || !node.focus || node.disabled) return false;
  node.focus();
  return document.activeElement === node;
}

/** What `render()` puts the keyboard on next, when a handler knows better
 *  than "where it was": the item that moved, the next one after a removal. */
var focusNext = null;

/** Put focus back where it was after a redraw. A control that is gone or
 *  now disabled hands it to its card, so it never falls to the page body. */
function restoreFocus(id, home) {
  if (!id) return;
  var again = document.getElementById(id);
  if (focusOn(again)) return;
  var owner = again && again.getAttribute ? again.getAttribute("data-home") : null;
  focusOn(document.getElementById(owner || home || ""));
}

/** The save's own message: what was saved, what must restart (with the
 *  app's restart button when it can run), and the backup the save made.
 *  It takes the focus, because the Save button it came from is now off. */
function showSaved(lines, restart) {
  var box = $("save-note");
  box.className = "banner mild";
  box.hidden = false;
  box.replaceChildren(el("strong", null, "Saved"));
  lines.forEach(function (line) {
    box.appendChild(el("p", null, line));
  });
  var action = actionById(APP_RESTART);
  if (restart.indexOf("app") !== -1 && action && actionState(action).available) {
    var row = el("div", "row");
    var button = el("button", null, action.label || action.id);
    button.type = "button";
    button.id = "save-note-restart";
    button.addEventListener("click", function () {
      runAction(action, {});
    });
    row.appendChild(button);
    box.appendChild(row);
  }
  focusOn(box);
}

/**
 * Write the changed keys with the fingerprint the page was given, and take
 * the next fingerprint from the answer, so consecutive saves need no poll
 * in between.
 *
 * On 409 the file moved under the page: nothing was saved, the user is told
 * and offered a reload, and the edits stay. It never re-sends the same
 * write with the fresh fingerprint by itself — that would overwrite a
 * change nobody saw, which is the whole point of the refusal.
 */
async function saveSettings() {
  var changes = collect();
  var keys = Object.keys(changes);
  if (keys.length === 0) {
    announce("Nothing to save: no setting was changed.");
    return null;
  }
  var stuck = keys.filter(function (key) {
    return has(localErrors, key);
  });
  if (stuck.length) {
    focusNext = domId("set", stuck[0]);
    render();
    announce(
      "Nothing was saved. Fix " +
        stuck
          .map(function (key) {
            return labelOf(key) + " (" + localErrors[key] + ")";
          })
          .join(", ") +
        " first."
    );
    return null;
  }
  if (!canWrite()) {
    var why = dead ? GONE : "The config file could not be read, so nothing can be saved yet.";
    showError(why, dead);
    return null;
  }
  $("save-note").hidden = true;
  announce("Saving…");
  var result = await api("POST", "/api/settings", { changes: changes, fingerprint: fingerprint });
  if (result.ok) {
    clearError();
    $("conflict").hidden = true;
    var data = result.data || {};
    // From the write's own answer, and nothing older may replace it: a
    // poll already in flight read the file before this write landed.
    generation += 1;
    fingerprint = data.fingerprint === undefined ? fingerprint : data.fingerprint;
    if (state) {
      if (data.values) state.values = data.values;
      state.fingerprint = fingerprint;
      if (data.notices) state.notices = data.notices;
    }
    fieldErrors = {};
    restartNotes = {};
    keys.forEach(function (key) {
      delete drafts[key];
      delete resets[key];
      var setting = settingOf(key);
      if (setting && setting.restart) restartNotes[key] = "Saved. " + restartWords(setting.restart);
    });
    var restart = Array.isArray(data.restart) ? data.restart.map(String) : [];
    var lines = ["Saved " + keys.length + (keys.length === 1 ? " setting: " : " settings: ") + keys.map(labelOf).join(", ") + "."];
    restart.forEach(function (need) {
      lines.push(restartWords(need));
    });
    if (typeof data.backup === "string" && data.backup) {
      lines.push("A copy of your old config file, with its comments, was kept at " + data.backup + ".");
      if (data.backup_has_retired === true) {
        lines.push(
          "The copy still holds the old settings that were removed, which can include a service key from an older version, so delete the copy once it is no longer needed."
        );
      }
    }
    render();
    showSaved(lines, restart);
    announce(lines.join(" "));
    await poll(); // the readiness list and the actions may have changed
    return result;
  }
  if (result.kind === "invalid" && result.body && result.body.errors) {
    fieldErrors = {};
    var elsewhere = [];
    Object.keys(result.body.errors).forEach(function (key) {
      fieldErrors[key] = String(result.body.errors[key]);
      if (!settingOf(key)) elsewhere.push(key + ": " + fieldErrors[key]);
    });
    var bad = Object.keys(fieldErrors);
    var placed = bad.filter(function (key) {
      return settingOf(key) !== null;
    });
    if (placed.length) focusNext = domId("set", placed[0]);
    render();
    if (elsewhere.length) showError(elsewhere.join(" "), false);
    announce(
      "Nothing was saved. " + bad.length + (bad.length === 1 ? " setting needs" : " settings need") +
        " a change: " +
        bad
          .map(function (key) {
            return labelOf(key) + ": " + fieldErrors[key];
          })
          .join(" ") +
        " Your edits are still here."
    );
    return result;
  }
  if (result.kind === "conflict") {
    showConflict();
    return result;
  }
  if (result.kind !== "gone" && result.kind !== "auth") showError(result.message, false);
  return result;
}

/** The file changed on disk since the page loaded it. Say so, keep every
 *  edit, and let the user reload the file underneath them. */
function showConflict() {
  var box = $("conflict");
  box.className = "banner";
  box.hidden = false;
  box.replaceChildren(el("strong", null, "The config file changed on disk, so nothing was saved"));
  box.appendChild(
    el(
      "p",
      null,
      "Something else wrote the file after this page loaded it. Your unsaved edits are still on this page. " +
        "Reload the file to see what it holds now; your edits are kept and marked Unsaved, and nothing is written until you press Save again."
    )
  );
  var row = el("div", "row");
  var reload = el("button", "primary", "Reload the file, keep my edits");
  reload.type = "button";
  reload.id = "conflict-reload";
  reload.addEventListener("click", reloadAfterConflict);
  row.appendChild(reload);
  box.appendChild(row);
  announce("Nothing was saved: the config file changed on disk. Reload it to continue; your edits are kept.");
}

async function reloadAfterConflict() {
  $("conflict").hidden = true;
  await poll();
  var count = Object.keys(collect()).length;
  announce(
    "The file was reloaded. " +
      (count ? count + (count === 1 ? " unsaved edit is" : " unsaved edits are") + " kept; check them and press Save." : "No edits were waiting.")
  );
}

// --- the page ---------------------------------------------------------

function render() {
  if (!state) return;
  var active = document.activeElement;
  var focused = focusNext || (active && active.id ? active.id : null);
  var home = active && active.getAttribute ? active.getAttribute("data-home") : null;
  focusNext = null;
  $("config-path").textContent = state.config_path || "—";

  // A config_error means the whole file was discarded and every value on
  // this page is a default, not the user's setting.
  banner(
    "config-alert",
    "The config file did not load — every value below is a default",
    state.config_error ? state.config_error + " Fix the file at " + state.config_path + "." : ""
  );
  renderNotices();
  renderRows();
  if (schema) {
    renderProfile();
    renderGroups();
  }
  updateBar();
  restoreFocus(focused, home);
}

function renderNotices() {
  var notices = (state && Array.isArray(state.notices) && state.notices) || [];
  var box = $("notices");
  if (!notices.length) {
    box.hidden = true;
    return;
  }
  box.className = "banner mild";
  box.replaceChildren(el("strong", null, "Your config file holds settings vocalize no longer uses"));
  var list = el("ul");
  notices.forEach(function (text) {
    list.appendChild(el("li", null, String(text)));
  });
  box.appendChild(list);
  box.appendChild(el("p", null, "The next save removes these retired settings from the file. Nothing else in it is touched."));
  box.hidden = false;
}

/** One readiness row: its name, its state in words, its detail, and its
 *  suggested command as a `<code>` node's text. */
function rowItem(row) {
  var known = row.state === "ok" || row.state === "warn" || row.state === "fail";
  var item = el("li", known ? row.state : "warn");
  var head = el("div", "row-head");
  head.appendChild(el("span", "row-name", row.name));
  head.appendChild(el("span", "row-state", row.state));
  item.appendChild(head);
  if (row.detail) item.appendChild(el("p", "row-detail", row.detail));
  if (row.action) item.appendChild(el("code", "row-action", row.action));
  return item;
}

function renderRows() {
  var list = $("rows");
  var rows = state.rows || [];
  list.replaceChildren();
  rows.forEach(function (row) {
    list.appendChild(rowItem(row));
  });
  var note = "";
  if (!rows.length) note = "Nothing to check yet.";
  else if (fingerprint === null) note = "Saving is off: the config file could not be read.";
  else if (fingerprint === "absent") note = "No config file yet — the first save creates it.";
  $("rows-note").textContent = note;
  $("rows-note").hidden = note === "";
}

function domId(prefix, key) {
  return prefix + "-" + String(key).replace(/[^A-Za-z0-9_-]/g, "-");
}

/** The words for a value, as the page shows it beside a control. */
function shown(value) {
  if (value === null || value === undefined) return "not set";
  if (value === true) return "on";
  if (value === false) return "off";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "an empty list";
  if (value === "") return "empty";
  return String(value);
}

function sortedGroups() {
  return schema.groups.slice().sort(function (a, b) {
    return (a.order || 0) - (b.order || 0);
  });
}

var OTHER = { id: "__other__", label: "Other", help: "Settings and actions this page could not place in a group." };

function renderGroups() {
  var holder = $("groups");
  var toc = $("toc");
  holder.replaceChildren();
  var links = el("ul");
  var groups = sortedGroups();
  var known = {};
  groups.forEach(function (group) {
    known[group.id] = true;
  });
  var straySettings = schema.settings.filter(function (s) {
    return !has(known, s.group);
  });
  var strayActions = schema.actions.filter(function (a) {
    return !has(known, a.group) && !isProfileAction(a);
  });
  var all = groups.slice();
  if (straySettings.length || strayActions.length) all.push(OTHER);
  all.forEach(function (group) {
    var settings =
      group === OTHER
        ? straySettings
        : schema.settings.filter(function (s) {
            return s.group === group.id;
          });
    var actions =
      group === OTHER
        ? strayActions
        : schema.actions.filter(function (a) {
            return a.group === group.id && !isProfileAction(a);
          });
    holder.appendChild(groupSection(group, settings, actions));
    var item = el("li");
    var link = el("a", null, group.label);
    link.href = "#" + domId("g", group.id);
    item.appendChild(link);
    links.appendChild(item);
  });
  toc.replaceChildren(links);
}

function groupSection(group, settings, actions) {
  var section = el("section", "group");
  section.id = domId("g", group.id);
  section.setAttribute("data-group", group.id);
  var heading = el("h2", null, group.label);
  heading.id = domId("gh", group.id);
  section.setAttribute("aria-labelledby", heading.id);
  section.appendChild(heading);
  if (group.help) section.appendChild(el("p", "hint", group.help));
  settings.forEach(function (setting) {
    section.appendChild(settingCard(setting));
  });
  if (actions.length) {
    section.appendChild(el("h3", null, group.label + ": actions"));
    actions.forEach(function (action) {
      section.appendChild(actionCard(action));
    });
  }
  return section;
}

// --- one setting ------------------------------------------------------

/** Whether a setting's `applies_when` holds, and the words when it does not. */
function applies(setting) {
  var when = setting.applies_when;
  if (!Array.isArray(when) || when.length !== 2) return { ok: true };
  var other = settingOf(when[0]);
  var now = other ? current(other) : undefined;
  if (same(now, when[1])) return { ok: true };
  var name = "“" + (other ? other.label : when[0]) + "”";
  var words =
    when[1] === true ? name + " is on" : when[1] === false ? name + " is off" : name + " is set to " + shown(when[1]);
  return { ok: false, note: "Applies only when " + words + ". Change that setting to use this one." };
}

/** Register an interactive part of a control: disabled with the card when
 *  the setting does not apply, and described by its help and error. */
function part(ctx, node) {
  node.disabled = ctx.off || node.disabled === true;
  if (ctx.home) node.setAttribute("data-home", ctx.home);
  ctx.parts.push(node);
  return node;
}

/** Why a source is empty and what to do about it, from the state. */
function sourceNote(source) {
  var notes = (state && state.source_notes) || {};
  return source && typeof notes[source] === "string" ? notes[source] : "";
}

function labelFor(ctx, node, text) {
  var tag = el("label", null, text === undefined ? ctx.setting.label : text);
  tag.htmlFor = node.id;
  return tag;
}

/** The environment variable that overrides `key` in the server's own
 *  environment, by name only (the server never sends its value). */
function overrideOf(key) {
  var map = state && state.overrides;
  if (!map || typeof map !== "object" || !has(map, key)) return "";
  var name = map[key];
  return typeof name === "string" && /^VOCALIZE_[A-Z_]{1,40}$/.test(name) ? name : "";
}

function settingCard(setting) {
  var key = setting.key;
  var card = el("div", "setting");
  card.id = domId("card", key);
  card.setAttribute("data-setting", key);
  card.setAttribute("tabindex", "-1"); // where focus goes when its control cannot hold it
  var rule = applies(setting);
  var ctx = { setting: setting, value: current(setting), off: !rule.ok, parts: [], id: domId("set", key), home: card.id };
  var draw = has(RENDERERS, setting.control) ? RENDERERS[setting.control] : renderUnknown;
  var control = draw(ctx, card);
  control.setAttribute("data-control", key);
  control.setAttribute("data-home", card.id);

  var described = [];
  var why = sourceNote(setting.choices_from);
  if (why) {
    var empty = el("p", "note", why);
    empty.id = domId("src", key);
    card.appendChild(empty);
    described.push(empty.id);
  }
  if (setting.help) {
    var help = el("p", "hint", setting.help);
    help.id = domId("help", key);
    card.appendChild(help);
    described.push(help.id);
  }
  var variable = overrideOf(key);
  if (variable) {
    var env = el("p", "note", variable + " overrides this value for commands run from a shell where it is set. This page edits the config file.");
    env.id = domId("env", key);
    card.appendChild(env);
    described.push(env.id);
  }
  if (!rule.ok) {
    card.className += " off";
    var off = el("p", "note", rule.note);
    off.id = domId("off", key);
    card.appendChild(off);
    described.push(off.id);
  }
  var error = has(localErrors, key) ? localErrors[key] : has(fieldErrors, key) ? fieldErrors[key] : "";
  if (error) {
    card.className += " has-error";
    var bad = el("p", "bad", error);
    bad.id = domId("err", key);
    card.appendChild(bad);
    described.push(bad.id);
  }
  ctx.parts.forEach(function (node) {
    if (described.length) node.setAttribute("aria-describedby", described.join(" "));
    if (error) node.setAttribute("aria-invalid", "true");
  });
  if (has(restartNotes, key)) card.appendChild(el("p", "restart", restartNotes[key]));

  var badges = el("p", "badges");
  var isDefault = same(ctx.value, setting.default);
  badges.appendChild(
    el("span", "badge", isDefault ? "Default value" : "Changed from the default (" + shown(setting.default) + ")")
  );
  if (resets[key]) {
    card.className += " unsaved";
    badges.appendChild(el("span", "badge unsaved", "Unsaved: will return to the default"));
  } else if (has(drafts, key)) {
    card.className += " unsaved";
    badges.appendChild(el("span", "badge unsaved", "Unsaved edit (saved: " + shown(saved(setting)) + ")"));
  }
  if (setting.restart) badges.appendChild(el("span", "badge", restartWords(setting.restart)));
  card.appendChild(badges);

  var row = el("div", "row");
  var reset = el("button", null, "Reset to default");
  reset.type = "button";
  reset.id = domId("reset", key);
  reset.setAttribute("data-reset", key);
  reset.setAttribute("aria-label", "Reset " + setting.label + " to its default");
  reset.setAttribute("data-home", card.id);
  reset.disabled = resets[key] === true;
  reset.addEventListener("click", function () {
    resetTo(setting);
    render();
    announce(setting.label + " will return to its default, " + shown(setting.default) + ", when you save.");
  });
  row.appendChild(reset);
  card.appendChild(row);
  return card;
}

/** Change from a control, then redraw: other settings may depend on it. */
function commit(setting, value) {
  setDraft(setting, value);
  render();
}

/** Typed text for a key whose default is null: empty means "not set". */
function textValue(setting, text) {
  if (text === "" && setting.default === null) return null;
  return text;
}

/** Store typed text: null (empty, for a key with no default) is a reset. */
function typed(setting, text, redraw) {
  var value = textValue(setting, text);
  if (value === null) resetTo(setting);
  else setDraft(setting, value);
  if (redraw) render();
}

function textInput(ctx, value) {
  var box = el("input");
  box.type = "text";
  box.id = ctx.id;
  box.value = value === null || value === undefined ? "" : String(value);
  box.autocomplete = "off";
  box.spellcheck = false;
  if (ctx.setting.max_length) box.maxLength = ctx.setting.max_length;
  return part(ctx, box);
}

function option(value, text, selected) {
  var item = el("option");
  item.value = value;
  item.textContent = text;
  item.selected = selected;
  return item;
}

/** The named source from the state as `{value, label}` entries, or null. */
function sourceEntries(source) {
  var sources = (state && state.sources) || {};
  if (!source || !Array.isArray(sources[source])) return null;
  return sources[source].map(function (entry) {
    if (entry !== null && typeof entry === "object") {
      return { value: entry.value, label: entry.label === undefined ? String(entry.value) : String(entry.label) };
    }
    return { value: entry, label: String(entry) };
  });
}

/** The options a setting or a param offers, each `{value, label}`. With
 *  `choices`, those are the allowed values in their order, and the source
 *  only lends labels to the values it knows; an empty choice it does not
 *  label is called `emptyLabel` (the setting's help). With no `choices`,
 *  the source's own list. */
function optionsFrom(choices, source, emptyLabel) {
  var listed = sourceEntries(source);
  if (!Array.isArray(choices)) return listed || [];
  var labels = {};
  (listed || []).forEach(function (entry) {
    labels["v:" + String(entry.value)] = entry.label;
  });
  return choices.map(function (choice) {
    var key = "v:" + String(choice);
    var label = has(labels, key) && labels[key] !== "" ? labels[key] : choice === "" ? emptyLabel || "None" : String(choice);
    return { value: choice, label: label };
  });
}

/** A `<select>` over `options`, `value` selected. A value the list does not
 *  hold is offered first as itself, so nothing changes under the user; a
 *  null value is the "not set" option when `unsetLabel` names one. */
function dropDown(ctx, options, value, unsetLabel, onPick) {
  var list = options.slice();
  var known = list.some(function (entry) {
    return same(entry.value, value);
  });
  if (unsetLabel) list.unshift({ value: UNSET, label: unsetLabel });
  if (!known && !(value === null && unsetLabel)) {
    list.unshift({ value: value, label: shown(value) + " (not in the list)" });
  }
  var pick = el("select");
  pick.id = ctx.id;
  list.forEach(function (entry) {
    var mine = entry.value === UNSET ? value === null : same(entry.value, value);
    pick.appendChild(option(entry.value === null ? UNSET : String(entry.value), entry.label, mine));
  });
  pick.value = value === null ? UNSET : String(value);
  pick.addEventListener("change", function () {
    if (pick.value === UNSET) return onPick(null, true);
    var chosen = null;
    list.forEach(function (entry) {
      if (entry.value !== UNSET && String(entry.value) === pick.value) chosen = entry;
    });
    return onPick(chosen ? chosen.value : pick.value, false);
  });
  return part(ctx, pick);
}

function pickInto(setting) {
  return function (value, unset) {
    if (unset) {
      resetTo(setting);
      render();
    } else commit(setting, value);
  };
}

// --- the renderers, one per control kind --------------------------------

/* chord: vocalize's spelling of a key combination. Modifiers in the order
 * `config.CHORD_MODIFIERS` gives them, then one key: a-z, 0-9 or f1-f12. */
var CHORD_ORDER = [
  ["ctrlKey", "ctrl"],
  ["altKey", "alt"],
  ["metaKey", "cmd"],
  ["shiftKey", "shift"]
];

function chordKey(event) {
  var code = String(event.code || "");
  var match = /^Key([A-Z])$/.exec(code) || /^Digit([0-9])$/.exec(code);
  if (match) return match[1].toLowerCase();
  match = /^F([1-9]|1[0-2])$/.exec(code);
  if (match) return "f" + match[1];
  var key = String(event.key || "").toLowerCase();
  return /^[a-z0-9]$/.test(key) || /^f([1-9]|1[0-2])$/.test(key) ? key : null;
}

/** The chord a key press spells, or null when it is not one (no ctrl, alt
 *  or cmd held, or no key vocalize knows). Plain typing is left alone, so
 *  the same field takes a chord typed by hand. */
function chordOf(event) {
  if (!event.ctrlKey && !event.altKey && !event.metaKey) return null;
  var key = chordKey(event);
  if (key === null) return null;
  var parts = [];
  CHORD_ORDER.forEach(function (pair) {
    if (event[pair[0]]) parts.push(pair[1]);
  });
  parts.push(key);
  return parts.join("+");
}

var RENDERERS = {
  toggle: function (ctx, card) {
    var box = el("input");
    box.type = "checkbox";
    box.id = ctx.id;
    box.checked = ctx.value === true;
    part(ctx, box);
    box.addEventListener("change", function () {
      commit(ctx.setting, box.checked === true);
    });
    var line = el("div", "check");
    line.appendChild(box);
    line.appendChild(labelFor(ctx, box));
    card.appendChild(line);
    return box;
  },

  select: function (ctx, card) {
    var options = optionsFrom(ctx.setting.choices, ctx.setting.choices_from, ctx.setting.help);
    var unset = ctx.value === null || ctx.setting.default === null ? "Not set (the default)" : null;
    var pick = dropDown(ctx, options, ctx.value, unset, pickInto(ctx.setting));
    card.appendChild(labelFor(ctx, pick));
    card.appendChild(pick);
    return pick;
  },

  number: function (ctx, card) {
    var setting = ctx.setting;
    var box = el("input");
    box.type = "number";
    box.id = ctx.id;
    box.inputMode = setting.type === "int" ? "numeric" : "decimal";
    if (setting.minimum !== null && setting.minimum !== undefined) box.min = String(setting.minimum);
    if (setting.maximum !== null && setting.maximum !== undefined) box.max = String(setting.maximum);
    box.step = setting.step !== null && setting.step !== undefined ? String(setting.step) : setting.type === "int" ? "1" : "any";
    box.value = ctx.value === null || ctx.value === undefined ? "" : String(ctx.value);
    part(ctx, box);
    function take() {
      var text = String(box.value).trim();
      delete localErrors[setting.key];
      if (text === "") {
        resetTo(setting); // empty: not set, so the default applies
        return;
      }
      var number = Number(text);
      if (!isFinite(number)) localErrors[setting.key] = "Enter a number.";
      else if (setting.type === "int" && !Number.isInteger(number)) localErrors[setting.key] = "Enter a whole number.";
      else if (setting.minimum !== null && setting.minimum !== undefined && number < setting.minimum) {
        localErrors[setting.key] = "Enter " + setting.minimum + " or more.";
      } else if (setting.maximum !== null && setting.maximum !== undefined && number > setting.maximum) {
        localErrors[setting.key] = "Enter " + setting.maximum + " or less.";
      }
      setDraft(setting, number);
    }
    box.addEventListener("input", take);
    box.addEventListener("change", function () {
      take();
      render();
      if (has(localErrors, setting.key)) announce(setting.label + ": " + localErrors[setting.key]);
    });
    card.appendChild(labelFor(ctx, box));
    card.appendChild(box);
    card.appendChild(el("p", "hint", "Leave it empty to use the default."));
    return box;
  },

  text: function (ctx, card) {
    var box = textInput(ctx, ctx.value);
    box.addEventListener("input", function () {
      typed(ctx.setting, box.value, false);
    });
    box.addEventListener("change", function () {
      typed(ctx.setting, box.value, true);
    });
    card.appendChild(labelFor(ctx, box));
    card.appendChild(box);
    return box;
  },

  chord: function (ctx, card) {
    var box = textInput(ctx, ctx.value);
    box.placeholder = "ctrl+alt+cmd+d";
    box.addEventListener("keydown", function (event) {
      var chord = chordOf(event);
      if (chord === null) return; // plain typing, Tab and the rest: left alone
      event.preventDefault();
      box.value = chord;
      setDraft(ctx.setting, chord);
      announce(ctx.setting.label + " set to " + chord + ". Press Save to keep it.");
    });
    box.addEventListener("input", function () {
      setDraft(ctx.setting, String(box.value).trim().toLowerCase());
    });
    box.addEventListener("change", function () {
      commit(ctx.setting, String(box.value).trim().toLowerCase());
    });
    card.appendChild(labelFor(ctx, box));
    card.appendChild(box);
    card.appendChild(
      el(
        "p",
        "hint",
        "Click the field and press the combination, or type it, for example ctrl+alt+cmd+d. Empty turns the shortcut off."
      )
    );
    var row = el("div", "row");
    var clear = el("button", null, "Turn this shortcut off");
    clear.type = "button";
    clear.id = domId("off-btn", ctx.setting.key);
    part(ctx, clear);
    clear.addEventListener("click", function () {
      commit(ctx.setting, "");
      announce(ctx.setting.label + " will be turned off when you save.");
    });
    row.appendChild(clear);
    card.appendChild(row);
    return box;
  },

  device: function (ctx, card) {
    var source = ctx.setting.choices_from || "input_devices";
    var devices = optionsFrom(null, source);
    var options = [{ value: "", label: "The system default microphone" }].concat(devices);
    var value = ctx.value === null ? "" : ctx.value;
    var pick = dropDown(ctx, options, value, null, function (chosen) {
      commit(ctx.setting, chosen === null ? "" : String(chosen));
    });
    card.appendChild(labelFor(ctx, pick));
    card.appendChild(pick);
    if (!devices.length && !sourceNote(source)) {
      card.appendChild(el("p", "note", "No microphones are listed right now, so only the system default is offered."));
    }
    return pick;
  },

  list: function (ctx, card) {
    var setting = ctx.setting;
    var items = Array.isArray(ctx.value) ? ctx.value.map(String) : [];
    var group = el("fieldset");
    group.id = ctx.id;
    group.appendChild(el("legend", null, setting.label));
    var list = el("ol", "items");
    list.setAttribute("role", "list");
    items.forEach(function (item, index) {
      var row = el("li");
      var box = el("input");
      box.type = "text";
      box.id = domId("item", setting.key) + "-" + index;
      box.value = item;
      box.autocomplete = "off";
      if (setting.max_length) box.maxLength = setting.max_length;
      part(ctx, box);
      function edited() {
        var next = items.slice();
        next[index] = box.value;
        return next;
      }
      box.addEventListener("input", function () {
        setDraft(setting, edited());
      });
      box.addEventListener("change", function () {
        commit(setting, edited());
      });
      row.appendChild(labelFor(ctx, box, "Item " + (index + 1)));
      row.appendChild(box);
      var remove = el("button", null, "Remove");
      remove.type = "button";
      remove.id = domId("remove", setting.key) + "-" + index;
      remove.setAttribute("aria-label", "Remove “" + item + "” from " + setting.label);
      part(ctx, remove);
      remove.addEventListener("click", function () {
        var next = items.slice();
        next.splice(index, 1);
        // The item that took its place, else the one before, else the adder.
        focusNext = next.length
          ? domId("remove", setting.key) + "-" + Math.min(index, next.length - 1)
          : domId("add", setting.key);
        commit(setting, next);
        announce("Removed “" + item + "” from " + setting.label + ".");
      });
      row.appendChild(remove);
      list.appendChild(row);
    });
    group.appendChild(list);
    if (!items.length) group.appendChild(el("p", "hint", "The list is empty."));

    var full = setting.max_items !== null && setting.max_items !== undefined && items.length >= setting.max_items;
    var add = el("input");
    add.type = "text";
    add.id = domId("add", setting.key);
    add.autocomplete = "off";
    if (setting.max_length) add.maxLength = setting.max_length;
    var addButton = el("button", null, "Add");
    addButton.type = "button";
    addButton.id = domId("add-btn", setting.key);
    addButton.setAttribute("aria-label", "Add to " + setting.label);
    add.disabled = full;
    addButton.disabled = full;
    part(ctx, add);
    part(ctx, addButton);
    function addOne() {
      var text = String(add.value).trim();
      if (text === "") {
        announce("Type something to add first.");
        return;
      }
      commit(setting, items.concat([text]));
      announce("Added “" + text + "” to " + setting.label + ". Press Save to keep it.");
    }
    addButton.addEventListener("click", addOne);
    add.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        addOne();
      }
    });
    group.appendChild(labelFor(ctx, add, "New item for " + setting.label));
    var adder = el("div", "row");
    adder.appendChild(add);
    adder.appendChild(addButton);
    group.appendChild(adder);
    if (full) group.appendChild(el("p", "note", "The list is full: it holds at most " + setting.max_items + " items."));
    card.appendChild(group);
    return group;
  },

  voice: function (ctx, card) {
    var setting = ctx.setting;
    var options = optionsFrom(setting.choices, setting.choices_from, setting.help);
    var unset = ctx.value === null || setting.default === null ? "The engine's own default voice" : null;
    var pick = dropDown(ctx, options, ctx.value, unset, pickInto(setting));
    card.appendChild(labelFor(ctx, pick));
    card.appendChild(pick);
    if (!options.length) card.appendChild(el("p", "note", "No voices are listed for this engine right now: is it installed?"));

    var parts = String(setting.key).split(".");
    var engine = parts.length === 3 ? parts[1] : "";
    var preview = actionById("preview_voice");
    var status = preview ? actionState(preview) : { available: false, reason: "this vocalize offers no preview" };
    var row = el("div", "row");
    var button = el("button", null, "Preview");
    button.type = "button";
    button.id = domId("preview", setting.key);
    button.setAttribute("aria-label", "Preview " + setting.label);
    button.disabled = !status.available;
    part(ctx, button);
    button.addEventListener("click", function () {
      runAction(preview, previewParams(preview, engine, current(setting)));
    });
    row.appendChild(button);
    card.appendChild(row);
    if (!status.available) {
      card.appendChild(el("p", "note", "Preview is not available: " + (status.reason || "no reason given") + "."));
    } else {
      var job = jobFor(preview.id);
      if (job) card.appendChild(el("p", "note", jobWords(job)));
    }
    return pick;
  },

  folder: function (ctx, card) {
    var box = textInput(ctx, ctx.value);
    box.addEventListener("input", function () {
      typed(ctx.setting, box.value, false);
    });
    box.addEventListener("change", function () {
      typed(ctx.setting, box.value, true);
    });
    card.appendChild(labelFor(ctx, box));
    card.appendChild(box);
    card.appendChild(el("p", "hint", "A folder path on this Mac; ~ stands for your home folder."));
    return box;
  },

  chain: function (ctx, card) {
    var setting = ctx.setting;
    var order = Array.isArray(ctx.value) ? ctx.value.map(String) : [];
    var engines = optionsFrom(setting.choices, setting.choices_from || "tts_engines", setting.help);
    function nameOf(value) {
      var label = value;
      engines.forEach(function (entry) {
        if (String(entry.value) === value) label = entry.label;
      });
      return label;
    }
    var group = el("fieldset");
    group.id = ctx.id;
    group.appendChild(el("legend", null, setting.label));
    var list = el("ol", "items");
    list.setAttribute("role", "list");
    order.forEach(function (name, index) {
      var row = el("li");
      row.appendChild(el("span", "item-name", index + 1 + ". " + nameOf(name)));
      [
        ["up", "Move up", index === 0, -1],
        ["down", "Move down", index === order.length - 1, 1]
      ].forEach(function (spec) {
        var move = el("button", null, spec[1]);
        move.type = "button";
        move.id = domId(spec[0], setting.key) + "-" + index;
        move.setAttribute("aria-label", spec[1] + ": " + nameOf(name));
        move.disabled = spec[2];
        part(ctx, move);
        move.addEventListener("click", function () {
          var next = order.slice();
          var to = index + spec[3];
          next.splice(index, 1);
          next.splice(to, 0, name);
          // Follow the engine that moved; at an end, its other button.
          var edge = spec[0] === "up" ? to === 0 : to === next.length - 1;
          focusNext = domId(edge ? (spec[0] === "up" ? "down" : "up") : spec[0], setting.key) + "-" + to;
          commit(setting, next);
          announce(nameOf(name) + " is now number " + (index + spec[3] + 1) + " of " + next.length + ".");
        });
        row.appendChild(move);
      });
      var remove = el("button", null, "Remove");
      remove.type = "button";
      remove.id = domId("remove", setting.key) + "-" + index;
      remove.setAttribute("aria-label", "Remove " + nameOf(name) + " from " + setting.label);
      remove.disabled = order.length <= 1;
      part(ctx, remove);
      remove.addEventListener("click", function () {
        var next = order.slice();
        next.splice(index, 1);
        focusNext = domId("remove", setting.key) + "-" + Math.min(index, next.length - 1);
        commit(setting, next);
        announce("Removed " + nameOf(name) + ".");
      });
      row.appendChild(remove);
      list.appendChild(row);
    });
    group.appendChild(list);
    if (order.length === 1) group.appendChild(el("p", "hint", "At least one engine must stay in the list."));

    var left = engines.filter(function (entry) {
      return order.indexOf(String(entry.value)) === -1;
    });
    var full = setting.max_items !== null && setting.max_items !== undefined && order.length >= setting.max_items;
    if (left.length && !full) {
      var pickCtx = { setting: setting, off: ctx.off, parts: ctx.parts, id: domId("add", setting.key), home: ctx.home };
      var chosen = { value: left[0].value };
      var pick = dropDown(pickCtx, left, left[0].value, null, function (value) {
        chosen.value = value;
      });
      group.appendChild(labelFor(pickCtx, pick, "Add an engine to the end"));
      var adder = el("div", "row");
      adder.appendChild(pick);
      var add = el("button", null, "Add");
      add.type = "button";
      add.id = domId("add-btn", setting.key);
      add.setAttribute("aria-label", "Add the chosen engine to " + setting.label);
      part(ctx, add);
      add.addEventListener("click", function () {
        var value = String(chosen.value);
        commit(setting, order.concat([value]));
        announce("Added " + nameOf(value) + " at the end.");
      });
      adder.appendChild(add);
      group.appendChild(adder);
    } else {
      group.appendChild(el("p", "hint", "Every engine is already in the list."));
    }
    card.appendChild(group);
    return group;
  }
};

/** A control kind this page does not know: a plain text field over the
 *  value, read back by the setting's type, with a note saying so. Never
 *  dropped — the setting still has a control. */
function renderUnknown(ctx, card) {
  var setting = ctx.setting;
  var value = ctx.value;
  var text = Array.isArray(value) ? value.join(", ") : value === null || value === undefined ? "" : String(value);
  var box = textInput(ctx, text);
  function read() {
    var raw = String(box.value).trim();
    delete localErrors[setting.key];
    if (raw === "") return setting.type === "str" ? "" : null;
    if (setting.type === "bool") {
      if (raw === "true" || raw === "false") return raw === "true";
      localErrors[setting.key] = "Type true or false.";
      return raw;
    }
    if (setting.type === "int" || setting.type === "float") {
      var number = Number(raw);
      if (!isFinite(number)) localErrors[setting.key] = "Enter a number.";
      return number;
    }
    if (setting.type === "list") {
      return raw
        .split(",")
        .map(function (item) {
          return item.trim();
        })
        .filter(Boolean);
    }
    return box.value;
  }
  function store(redraw) {
    var next = read();
    if (next === null) resetTo(setting);
    else setDraft(setting, next);
    if (redraw) render();
  }
  box.addEventListener("input", function () {
    store(false);
  });
  box.addEventListener("change", function () {
    store(true);
    if (has(localErrors, setting.key)) announce(setting.label + ": " + localErrors[setting.key]);
  });
  card.appendChild(labelFor(ctx, box));
  card.appendChild(box);
  card.appendChild(
    el(
      "p",
      "note",
      "This page does not know the control “" + setting.control + "”, so it is shown as plain text" +
        (setting.type === "list" ? " (items separated by commas)." : ".")
    )
  );
  return box;
}

// --- actions ----------------------------------------------------------

var jobs = {}; // job id to the last {id, action, state, progress, message}
var actionNotes = {}; // action id to {text, bad}
var paramDrafts = {}; // action id to {param name: value}
var jobTimer = null;
var ACTION_KINDS = { job: true, instant: true };

function actionById(id) {
  var found = null;
  (schema ? schema.actions : []).forEach(function (action) {
    if (action.id === id) found = action;
  });
  return found;
}

function actionState(action) {
  var all = (state && state.actions) || {};
  var mine = has(all, action.id) ? all[action.id] : null;
  if (!mine) return { available: true, reason: "", status: "" };
  return {
    available: mine.available !== false,
    reason: mine.reason ? String(mine.reason) : "",
    status: mine.status ? String(mine.status) : ""
  };
}

function isProfileAction(action) {
  return action.id === "apply_profile";
}

function jobFor(actionId) {
  var found = null;
  Object.keys(jobs).forEach(function (id) {
    if (jobs[id].action === actionId && (found === null || jobs[id].state === "running")) found = jobs[id];
  });
  return found;
}

function anyRunning() {
  return Object.keys(jobs).some(function (id) {
    return jobs[id].state === "running";
  });
}

function jobWords(job) {
  var action = actionById(job.action);
  var label = action ? action.label || action.id : job.action;
  var message = job.message ? ": " + job.message : "";
  if (job.state === "done") return label + " finished" + message;
  if (job.state === "failed") return label + " failed" + message;
  var percent = typeof job.progress === "number" ? " (" + Math.round(job.progress * 100) + "% done)" : "";
  return label + " is running" + percent + message;
}

/** The params a voice preview needs: the engine named by the setting's key
 *  and the voice shown in its drop-down. Matched by param name. */
function previewParams(action, engine, voice) {
  var params = {};
  ((action && action.params) || []).forEach(function (param) {
    if (param.name === "engine" || param.name === "provider" || param.choices_from === "tts_engines") {
      params[param.name] = engine;
    } else if (param.name === "voice" && voice !== null && voice !== undefined) {
      params[param.name] = String(voice);
    }
  });
  return params;
}

function paramOptions(param) {
  var sources = (state && state.sources) || {};
  if (Array.isArray(param.choices) || (param.choices_from && Array.isArray(sources[param.choices_from]))) {
    return optionsFrom(param.choices, param.choices_from, param.label || param.name);
  }
  if (param.choices_from === "profiles" || param.name === "profile") {
    var names = (state && state.profile && state.profile.names) || [];
    return names.map(function (name) {
      return { value: name, label: String(name) };
    });
  }
  return [];
}

function paramValue(action, param, options) {
  var mine = paramDrafts[action.id] || {};
  if (has(mine, param.name)) return mine[param.name];
  var detected = state && state.profile ? state.profile.detected : null;
  var offersDetected = options.some(function (entry) {
    return entry.value === detected;
  });
  if (detected && offersDetected) return detected;
  if (param.type === "enum" && options.length) return options[0].value;
  return "";
}

function paramControl(action, param) {
  var wrap = el("div", "param");
  var name = param.label || param.name;
  var ctx = {
    setting: { label: name },
    off: false,
    parts: [],
    id: domId("param", action.id + "-" + param.name),
    home: domId("action", action.id)
  };
  var mine = (paramDrafts[action.id] = paramDrafts[action.id] || {});
  var control;
  if (param.type === "enum") {
    var options = paramOptions(param);
    var value = paramValue(action, param, options);
    mine[param.name] = value;
    control = dropDown(ctx, options, value, null, function (chosen) {
      mine[param.name] = chosen;
    });
  } else {
    control = textInput(ctx, paramValue(action, param, []));
    mine[param.name] = control.value;
    control.addEventListener("input", function () {
      mine[param.name] = control.value;
    });
  }
  wrap.appendChild(labelFor(ctx, control, name + (param.required ? " (required)" : "")));
  wrap.appendChild(control);
  var why = sourceNote(param.choices_from);
  if (why) {
    var empty = el("p", "note", why);
    empty.id = ctx.id + "-src";
    control.setAttribute("aria-describedby", empty.id);
    wrap.appendChild(empty);
  }
  if (param.type !== "enum" && param.type !== "str") {
    wrap.appendChild(el("p", "note", "This page does not know the field type “" + param.type + "”, so it is plain text."));
  }
  return wrap;
}

function actionCard(action) {
  var card = el("div", "action-card");
  card.id = domId("action", action.id);
  card.setAttribute("data-action", action.id);
  card.setAttribute("tabindex", "-1"); // where focus goes when its button is turned off
  var status = actionState(action);
  (action.params || []).forEach(function (param) {
    card.appendChild(paramControl(action, param));
  });
  var row = el("div", "row");
  var button = el("button", action.danger ? "danger" : null, action.label || action.id);
  button.type = "button";
  button.id = domId("run", action.id);
  button.setAttribute("data-run", action.id);
  button.setAttribute("data-home", card.id);
  button.disabled = !status.available;
  button.addEventListener("click", function () {
    runAction(action, Object.assign({}, paramDrafts[action.id] || {}));
  });
  row.appendChild(button);
  if (action.danger) row.appendChild(el("span", "badge", "Cannot be undone"));
  if (action.confirm || action.danger) row.appendChild(el("span", "badge", "Asks first"));
  card.appendChild(row);
  var described = [];
  if (action.help) {
    var help = el("p", "hint", action.help);
    help.id = domId("ahelp", action.id);
    card.appendChild(help);
    described.push(help.id);
  }
  if (!has(ACTION_KINDS, action.kind)) {
    card.appendChild(
      el("p", "note", "This page does not know the action kind “" + action.kind + "”; the button runs it and shows the answer.")
    );
  }
  if (status.status) {
    var said = el("p", "note", "Now: " + status.status);
    said.id = domId("astatus", action.id);
    card.appendChild(said);
    described.push(said.id);
  }
  if (!status.available) {
    var why = el("p", "note", "Not available: " + (status.reason || "no reason given") + ".");
    why.id = domId("awhy", action.id);
    card.appendChild(why);
    described.push(why.id);
  }
  var job = jobFor(action.id);
  if (job) {
    if (job.state === "running") {
      var bar = el("progress");
      bar.max = 1;
      if (typeof job.progress === "number") bar.value = job.progress;
      bar.setAttribute("aria-label", (action.label || action.id) + " progress");
      card.appendChild(bar);
    }
    var said2 = el("p", job.state === "failed" ? "bad" : "note", jobWords(job));
    said2.id = domId("ajob", action.id);
    card.appendChild(said2);
    described.push(said2.id);
  }
  if (has(actionNotes, action.id)) {
    var told = el("p", actionNotes[action.id].bad ? "bad" : "note", actionNotes[action.id].text);
    told.id = domId("anote", action.id);
    card.appendChild(told);
    described.push(told.id);
    if (actionNotes[action.id].bad) button.setAttribute("aria-invalid", "true");
  }
  if (described.length) button.setAttribute("aria-describedby", described.join(" "));
  return card;
}

function renderProfile() {
  var box = $("profile");
  var action = actionById("apply_profile");
  var profile = state.profile || null;
  if (!action && !profile) {
    box.replaceChildren();
    return;
  }
  var section = el("section", "profile");
  var heading = el("h2", null, "Machine profile");
  heading.id = "profile-heading";
  section.setAttribute("aria-labelledby", heading.id);
  section.appendChild(heading);
  var detected = profile && profile.detected ? String(profile.detected) : "";
  section.appendChild(
    el("p", null, detected ? "This Mac was detected as: " + detected + "." : "No profile was detected for this Mac.")
  );
  if (profile && Array.isArray(profile.names) && profile.names.length) {
    section.appendChild(el("p", "hint", "Profiles: " + profile.names.join(", ") + "."));
  }
  if (action) section.appendChild(actionCard(action));
  box.replaceChildren(section);
}

/** Run one action. Asks first when the schema says to; a job is followed
 *  by polling until it is done or failed, and then the state is fetched. */
async function runAction(action, params) {
  if (!action) return null;
  var label = action.label || action.id;
  if (action.confirm || action.danger) {
    var question =
      "Run “" + label + "” now?" +
      (action.help ? "\n\n" + action.help : "") +
      (action.danger ? "\n\n“" + label + "” cannot be undone." : "");
    if (!window.confirm(question)) {
      announce(label + " was not run.");
      return null;
    }
  }
  var missing = (action.params || []).filter(function (param) {
    var value = params[param.name];
    return param.required && (value === undefined || value === null || value === "");
  });
  if (missing.length) {
    var names = missing.map(function (param) {
      return param.label || param.name;
    });
    actionNotes[action.id] = { text: "Fill in " + names.join(", ") + " first.", bad: true };
    render();
    announce(actionNotes[action.id].text);
    return null;
  }
  delete actionNotes[action.id];
  announce("Starting " + label + "…");
  var result = await api("POST", "/api/action/" + encodeURIComponent(action.id), { params: params });
  if (result.ok) {
    var data = result.data || {};
    if (data.job) {
      jobs[data.job] = { id: data.job, action: action.id, state: "running", progress: null, message: data.message || "" };
      render();
      announce(label + " started" + (data.message ? ": " + data.message : "."));
      pollJobs();
    } else {
      actionNotes[action.id] = { text: data.message || label + " is done.", bad: false };
      announce(actionNotes[action.id].text);
      await poll();
    }
    return result;
  }
  if (result.kind === "gone" || result.kind === "auth") return result;
  var text = result.status === 409 ? "Another action is running. Try again when it finishes." : result.message;
  actionNotes[action.id] = { text: text, bad: true };
  render();
  announce(label + " did not run: " + text);
  return result;
}

/** Ask how the jobs are doing; again in a moment while any runs. When one
 *  ends the state is fetched, because what it did shows there. */
async function pollJobs() {
  if (!anyRunning()) return null;
  var result = await api("GET", "/api/action/status");
  if (!result.ok) return result;
  var ended = [];
  ((result.data && result.data.jobs) || []).forEach(function (job) {
    var before = jobs[job.id];
    jobs[job.id] = job;
    if (before && before.state === "running" && job.state !== "running") ended.push(job);
  });
  render();
  ended.forEach(function (job) {
    announce(jobWords(job));
  });
  if (anyRunning() && jobTimer === null) {
    jobTimer = setTimeout(function () {
      jobTimer = null;
      pollJobs();
    }, JOB_POLL_MS);
  }
  if (ended.length) await poll();
  return result;
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
  $("page-address").textContent = window.location.host;
  $("recheck").addEventListener("click", function () {
    clearError();
    refresh();
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

  await start();
  if (!dead) pingTimer = setInterval(ping, PING_MS);
}

function ping() {
  // The result is handled inside `api`: a closed portal shows the banner and
  // stops this timer. Nothing here retries anything.
  api("GET", "/api/ping");
}

/* The save bar is in the document before this script runs (the script tag
 * is the body's last child), so it is wired here, once. */
$("save").addEventListener("click", saveSettings);
$("discard").addEventListener("click", discardEdits);

/** What a test drives. The page itself goes through the same functions. */
window.portal = {
  api: api,
  start: start,
  refresh: refresh,
  canWrite: canWrite,
  saveSettings: saveSettings,
  collect: collect,
  pollJobs: pollJobs,
  runAction: runAction,
  chordOf: chordOf,
  showError: showError,
  clearError: clearError,
  renderers: RENDERERS,
  state: function () {
    return state;
  }
};

document.addEventListener("DOMContentLoaded", boot);
