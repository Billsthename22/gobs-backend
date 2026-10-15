# GBOS backend

The FastAPI backend for the GBOS device management platform. It handles authentication, devices, employees, activity, alerts, analytics, and remote monitoring. PostgreSQL stores the data; WebSockets connect devices and monitoring clients.

## What was deployed

On October 15, 2026, a new Render web service named `gbos-backend` was created from this repository:

- Repository: https://github.com/Billsthename22/gobs-backend
- Branch: `main`
- Live API: https://gbos-backend.onrender.com
- Interactive API documentation: https://gbos-backend.onrender.com/docs
- Render dashboard: https://dashboard.render.com/web/srv-db8679avcj2c738ce6qg

Render runs the backend on its **free plan**, in Oregon, using Python `3.11.11`. It installs dependencies with:

```sh
pip install -r requirements.txt
```

It starts the application with:

```sh
uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
```

Render supplies `PORT`. One worker is intentional because active device connections and power command state are held in process memory.

The deployment reused the hosted PostgreSQL database and application settings from the local `.env`. No new database was created. A Render API key was used to create the service; it was not added to the deployed application's environment.

The deployed commit was `d60f0f84a2f58455804c5b3303816fef13e2c058` (`Add Render deployment configuration`). The build succeeded, Render reported the deployment as live, and both `/health` and `/health/database` returned HTTP 200 with `status: ok`. The local test suite also passed all 8 tests.

## Configuration

Production variables are managed under **Environment** in the Render dashboard. Editing your local `.env` does not update Render automatically.

| Variable | Purpose |
| --- | --- |
| `ADMIN_EMAILS` | Comma-separated bootstrap administrator emails. This installation uses `admin@example.com`. |
| `DATABASE_URL` | PostgreSQL connection string. The deployment uses the existing hosted database. |
| `JWT_SECRET_KEY` | Signs login tokens. Keep it private; changing it invalidates existing tokens. |
| `JWT_ALGORITHM` | Token algorithm; defaults to `HS256`. |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime; defaults to `60`. |
| `FRONTEND_URL` | Comma-separated frontend origins allowed by CORS. |
| `POWER_ADMIN_EMAILS` | Exact, case-sensitive account emails allowed to issue power commands. Defaults to empty, which grants nobody access. See [POWER-CONTROL.md](POWER-CONTROL.md). |
| `PYTHON_VERSION` | Set to `3.11.11` on Render for this deployment. |
| `RENDER_API_KEY` | Local deployment credential only; the backend does not need it to run. |

Only application variables present in the local `.env` were copied, along with `PYTHON_VERSION`. Settings omitted from Render use the application's defaults.

The local `.env` is ignored by Git. Keep credentials out of commits and documentation.

### About render.yaml

The repository already contains [render.yaml](render.yaml). The live service was created directly through Render's API, rather than through a Blueprint, so this file is not currently the source of its settings.

The file now reflects the free plan and pinned Python version. Administrator emails, power administrator emails, and secrets must be supplied separately. Editing it alone does not reconfigure this service.

## Connecting the frontend and agents

The updated frontend and agent source default to Render. Frontend requests now share a single API helper that sends the signed-in user's bearer token. Frontend deployment settings can still override the default. Installed agents need updated binaries and per-device credentials; changing source code does not update an installed service.

To use this Render backend, set the frontend's build environment variable and redeploy the frontend:

```dotenv
NEXT_PUBLIC_API_URL=https://gbos-backend.onrender.com
```

Set the following in each agent's actual runtime/service environment, then restart the agent:

```dotenv
GBOS_API_URL=https://gbos-backend.onrender.com
GBOS_DEVICE_TOKEN=replace-with-this-devices-credential
```

For installed services, configure the service environment; an interactive shell variable may not reach the service. Both clients derive the WebSocket URL from the API URL, using `wss://` for HTTPS.

Ensure `FRONTEND_URL` on Render includes the frontend's exact origin (for example, `https://your-app.vercel.app`), then apply the environment change and redeploy the backend.

## Workspace access and audit history

