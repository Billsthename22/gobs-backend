# Admin-approved agent updates

The Devices page includes an Agent updates panel. Workspace members can read versions and results; only administrators can register signed releases, approve updates, cancel queued jobs, or revoke releases. Registering a release does not install it. An administrator selects computers, reviews exact target versions, and approves.

The API persists releases, per-device version reports, and update jobs in three new tables created by the existing startup schema process. No existing device columns change. Agent versions come from authenticated reports, never from installer filenames. Existing 0.1.3 agents show “Not reported / Manual upgrade required”. They need one manual installation of an updater-enabled build (0.1.4 or later).

## Configure trust once

Generate an Ed25519 signing key on an administrator-controlled release machine, outside the repositories:

```sh
python scripts/sign_agent_release.py generate-key --output /secure/location/gbos-release-key.pem
```

The tool writes the private key with mode 600 and prints the base64 public key. Keep the private key offline or in a protected release pipeline; do not put it in the API environment, agent, frontend, or repository.

Set these backend environment variables:

```text
AGENT_UPDATE_PUBLIC_KEY=<the base64 public key>
AGENT_UPDATE_DOWNLOAD_HOSTS=<comma-separated exact HTTPS download hostnames>
```

Compile every agent with the same public key:

```sh
GBOS_UPDATE_PUBLIC_KEY='<the base64 public key>' cargo build --release --bin agent
```

The Windows workflow reads the repository variable `GBOS_UPDATE_PUBLIC_KEY`. The macOS packaging script inherits the same environment variable. Build, package, and manually install this bootstrap version on existing machines. Normal telemetry works in builds without a public key, but remote updating is disabled. Windows remote updates currently require the standard `%ProgramFiles%\GBOS\GBOS-Agent.exe` installation; macOS requires `/usr/local/libexec/gbos-agent`. Custom Windows installation paths require a manual upgrade.

## Publish and approve a release

1. Increment the Cargo package version and build a raw agent executable for each platform and architecture. Keep the same public key baked into it. Use stable `major.minor.patch` versions, without prerelease suffixes.
2. Upload each binary to an immutable HTTPS URL on an approved host. Remote updates use the **raw executable**, not the NSIS installer, PKG, or DMG. Disable redirects at the hosting URL.
3. Sign the exact uploaded bytes, using the intended runtime architecture (`x86_64` or `aarch64`):

```sh
python scripts/sign_agent_release.py sign \
  --key /secure/location/gbos-release-key.pem \
  --artifact /release/agent.exe \
  --version 0.1.5 --platform windows --architecture x86_64 \
  --url https://downloads.example.com/agent/0.1.5/windows-x86_64.exe \
  --output /release/windows-0.1.5.json
```

4. In Devices → Agent updates → Manage approved releases, paste the signed JSON and register it.
5. Start with one pilot computer. Select it, review the target version, and approve. Confirm a successful reconnection before approving more devices.

Release identity `(version, platform, architecture)` is immutable. Publish a higher version to replace a bad release. Revoking blocks queued or downloading installations; a helper that has already started installation can finish. Revocation does not roll devices back automatically.

## Delivery and recovery

- Agents check in every 30 seconds. Offline approvals remain queued for up to seven days. The service account needs HTTPS access to the API and artifact host; credentials are sent only to the API.
- A matching public key and supported installation path are required before a device is eligible. Only strictly newer versions for the reported OS and architecture can be approved.
- The agent verifies an Ed25519 signature over the version, OS, architecture, URL, SHA-256, and size, then downloads without redirects and checks the exact size and digest. Both the backend and agent enforce a 250 MB maximum. Downloads and preparation leave the running agent intact.
- A separate helper runs as SYSTEM through Task Scheduler on Windows, or as a separate system LaunchDaemon on macOS. The staging directory is restricted to administrators and the service account. The helper rechecks the signature and payload, saves the current binary, stops the service, replaces the binary, and starts it. It does not restart the computer or modify the device credential.
- The new agent must receive an authenticated WebSocket heartbeat and have its updater capability accepted by the API within two minutes. Otherwise the helper attempts to restore and restart the previous binary. A fresh signed release with an incorrect binary version also fails that readiness check.
- The transaction, backup, and receipt remain on disk. An interrupted installation is recovered by the helper at boot or the running agent's next poll. A staged approval older than ten minutes is rejected rather than installed late. If filesystem permissions, disk failure, or service control prevent recovery, a local administrator must intervene; rollback cannot repair an unavailable disk or operating system.
- Download jobs time out after 20 minutes; installation jobs after 10 minutes. Expired means the outcome needs investigation, not that the old binary is certainly running. A delayed receipt may reconcile the result later. Reapproval is explicit, never an automatic retry loop.
- Lifecycle transitions and approving administrators are audited. Repeated receipts are idempotent; one active job per device is enforced by row locking. Queued jobs can be cancelled; in-progress jobs cannot.

Receipt and transaction locations:

```text
Windows: %ProgramData%\GBOS\updates
macOS:   /Library/Application Support/GBOS/updates
```

## Verification

From the backend:

```sh
python -m pytest -q
```

From the agent:

```sh
cargo test --bin agent
cargo check --bin agent
cargo check --bin agent --target x86_64-pc-windows-gnu
```

From the frontend:

```sh
npx tsc --noEmit
npx eslint src/components/devices/AgentUpdates.tsx
npm run build -- --webpack
```

These checks cover protocol behavior, access control, signature rejection, version/platform checks, queue lifecycle, and compilation. They do not replace a Windows and macOS pilot that exercises actual service replacement, blocked downloads, invalid payloads, rollback, and reboot recovery. No live fleet update is performed by these checks.
