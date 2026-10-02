# Contributing to Clayrune

Contributions are welcome, and so are small ones: a typo, a confusing message, a
test for something that broke once. Clayrune is maintained by one person, so a
PR may wait a few days for a reply. A reply is the normal outcome.

Where things go:

- **Something is broken, or you have a question or an idea:** an
  [issue](https://github.com/clayrune-io/clayrune/issues/new/choose). A one-line
  report is fine. [SUPPORT.md](SUPPORT.md) has the rest.
- **A security problem:** not an issue. See [SECURITY.md](SECURITY.md).
- **A code change:** a pull request, as below. For anything bigger than a bug
  fix, open an issue first so you do not build something that cannot be merged.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Run it from source

Needs Python 3.11 or newer and the
[Claude CLI](https://docs.anthropic.com/en/docs/claude-code/getting-started)
signed in. The interpreter floor is enforced in `mc/preflight.py`.

```bash
git clone https://github.com/clayrune-io/clayrune.git
cd clayrune
pip install -r requirements-dev.txt   # includes requirements.txt, pytest, pyright
python server.py                      # http://localhost:5199
# or: python app.py                   # the same server in a native desktop window
```

By default the server listens on all interfaces, because LAN, phone pairing and
tunnel clients depend on it. Set `MC_BIND_LOOPBACK=1` to listen on loopback only
while you develop, `MC_PORT=8080` to use another port, and `MC_DATA_DIR` to keep
a throwaway copy of your data somewhere else. Settings and your projects are
written under `data/` and `config.json`; that is your data, not source, and it
must never end up in a commit.

There is no build step. The backend is Flask (`server.py`, `mc/`). The front end
is plain JavaScript modules in `static/js/` and CSS in `static/css/`, served as
they are. Edit a file, then hard-reload the browser tab: a server restart does
not reload a tab that is already open, and an open tab keeps running the old
JavaScript.

## Check your change

Run what covers the files you touched.

**Python tests.** From the repo root:

```bash
pytest                       # main app, ./tests
pytest control_plane/tests   # only if you touched control_plane/
pytest tests/test_secrets_store.py   # or one file while you work
```

`pytest` reads its settings from `pyproject.toml`. A guard in `tests/` fails any
test that writes to your real data directory, so a test that needs one should
use the `tmp_data_dir` fixture. [`tests/README.md`](tests/README.md) describes
the fixtures. These two commands are what the PR workflow runs.

**Type checking.** `pyright` with no arguments checks the scope in
`pyproject.toml` (`[tool.pyright]`: the `mc/` package, minus a short exclude
list). New or moved modules under `mc/` should pass at the basic level. CI runs
pyright without blocking, because there is an existing baseline of errors in
`mc/distiller.py` and `mc/agent_runtime.py`. Do not add to it.

**Front end.** The smoke tests drive the real page in headless Chromium with
Playwright. If you touched anything under `static/`:

```bash
cd tools/smoke
npm install
npx playwright install chromium   # one time
npm test                          # the CI set
node boot-smoke.mjs               # or a single file
```

`boot-smoke.mjs` is the one that matters most: it fails when a runtime error
during boot leaves the dashboard stuck on "Loading...", which a syntax check
does not catch. Most files in `tools/smoke/` run against a stub API and need no
server. A few (for example `split-view-real.mjs`) drive a server you already have
running, at `MC_BASE` (default `http://localhost:5199`). That server serves
whichever checkout it was started from, so make sure it is yours before
trusting the result. See [`tools/smoke/README.md`](tools/smoke/README.md).

A change you have not seen behave is not finished. For a bug fix, add a test that
fails before your change and passes after it. If you cannot write one, say why in
the PR.

## Make the change

- Branch from `master` and keep one concern per PR.
- Match the code around you: its naming, its comment density, its idiom. Your
  change should be hard to spot in a blame view.
- Treat odd-looking guards and branches as load-bearing until you have read why
  they are there. Many exist because something already broke once, and a comment
  or a test usually says so. Do not weaken a safety check to make a test pass.
  If one is in your way, say so in the PR.
- Where you touch a function with `except Exception: pass`, decide whether it is
  cosmetic cleanup or wraps file I/O, JSON state, a subprocess or the network.
  In the second case log the error (`_log(...)`) rather than swallowing it
  silently.
- Nothing specific to your own machine goes in the repo: no personal paths, IPs,
  email addresses, or credentials. This repo is read by other people's installs
  and agents, so ask whether it would be wrong on a stranger's machine.
  Credentials never go in a command line or a file. [`docs/SECRETS.md`](docs/SECRETS.md)
  explains the vault.
- User-visible changes get a dated entry in [CHANGELOG.md](CHANGELOG.md), newest
  first, in the same shape as the entries already there.

## Commit

Stage the files you edited by explicit path:

```bash
git add path/to/file.py tests/test_file.py
git commit
```

Do not use `git add -A`, `git add .` or `git commit -a`. A working tree often
carries unrelated changes (your own project data under `data/projects/`, local
backups), and a sweeping add commits them. Scratch files belong in `_scratch/`,
which is gitignored.

Write the commit message for whoever finds the line in six months: what was
wrong, why this fixes it, and what undoing it would break.

## Open the pull request

Push your branch to a fork and open a PR against `master`. The
[template](.github/PULL_REQUEST_TEMPLATE.md) asks what changed, how you checked
it, and what you left alone. `master` is the release channel, so it should always
be in a state you would be willing to install.

## Licence

Contributions are accepted under the repository's [MIT licence](LICENSE).
`mc_remote/` and `mc_tunnel/` are proprietary and are not open to outside
contributions. The open seam for remote access is `mc_remote_iface/`; see the
README's license section.
