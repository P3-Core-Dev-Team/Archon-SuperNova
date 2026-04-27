# Discovery Pipeline — Web UI

Angular 17 single-page app for submitting discovery jobs, monitoring their
progress in the background, and exploring the results (foreign-key
relationships as an interactive graph, PII findings as a sortable table).

## Layout

```
ui/
├── package.json           # npm + angular cli
├── angular.json
├── tsconfig.json / tsconfig.app.json
├── proxy.conf.json        # /api -> http://127.0.0.1:8090 (the FastAPI backend)
└── src/
    ├── index.html
    ├── main.ts
    ├── styles.scss
    └── app/
        ├── app.config.ts
        ├── app.routes.ts
        ├── app.component.ts
        ├── models/job.model.ts
        ├── services/job.service.ts
        └── components/
            ├── job-submit/job-submit.component.ts        # the input form
            ├── job-list/job-list.component.ts            # status table
            ├── job-detail/job-detail.component.ts        # tabs container
            ├── relationship-graph/relationship-graph.component.ts
            └── pii-table/pii-table.component.ts
```

## How it behaves

1. **Submit** — `/submit` shows a form (label, schema, host, port, db, user, password). Pressing **Run discovery** posts to `POST /api/jobs`, the form clears, and the user is sent to the jobs list with the new job highlighted.
2. **Monitor** — `/` is the jobs list. Polls `GET /api/jobs` every 3s. Status pill shows `queued` → `running` (pulsing) → `succeeded` / `failed`. Live duration counter, relationship + PII counts populate when the job ends.
3. **View** — clicking **View** opens `/jobs/<id>` with three tabs:
   - **Relationships graph** — interactive vis-network rendering. Nodes = tables (sized by edge degree). Edges = FK candidates (color + thickness by confidence). Click an edge for a detail panel. A confidence slider live-filters edges.
   - **PII findings** — sortable table: table, column, PII type, detector, match count, match rate, Bayesian score, validated / name-prior flags, redacted examples.
   - **Run log** — tail of the pipeline log (200 lines, refreshes every 2s while tab is active).

## Run locally

You need:
- Node 18+, npm
- Python 3.11+, FastAPI (`pip install fastapi uvicorn` plus the existing pipeline deps)
- The Spring Boot extraction service running on `:8080` (or the Python mock — see project root)
- A reachable Postgres instance for the source DB
- A reachable Postgres instance for `discovery_results` with the `discovery` schema

### Start the backend (FastAPI, port 8090)

```bash
cd /home/ubuntu/Music/files/discovery
pip install fastapi uvicorn
python3 api/main.py
# → listening on http://127.0.0.1:8090
```

### Start the UI (Angular, port 4200, proxied to :8090)

```bash
cd /home/ubuntu/Music/files/discovery/ui
npm install
npm start
# → http://127.0.0.1:4200
```

## API summary

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/jobs` | Submit a job |
| `GET`  | `/api/jobs` | List all jobs |
| `GET`  | `/api/jobs/{id}` | Job status |
| `GET`  | `/api/jobs/{id}/log?tail=200` | Tail the run log |
| `GET`  | `/api/jobs/{id}/relationships?limit=500` | Graph payload (nodes + edges) |
| `GET`  | `/api/jobs/{id}/pii` | PII findings table |
| `GET`  | `/api/health` | Liveness check |

## Production build

```bash
cd ui
npm run build              # → dist/discovery-ui/
```

Serve the resulting `dist/discovery-ui/browser/` directory behind any static
file server (nginx, Caddy, etc.). Configure the same server (or a sidecar) to
reverse-proxy `/api/*` to the FastAPI backend.

## Notes on the design

- **Standalone components** (Angular 17), no NgModules.
- **Signals** for component state, `ngOnInit` + `interval` for polling.
- **Reactive Forms** with `nonNullable` typed group for the submit form.
- **vis-network** is the smallest CPU-friendly graph lib that handles ≥ 1000
  nodes / 5000 edges interactively. The relationship graph defaults to the
  top 1000 edges by confidence; an operator can adjust via the slider.
- **No global state library** — each component reads via `JobService`. For a
  larger app you'd add NgRx or a signal-based store.
- **Auth deliberately omitted from the UI** — both the FastAPI backend and the
  pipeline already have bearer-token surface. Bolt your reverse proxy auth on
  in front. The form does NOT send credentials over the wire to anywhere
  except the FastAPI backend on the same machine.
