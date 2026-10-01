# GBOS power control

## Diagnosis

In committed HEAD, remote_monitor.py sent RESTART_DEVICE / SHUTDOWN_DEVICE but
agent/src/main.rs had no matching handlers. The socket write was reported as
success although the agent ignored the message. The initial working tree already
contained partial fixes: handlers, command IDs, status logs, and bulk UI/routes.
This change completes those additions with correlated receipts, bounded waits,
stale-connection detection, administrator authorization, command expiry, and
UI reporting. Installed binaries and deployed source were not inspected remotely;
old installers must be rebuilt, not assumed to include these changes.

## Protocol and API

All power REST routes require the existing Bearer JWT, an active user, and an
exact stored email listed in POWER_ADMIN_EMAILS (case-sensitive). This allowlist is necessary because the
existing model has no roles and /auth/register is public. Empty denies access.

- POST /remote-monitoring/device/{device_id}/restart
- POST /remote-monitoring/device/{device_id}/shutdown
- POST /remote-monitoring/devices/restart
- POST /remote-monitoring/devices/shutdown
- GET /remote-monitoring/power/eligible
- GET /remote-monitoring/power/commands/{command_id}

Bulk POST accepts an optional JSON array of device IDs. The UI sends the exact
connected-device set shown in confirmation, so newly connected devices are not
silently added. Without a body, all active devices are considered. Responses
contain per-device results and counts; offline devices receive no queued command.
The UI separately reports devices offline at confirmation. Connections that
become stale/disconnect after confirmation are checked again during dispatch.

Backend sends {type: RESTART_DEVICE|SHUTDOWN_DEVICE, command_id, expires_at}
on the existing /ws/devices/{device_id} socket. Socket sends have a 3-second
limit. The backend then waits up to 8 seconds for agent status. A live connection
requires incoming traffic within 45 seconds. The agent rejects commands expired
at receipt (15-second dispatch lifetime; synchronize clocks). Nothing is replayed
after reconnect. There is no persistent or offline queue.

Lifecycle: pending -> sent -> acknowledged -> executing -> completed|failed.
'Sent' means only a socket write; no acknowledgement leaves delivery unconfirmed.
'Completed' means the OS accepted the native command, or explicitly reported
simulation in dry-run mode. It does NOT prove that a physical restart/shutdown
finished. The OS may disconnect the agent before its final receipt; executing
then remains the last confirmed state. UI polls receipts for another 10 seconds
and reports late failures. Request timeouts never imply success or trigger retry.

Receipts are correlated to device ID, command ID, command type, and the original
socket; stale/out-of-order/unsolicited status reports are ignored. Activity rows
retain audit events. Receipt lookup and duplicate locks last 10 minutes in memory;
backend restart loses these receipts. Duplicate power requests on the same socket
are rejected during this interval unless the previous request failed. A new agent
connection is a new session. The agent additionally guards execution across
socket reconnects for its process lifetime (reset on failure or dry-run).

Windows: fixed shutdown.exe /r /t 0 /f or shutdown.exe /s /t 0 /f, executed directly
without a shell. Existing service installs as LocalSystem. The /f option forces
application closure. macOS: fixed /sbin/shutdown -r now or -h now. Existing
LaunchDaemon runs as root; an ordinary terminal user may receive a privilege
failure. Native exit/spawn errors are reported back to the backend/UI.

## Configuration and deployment

1. Railway: set POWER_ADMIN_EMAILS to comma-separated existing administrator
   emails exactly as stored (case-sensitive). Preserve DATABASE_URL, JWT_SECRET_KEY and existing FRONTEND_URL settings.
   Deploy backend and use the Procfile (explicit --workers 1). Set Railway replicas
   to ONE. Existing WebSocket ownership is process-local; multiple replicas or
   workers cannot reliably route power requests. No Supabase migration/reset.
2. Build and distribute the updated agent to every managed machine. Deploy backend
   first, then agents, then frontend. Old agents cannot be made to execute missing
   handlers by a frontend deployment. The expiry field is required by the updated
   agent; an older backend will receive a rejected power command.
3. Build on Windows with `cargo build --release --bin agent`; update the installed
   binary using the existing installer/service workflow from an elevated terminal.
   The existing service installer uses LocalSystem. Run `agent.exe --status` after
   installing. Rebuild/replace hosted Windows downloads through the existing
   release process. Existing prebuilt EXEs in this checkout were not modified.
