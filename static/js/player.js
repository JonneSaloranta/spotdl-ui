/* Persistent library music player.
 *
 * Lives outside #app-shell in templates/base.html so htmx-boosted
 * navigation (hx-boost on <body>, scoped to #app-shell) never touches —
 * and never interrupts — this element or the <audio> it controls.
 * Consequently this script itself only ever runs once, on the first real
 * page load; it must not assume the DOM it queries at startup (play
 * buttons, in particular) is the DOM that still exists after the user
 * has navigated a few times, hence the delegated click listener below
 * instead of binding to each button directly.
 */
(function () {
  "use strict";

  var audio = document.getElementById("player-audio");
  var bar = document.getElementById("player-bar");
  if (!audio || !bar) return;

  var toggleBtn = document.getElementById("player-toggle");
  var toggleIcon = toggleBtn.querySelector("i");
  var prevBtn = document.getElementById("player-prev");
  var nextBtn = document.getElementById("player-next");
  var titleEl = document.getElementById("player-title");
  var artistEl = document.getElementById("player-artist");
  var coverEl = document.getElementById("player-cover");
  var seek = document.getElementById("player-seek");
  var curTimeEl = document.getElementById("player-current-time");
  var durTimeEl = document.getElementById("player-duration");
  var volume = document.getElementById("player-volume");
  // e.g. "/library/0/neighbors/" — id-in-path URL rendered once by
  // Django (apps/library/urls.py); JS just substitutes the real id in
  // rather than hardcoding the path here.
  var neighborsUrlTemplate = audio.getAttribute("data-neighbors-url-template");

  var currentTrackId = null;
  var prevTrack = null;
  var nextTrack = null;
  var seekDragging = false;
  var neighborsRequestId = 0;

  function formatTime(totalSeconds) {
    if (!isFinite(totalSeconds) || totalSeconds < 0) totalSeconds = 0;
    totalSeconds = Math.floor(totalSeconds);
    var minutes = Math.floor(totalSeconds / 60);
    var seconds = totalSeconds % 60;
    return minutes + ":" + (seconds < 10 ? "0" : "") + seconds;
  }

  var savedVolume = parseFloat(localStorage.getItem("spotdl-ui:player-volume"));
  if (!isFinite(savedVolume) || savedVolume < 0 || savedVolume > 1) savedVolume = 1;
  audio.volume = savedVolume;
  volume.value = String(savedVolume);

  volume.addEventListener("input", function () {
    audio.volume = parseFloat(volume.value);
    localStorage.setItem("spotdl-ui:player-volume", volume.value);
  });

  function setIcon(el, playing) {
    el.classList.toggle("bi-play-fill", !playing);
    el.classList.toggle("bi-pause-fill", playing);
  }

  function syncTrackButtons() {
    // Re-applies "currently playing" state to whatever .play-track-btn
    // elements exist right now, including ones an htmx-boosted
    // navigation just swapped in — those never received a click of
    // their own, so nothing else would otherwise tell them apart from a
    // track that isn't playing.
    document.querySelectorAll(".play-track-btn").forEach(function (btn) {
      var isCurrent = currentTrackId !== null && String(currentTrackId) === btn.getAttribute("data-id");
      var playing = isCurrent && !audio.paused;
      setIcon(btn.querySelector("i"), playing);
      btn.setAttribute("aria-pressed", playing ? "true" : "false");
    });
  }

  function fetchNeighbors(id) {
    var requestId = ++neighborsRequestId;
    fetch(neighborsUrlTemplate.replace("/0/", "/" + id + "/"))
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (data) {
        if (!data || requestId !== neighborsRequestId) return;  // superseded by a newer track
        prevTrack = data.prev;
        nextTrack = data.next;
        prevBtn.disabled = !prevTrack;
        nextBtn.disabled = !nextTrack;
      })
      .catch(function () {
        // Losing prev/next isn't worth surfacing to the user — playback
        // of the current track is unaffected either way.
        if (requestId !== neighborsRequestId) return;
        prevTrack = null;
        nextTrack = null;
        prevBtn.disabled = true;
        nextBtn.disabled = true;
      });
  }

  function setCover(coverUrl) {
    if (!coverUrl) {
      coverEl.classList.add("d-none");
      coverEl.removeAttribute("src");
      return;
    }
    coverEl.onerror = function () {
      coverEl.classList.add("d-none");
      coverEl.removeAttribute("src");
    };
    coverEl.src = coverUrl;
    coverEl.classList.remove("d-none");
  }

  function loadAndPlay(id, url, title, artist, coverUrl) {
    currentTrackId = id;
    audio.src = url;
    titleEl.textContent = title;
    artistEl.textContent = artist || "";
    setCover(coverUrl);
    bar.classList.remove("d-none");
    document.body.classList.add("player-active");
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    audio.play();
    fetchNeighbors(id);
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".play-track-btn");
    if (!btn) return;
    var id = parseInt(btn.getAttribute("data-id"), 10);
    if (id === currentTrackId) {
      if (audio.paused) { audio.play(); } else { audio.pause(); }
      return;
    }
    loadAndPlay(
      id,
      btn.getAttribute("data-url"),
      btn.getAttribute("data-title"),
      btn.getAttribute("data-artist"),
      btn.getAttribute("data-cover-url")
    );
  });

  prevBtn.addEventListener("click", function () {
    if (prevTrack) loadAndPlay(prevTrack.id, prevTrack.url, prevTrack.title, prevTrack.artist, prevTrack.cover_url);
  });
  nextBtn.addEventListener("click", function () {
    if (nextTrack) loadAndPlay(nextTrack.id, nextTrack.url, nextTrack.title, nextTrack.artist, nextTrack.cover_url);
  });

  audio.addEventListener("ended", function () {
    if (nextTrack) {
      loadAndPlay(nextTrack.id, nextTrack.url, nextTrack.title, nextTrack.artist, nextTrack.cover_url);
    } else {
      syncTrackButtons();
    }
  });

  audio.addEventListener("play", function () {
    setIcon(toggleIcon, true);
    toggleBtn.setAttribute("aria-pressed", "true");
    syncTrackButtons();
  });

  audio.addEventListener("pause", function () {
    setIcon(toggleIcon, false);
    toggleBtn.setAttribute("aria-pressed", "false");
    syncTrackButtons();
  });

  toggleBtn.addEventListener("click", function () {
    if (!audio.src) return;
    if (audio.paused) { audio.play(); } else { audio.pause(); }
  });

  audio.addEventListener("loadedmetadata", function () {
    seek.max = String(Math.floor(audio.duration) || 0);
    durTimeEl.textContent = formatTime(audio.duration);
  });

  audio.addEventListener("timeupdate", function () {
    if (!seekDragging) {
      seek.value = String(Math.floor(audio.currentTime));
    }
    curTimeEl.textContent = formatTime(audio.currentTime);
  });

  seek.addEventListener("input", function () {
    seekDragging = true;
    curTimeEl.textContent = formatTime(parseFloat(seek.value));
  });

  seek.addEventListener("change", function () {
    audio.currentTime = parseFloat(seek.value);
    seekDragging = false;
  });

  // A boosted navigation can bring the current track's own play button
  // back into the page (e.g. leaving the library and returning to it) —
  // re-sync its icon/aria-pressed state once the new content has settled.
  document.body.addEventListener("htmx:afterSettle", syncTrackButtons);
})();