The Windows 0.1.2 agent also sends active-application executable names and active/idle transitions. These appear in Activity; select **Application** or **Presence** to filter them. Analytics counts application activity events, not exact usage duration. Idle is five minutes without input. Onboarding supplies employee notice, with collection details available through the laptop's GBOS tray icon. Live screen viewing has its own visible indicator.

This installation is one company workspace. All members share its devices; it does not yet provide isolated organizations or per-device user assignments.

- **Viewer:** read dashboards, devices, employees, activity, and alerts. Cannot change records, watch screen streams, or issue commands.
- **Operator:** viewer access plus device/employee management, GPS toggles, and remote monitoring.
- **Administrator:** operator access plus invitations, role changes, revocation, audit history, and agent credential issuance. Power commands also require the exact email to appear in `POWER_ADMIN_EMAILS`.

`admin@example.com` is the bootstrap administrator through `ADMIN_EMAILS`. Its existing password is unchanged. Bootstrap administrators cannot be removed through the UI; change the environment variable to remove one. Existing accounts outside `ADMIN_EMAILS` need an access grant before signing in or using an existing token.

Administrators use **Settings → Workspace access and audit history** to invite an email as viewer, operator, or administrator. Existing accounts gain that role immediately and retain their passwords. New users receive a one-time invitation code that the administrator shares privately; they enter it at `/join`. Codes expire after seven days. Tokens are hashed in PostgreSQL and are not written to audit logs. Public registration without an invitation is denied.

Revoking membership blocks subsequent API calls even when the old login token has not expired. Role changes and revocations close monitoring viewer connections, which must authenticate again. Monitoring sockets also recheck membership and token expiry at least every 15 seconds. Stopping a monitoring session closes its viewer sockets.

Administrators pre-enroll devices through Settings and issue individual credentials. Registration requires the matching hostname and credential; `/ws/devices/{id}` requires that device's credential in an Authorization header. See the adjacent agent README for installation and credential file paths. Credentials are shown once and can be rotated.

Three additive tables are created at startup: `access_grants`, `device_credentials`, and `audit_logs`. Existing user and device tables are preserved. Write requests record an audit attempt before the handler runs, then their actor, HTTP result, action, and resource. If audit storage is unavailable, the request cannot proceed. An interrupted request can remain `attempted`; investigate before retrying a destructive action. Power requests and correlated agent reports also record command IDs and results. Audit rows have no device/user foreign keys, so deleting those records does not delete the audit trail. Passwords, tokens, and request bodies are excluded.

Administrators can read the latest 100 events in Settings, or query `GET /access/audit-logs?limit=100&before_id=123` to page backward. Agent reports describe the native request's result, not independent proof of a physical power change.

## Updating the backend

Automatic deployment is enabled for `main`. Commit and push backend changes to this repository's `main` branch; Render will build and deploy them automatically.

Watch **Events** and **Logs** in the Render dashboard. To deploy without a new commit, use **Manual Deploy → Deploy latest commit**. After a deployment, check:

```sh
curl --fail https://gbos-backend.onrender.com/health
curl --fail https://gbos-backend.onrender.com/health/database
```

The first checks the API. The second runs a database query and also returns the database name. Successful responses include `"status":"ok"`.

## Running locally

From the `backend` directory, use Python 3.11 and create an isolated environment:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

If `.env` does not already exist, copy `.env.example` to `.env` and fill in the database URL and JWT secret. Use a development database for local work: the application connects to the configured database and creates missing tables when it starts.

Start the development server:

```sh
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs to explore the routes. Run the automated tests with:

```sh
python -m pytest -q
```

## Operational notes

- Free Render services can sleep when idle, so the first request can take longer. See [Render's free service documentation](https://render.com/docs/free).
- Device connections and pending power command state live in memory. A restart or deployment drops connections and clears that state; agents must reconnect.
- Startup uses SQLAlchemy `create_all` to create missing tables. It does not migrate existing columns; schema changes need a separate migration plan.
- For build failures, check Render's build logs. For startup or database errors, check runtime logs and the production environment variables. For browser CORS errors, check `FRONTEND_URL` and the frontend API setting.
