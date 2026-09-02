// Placeholder page for the config portal. Run 9 replaces it with the real
// tabs; all this does is prove the auth handshake and print the state.
//
// Three rules this file has to keep, and the run-9 page after it:
//   - no inline script and no external resource, so the shipped CSP
//     (default-src 'self') is never the thing that has to be relaxed;
//   - the one-time code is read from the URL fragment, which the browser
//     does not send to the server, and is dropped from the address bar as
//     soon as it has been exchanged;
//   - the session token stays in this closure. Not localStorage, not a
//     cookie, not a query string -- only the X-Vocalize-Token header.
(function () {
  "use strict";

  var statusEl = document.getElementById("status");
  var stateEl = document.getElementById("state");
  var token = null;

  function say(message) {
    // textContent, never innerHTML: server text is never markup here.
    statusEl.textContent = message;
  }

  var code = new URLSearchParams(window.location.hash.slice(1)).get("code");
  window.history.replaceState(null, "", window.location.pathname);

  if (!code) {
    say("No code in the address. Start the portal again with: vocalize portal");
    return;
  }

  fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code })
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("The code was refused. Start the portal again.");
      }
      return response.json();
    })
    .then(function (session) {
      token = session.token;
      // The server closes itself once the page stops answering (four
      // missed 15 s pings), so an open tab has to keep saying it is here.
      // Without this the portal would exit a minute after it loaded and
      // the next click would fail with a connection error.
      window.setInterval(function () {
        // Swallowed on purpose: once the portal has closed, every ping
        // fails, and an uncaught rejection every 15 s is only noise.
        fetch("/api/ping", { headers: { "X-Vocalize-Token": token } }).catch(function () {});
      }, 15000);
      return fetch("/api/state", {
        headers: { "X-Vocalize-Token": token }
      });
    })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("The portal refused the session token.");
      }
      return response.json();
    })
    .then(function (state) {
      say("Connected.");
      stateEl.textContent = JSON.stringify(state, null, 2);
    })
    .catch(function (error) {
      say(error.message || String(error));
    });
})();