4. Build macOS with `cargo build --release --bin agent`. Use the existing
   `packaging/macos/build-dmg.sh` release workflow, install the package, and verify
   the root LaunchDaemon using `agent --status`. A local source rebuild alone
   does not replace a running installed service.
5. Deploy gbos/ to Vercel, preserving NEXT_PUBLIC_API_URL pointed at Railway.
   No frontend variable rename. Ensure the Vercel origin is allowed by backend CORS.
6. GBOS_POWER_COMMAND_DRY_RUN=1 is only for test process/service environments;
   leave it unset for real power control. The Rust agent does not load .env files
   automatically. Existing GBOS_API_URL remains unchanged.

## Safe local automated tests

From the project root:

```sh
cd /Users/gbaja/Desktop/gbos/backend
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m unittest discover -s tests -v

cd /Users/gbaja/Desktop/gbos/agent
rustc --edition 2024 --test src/power.rs -o /tmp/gbos-power-tests
/tmp/gbos-power-tests
cargo check --offline --bin agent

cd /Users/gbaja/Desktop/gbos/gbos
npx tsc --noEmit
npx eslint src/components/devices/DeviceTable.tsx
```

Backend tests force sqlite:// and use database test doubles, never the configured
production database. The WebSocket test uses the real server handler with a
simulated agent reporting the native-executor outcome. Rust tests validate both
Windows/macOS argv and run ONLY dry-run execution; they do not invoke shutdown.

For a manual end-to-end test, use a disposable local backend/database and an
isolated foreground agent, never the production service. Start the agent with:

```sh
cd /Users/gbaja/Desktop/gbos/agent
GBOS_API_URL=http://127.0.0.1:8000 GBOS_POWER_COMMAND_DRY_RUN=1 cargo run --bin agent
```

Configure the local frontend NEXT_PUBLIC_API_URL=http://127.0.0.1:8000, sign in as
an allowlisted administrator, and test both individual/global controls. Expect
'dry-run only; no OS power action'. Test a disconnected agent, empty fleet, and
expired login; verify loading buttons disable duplicates and errors are visible.
Do not send test power requests to a real service without explicit dry-run setup.

## Verification limits

Physical Windows/macOS power cycles are deliberately not tested on this development
Mac. Windows argument selection is unit-tested and `cargo check --offline --bin agent
--target x86_64-pc-windows-gnu` passes; the release binary still needs a Windows
build and service integration smoke test. Actual Railway/Vercel deployment
and installed agent versions are not verified by local tests. Browser UI validation
and local test results are recorded in the task report.

The pre-existing device registration and device WebSocket endpoint have no agent
credential authentication. This patch preserves that protocol for installer
compatibility; correlated command IDs are not a replacement for agent identity.
Provisioned per-device credentials are a separate hardening requirement before
trusting agent receipts against malicious socket impersonation. REST power control
itself is authenticated and restricted by the administrator allowlist.

## Additional verified smoke tests

The actual macOS debug agent was run against an isolated local backend with
GBOS_POWER_COMMAND_DRY_RUN=1. Both restart and shutdown returned completed receipts
explicitly reporting dry-run. This tests the real Rust WebSocket handler and
executor entry point. No physical OS power action was executed.

Reproduce (starts a temporary localhost server; no production database):

```sh
cd /Users/gbaja/Desktop/gbos/agent
cargo build --offline --bin agent
cd /Users/gbaja/Desktop/gbos/backend
PYTHONPATH=. venv/bin/python tests/smoke_agent.py
```

Browser tests use installed Google Chrome and intercept all backend requests.
They cover individual/bulk confirmation, targets, loading/disabled states,
accepted request text, offline errors, and late agent failure messages.

```sh
npm install --prefix /tmp/gbos-power-ui playwright
cd /Users/gbaja/Desktop/gbos/gbos
NEXT_PUBLIC_API_URL=http://127.0.0.1:8765 npm run dev -- --hostname 127.0.0.1 --port 3017
# In another terminal:
cd /Users/gbaja/Desktop/gbos/gbos
PLAYWRIGHT_MODULE=/tmp/gbos-power-ui/node_modules/playwright node tests/power-ui.cjs
```

Set CHROME_PATH if Chrome is installed somewhere other than the macOS default.
The temporary Playwright installation is not a production project dependency.
