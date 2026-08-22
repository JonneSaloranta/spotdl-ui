/* Shows Django's messages (templates/base.html's .toast-container) as
 * Bootstrap toasts that dismiss themselves after a few seconds, instead of
 * inline alerts that just sit in the page until manually closed.
 *
 * Bootstrap's Toast component only ever animates in when its JS is told to
 * .show() it — a plain .toast element in the DOM stays invisible otherwise
 * — so this has to run again after every htmx-boosted navigation swaps
 * #app-shell (a fresh set of messages, if any, each time), not just once on
 * the initial page load.
 */
(function () {
  "use strict";

  var AUTOHIDE_DELAY_MS = 5000;

  function showNewToasts() {
    document.querySelectorAll(".toast:not([data-toast-shown])").forEach(function (el) {
      el.setAttribute("data-toast-shown", "true");
      new bootstrap.Toast(el, { autohide: true, delay: AUTOHIDE_DELAY_MS }).show();
    });
  }

  document.addEventListener("DOMContentLoaded", showNewToasts);
  document.body.addEventListener("htmx:afterSwap", showNewToasts);
})();
