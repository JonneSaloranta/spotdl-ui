# User guide

## Logging in

Your administrator creates your account (there is no public sign-up). If you forget your
password, use "Forgot your password?" on the login page — this only works if the site has SMTP
configured; ask your administrator if you don't receive the email.

## Downloading music

1. Go to the main page and paste one or more URLs into the box — one per line. Supported: a
   single track, an album, or a playlist, from Spotify (`open.spotify.com`, `spotify.link`) or
   YouTube/YouTube Music (`youtube.com`, `music.youtube.com`, `youtu.be`).
2. Click "Start download". You're taken to that batch's page, which updates automatically every
   few seconds — no need to refresh.
3. Each track shows its status (queued → downloading → processing → completed/failed),
   a progress bar, and its current stage. A batch stays useful even if some tracks in it fail —
   the rest keep going.
4. If a track shows a **Duplicate** badge, an exact copy (same file content) already exists in
   the library — nothing new was downloaded for it. **Possible duplicate** means the artist and
   title match something already in the library but the file content differs (e.g. a different
   encode or a live version) — it was still downloaded as usual; the badge is just a heads-up.
5. **Retry** appears on failed tracks; **Cancel** appears on anything still in progress.
   "Cancel remaining" at the top of a batch cancels everything in it that hasn't finished yet.

## Browsing the library

The "Library" link in the navbar lists every track ever downloaded (by anyone), searchable by
artist/title/album. This is a shared library, not per-user — if you and another user both
download the same album, you'll see one entry, not two (per the duplicate detection above).

## Notifications

If your administrator has configured SMTP, you'll get one email when each of your batches
finishes (whether it fully succeeded or not) — never one email per track. Turn this off per
outcome (completed vs. failed) from your profile page.

## Theme and language

The theme switcher (top right) offers Light, Dark, and Automatic (follows your system). Your
choice is remembered on this device immediately and, once you're logged in, saved to your
account so it follows you to other devices too.

The language switcher changes the UI language without changing the page you're on or its URL —
switch back and forth freely, your place in the app doesn't move.

## Shared import links

If someone sends you a link like `https://<host>/share/<token>/`, you can submit URLs through
it without an account. It's temporary — the page will tell you plainly if the link has expired,
been disabled, or reached its use limit, without an account or login of any kind.
