# Update-ping receiver (Cloudflare Worker)

The server side of the daily update check that `mc/update_ping.py` sends. **Not
deployed. This folder is code only; nothing here has touched a Cloudflare account.**

## What it does

`POST /api/ping` with exactly `{"install_id", "version", "os"}` ->
upserts one row in D1 (`install_id`, `first_seen`, `last_seen`, `version`, `os`) and
replies `{"latest_version": "<LATEST_VERSION var>"}`. Any other method, any extra or
missing field, a malformed id/version/os, or a body over 1 KB gets a 4xx and
stores nothing. The Worker never reads or stores the caller's IP or headers
(Cloudflare itself still sees the connection, as it does for any site on it).

`install_id` is a random UUID the client keeps in `~/.clayrune/ping_id`. It is not
the machine hostname and cannot be tied to a person.

## Deploy (when you decide to)

```bash
cd workers/update-ping
cp wrangler.toml.example wrangler.toml          # fill in your D1 id, route, LATEST_VERSION
wrangler d1 create clayrune-update-ping         # paste the id into wrangler.toml
wrangler d1 execute clayrune-update-ping --remote --file=schema.sql
wrangler deploy
```

Release a new version: change `LATEST_VERSION` and `wrangler deploy`. Clients show
"update available" when it is newer than the version they run.

## Count installs

```bash
wrangler d1 execute clayrune-update-ping --remote --command \
  "SELECT COUNT(*) AS active_30d FROM installs WHERE last_seen > strftime('%s','now','-30 days')"
wrangler d1 execute clayrune-update-ping --remote --command \
  "SELECT os, COUNT(*) FROM installs GROUP BY os"
```

Counts are a floor, not a census: installs that turned the ping off, set
`DO_NOT_TRACK`/`CLAYRUNE_NO_PING`, or never opened the dashboard after updating
(the ping waits until the notice has been shown) are not in it.

## Client config

The client posts to `update_ping_url` (default `https://clayrune.io/api/ping`) in
`config.json`. It is deliberately not editable from Settings. Point it at your
own route to test, then reset it.
