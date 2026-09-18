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
The management console adds a separate one-shot runtime arm. Even with all
three boot-time gates open, enrollment cannot submit until the exact
acknowledgement is entered in the console. Pausing monitoring automatically
disarms enrollment.

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

The long-running service includes its management console. Compose publishes it
on server loopback at `127.0.0.1:18782` by default. Reach the default deployment
through an authenticated SSH tunnel:

```text
ssh -N -L 18782:127.0.0.1:18782 diogoserver
```

Then open `http://127.0.0.1:18782/`. The SSH connection is the authentication
boundary. The console also enforces an explicit Host allowlist, a client-IP
allowlist, same-origin requests, and a random CSRF token; it loads no external
assets and exposes neither cookie state nor webhook configuration.

For a reviewed single-client LAN deployment, set all three values together:

```text
WATCHER_BIND_ADDRESS=<server-lan-ip>
UI_ALLOWED_HOSTS=<server-lan-ip>
UI_ALLOWED_CLIENTS=<client-lan-ip>
```

Bind only the server's specific LAN address, never `0.0.0.0`. Before changing
the bind, install a persistent Docker-aware firewall policy that allows the
client `/32` and drops every other source for host TCP `18782`; ordinary UFW
input rules alone may not filter Docker-published traffic. Verify an authorized
request from the allowed client and denied requests from another LAN client,
VPN, IPv6, and the public path. IP allowlisting is network authorization, not
user authentication: anyone controlling or spoofing the allowed client can
reach the console. Do not expose it through Cloudflare, a reverse proxy, or the
public Internet without a separately reviewed authentication layer.

`10.89.0.1` is the fixed gateway of this Compose project's dedicated network
and is retained only by the default loopback deployment so host-originated
health/operator requests work. Override `UI_ALLOWED_CLIENTS` with only the
approved LAN client addresses when using a LAN bind; otherwise traffic from
other local Docker networks can be masqueraded as that gateway and pass the
application allowlist.

The console can pause or resume future cycles, request an immediate check, and
arm or disarm the one-shot enrollment path. Pausing does not interrupt a
submission that has already reached the durable `submitting` state. A latched
manual-intervention state cannot be cleared from the console; verify the
authoritative enrollment list before repairing state on the host.

When `TLS_ENABLED=true`, the console provides a two-stage **university login**
flow. **Prepare university login** first opens a fresh headless browser at the
fixed UC HTTPS origin without collecting credentials. If the portal presents
its text CAPTCHA, the watcher captures that challenge and displays it in the
console. The operator then enters the university username and password, plus a
CAPTCHA response only when that session shows a challenge. Those submitted
values are handed once to the same waiting browser session, cleared from
process memory after use, and never written to disk or logs. The prepared
session expires after five minutes. Only an independently
verified Playwright session state is atomically written to
`/data/auth-state.json`; a failed login never replaces a previous session.
The watcher does not solve or bypass CAPTCHAs. Never enable this form over
plain HTTP. The deployment certificate must be trusted on the authorized
client before entering credentials; do not bypass a browser certificate
warning.

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

The image defaults to its unprivileged `pwuser`. Compose explicitly runs as
`WATCHER_UID:WATCHER_GID` (`1000:1000` by default) so the process matches the
owner of the mode-700 bind-mounted `data/` directory on `diogoserver`. Set these
two values to the owning numeric UID/GID before deploying on another host.
Compose drops all capabilities,
sets `no-new-privileges`, uses a read-only root filesystem and private 256 MiB
shared memory, and bounds processes/CPU/memory/logs. The management port is
loopback-bound unless the explicit LAN variables and corresponding firewall
policy are supplied.
The dedicated Compose network uses `10.89.0.0/28`, selected after checking the
target host's Docker subnets and IPv4 routes. Revalidate that it does not
overlap before deploying this Compose file to a different host.
Only `/data` and `/tmp` are writable. A health check detects a stalled polling
loop or a latched manual-intervention state. Run only this trusted portal
workflow and do not add unrelated browsing to the service.

Copy `.env.example` to an ignored `.env` only when persistent overrides are
needed. Do not commit `.env`, `data/`, or `auth-state.json`. The webhook URL is
restricted to direct HTTPS on port 443 and redirects are rejected.

`data/auth-state.json` and `data/state.json` are the only persistent files.
No backup is configured: losing them is recoverable by capturing a new session,
and a missing state file recreates conservative defaults with enrollment
disarmed. Roll back by checking out the previously recorded Git revision,
rebuilding, and running `docker compose up -d`; never delete the data directory
as part of routine rollback.

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
