# InforEstudante vacancy watcher

This is a separate Python 3.12 package for observing the verified
InforEstudante course row and reporting a PL3 vacancy. It is fail-closed: the
default notification mode never submits enrollment.

## Safety gates

Autonomous enrollment is possible only when all of these are true:

1. `MODE=enroll`;
2. `ENABLE_ENROLLMENT=true`; and
3. `ENROLLMENT_ACK=02038756:PL3:249089` exactly.

The Compose defaults leave enrollment disabled. After a submit, the watcher
stops after authoritative confirmation. A navigation or verification ambiguity
is latched in `/data/state.json` as manual intervention and is never retried
automatically. `auth-state.json` contains browser cookies and is equivalent to
a password; it is ignored by Git and must never be committed or shared.
The watcher also takes a cross-process cycle lock. A durable `submitting`
marker is written before the single Save click; after a crash, the next cycle
checks the authoritative list and either confirms success or requires manual
intervention without submitting again.

## Local authentication capture

Install the package and its exact browser dependency in a Python 3.12 virtual
environment, then run:

```text
python -m pip install -e .
python -m playwright install chromium
python -m vacancy_watcher capture-auth
```

The command opens a headed browser. Complete login manually and press Enter
in the terminal. It never asks for, reads, or stores a username or password.
The host default is `data/auth-state.json`; Compose overrides it to
`/data/auth-state.json`. After you press Enter, the command revisits the exact
enrollment-list URL and saves state only if the expected course row is present.
Keep the file permissions restrictive.

## One-shot notification check

With a captured state mounted at `/data/auth-state.json`, use a headless run:

```powershell
$env:MODE = "notify"
$env:STORAGE_STATE_PATH = "data/auth-state.json"
$env:DATA_PATH = "data/state.json"
python -m vacancy_watcher once
```

`POLL_INTERVAL` is clamped to at least 10 seconds for polling. Authentication
or manual-intervention states back off for `AUTH_RETRY_INTERVAL` (900 seconds
by default) while reloading an updated auth file on the next cycle.
`WEBHOOK_URL` is optional; JSON events are always printed to stdout. State and
refreshed authentication use atomic replacement. POSIX runs also request mode
`0600`; Windows ACLs and bind-mount ownership must be secured separately.

## Compose usage

Create a local `data/` directory, place a captured `auth-state.json` there,
then run:

```text
docker compose run --rm vacancy-watcher once
docker compose up -d vacancy-watcher
```

To explicitly enable autonomous enrollment, supply all gates for that command
or service and review the risk first:

```text
docker compose run --rm \
  -e MODE=enroll \
  -e ENABLE_ENROLLMENT=true \
  -e ENROLLMENT_ACK=02038756:PL3:249089 \
  vacancy-watcher once
```

The image uses `mcr.microsoft.com/playwright/python:v1.63.0-noble` pinned to
OCI index digest
`sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0`,
and the package pins `playwright==1.63.0`. No live PL3 vacancy behavior is
claimed as verified by this repository.

Compose runs as the image's unprivileged `pwuser`, drops all capabilities,
sets `no-new-privileges`, uses a read-only root filesystem and private 256 MiB
shared memory, bounds processes/CPU/memory/logs, and exposes no host ports.
Only `/data` and `/tmp` are writable. A health check detects a stalled polling
loop or a latched manual-intervention state. Run only this trusted portal
workflow and do not add unrelated browsing to the service.

Copy `.env.example` to an ignored `.env` only when persistent overrides are
needed. Do not commit `.env`, `data/`, or `auth-state.json`. The webhook URL is
restricted to direct HTTPS on port 443 and redirects are rejected.

## Portal facts and assumptions

### Live-validated facts supplied for this implementation

- Every cycle starts at the exact HTTPS list URL and locates the exact first
  cell code `02038756` and title `Segurança e Privacidade`.
- The course link is followed from the current row (`inscrever.do?args=...`);
  the dynamic `args` value is never hard-coded or logged.
- The target is PL3, profile `PL`, current class id/value `249089`.
- The form is `inscreverFormBean`, POST action
  `/nonio/inscturmas/inscrever.do?method=submeter`, with save id
  `botaoGravar`.
- A zero-seat PL3 row has no enrollment input; a vacancy must have an enabled
  target checkbox. The client unchecks other `inscrever` controls with
  `alt=PL` before staging PL3.
- Authoritative success is the redirected list page showing PL3 for the exact
  course.

### Deliberately unverified assumptions

- The HTML table remains represented by `tr`/`td` rows and the vacancy count is
  the fourth cell, as in the supplied six-cell snapshots.
- Login expiry is represented by a redirect or visible password input.
- A generic JSON webhook accepts the event payloads without service-specific
  headers or authentication.
- Container browser/runtime behavior, live PL3 vacancy behavior, and the
  current portal's response timing have not been tested here.

## Tests

Tests are pure `unittest` tests with mocked data and no browser downloads or
live portal calls:

```powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
python -m unittest discover -s tests -v
python -m compileall -q src tests
```
