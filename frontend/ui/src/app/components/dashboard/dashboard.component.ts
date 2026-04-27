import {
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Subscription, forkJoin, interval, of, switchMap, catchError } from 'rxjs';
import {
  Job,
  JobRequest,
  SchemaInfo,
} from '../../models/job.model';
import { JobService } from '../../services/job.service';

interface SchemaCard {
  schema_name: string;
  table_count: number;
  job_count: number;
  last_job?: Job;
  last_status: string;
  last_duration: string;
  relationships: number;
  pii: number;
  clusters: number;
}

// Default connection metadata for the seeded `test` source DB. The dashboard's
// "Run all" button submits one job per schema using these. The password is
// intentionally empty -- the backend resolves password_secret_ref:
// env://SOURCE_DB_PASSWORD from the uvicorn process environment, so the UI
// never has to ship credentials. If your environment isn't preconfigured,
// submit jobs from the Submit page instead.
const SOURCE_DEFAULTS = {
  host: 'localhost',
  port: 5432,
  database: 'test',
  user: 'adsuser',
  password: '',
};

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, DatePipe, RouterLink],
  template: `
    <div class="header">
      <div>
        <h2>Cross-schema dashboard</h2>
        <p class="muted">
          Source database
          <span class="mono">{{ source().user }}&#64;{{ source().host }}:{{ source().port }}/{{ source().database }}</span>.
          {{ schemas().length }} schema(s) seeded.
        </p>
      </div>
      <div class="actions">
        <button class="primary" (click)="runAll()" [disabled]="running() || schemas().length === 0">
          {{ running() ? 'Submitting...' : 'Run all (' + schemas().length + ')' }}
        </button>
        <a class="link" routerLink="/jobs">View all jobs</a>
        <a class="link" routerLink="/submit">+ Custom run</a>
      </div>
    </div>

    @if (error()) {
      <div class="error">{{ error() }}</div>
    }

    @if (loading()) {
      <p class="muted">Loading...</p>
    }

    <div class="grid" *ngIf="!loading() && cards().length > 0">
      <div class="card schema-card" *ngFor="let c of cards()">
        <div class="card-head">
          <h3 class="mono">{{ c.schema_name }}</h3>
          <span class="status-pill {{ c.last_status }}">{{ c.last_status }}</span>
        </div>
        <div class="metrics">
          <div class="metric">
            <span class="metric-label">Tables</span>
            <span class="metric-value">{{ c.table_count }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Relationships</span>
            <span class="metric-value">{{ c.relationships }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">PII findings</span>
            <span class="metric-value">{{ c.pii }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Clusters</span>
            <span class="metric-value">{{ c.clusters }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Jobs run</span>
            <span class="metric-value">{{ c.job_count }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Last duration</span>
            <span class="metric-value">{{ c.last_duration }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Last run</span>
            <span class="metric-value mono small">
              {{ c.last_job?.submitted_at ? (c.last_job!.submitted_at | date:'short') : '-' }}
            </span>
          </div>
        </div>
        <div class="card-actions">
          <button (click)="runOne(c.schema_name)" [disabled]="running()">Run</button>
          <a *ngIf="c.last_job"
             [routerLink]="['/jobs', c.last_job.job_id]"
             class="link small">Open last job</a>
        </div>
      </div>
    </div>

    <div class="card" *ngIf="!loading() && cards().length === 0">
      <p class="muted">No schemas detected in source DB.</p>
    </div>

    <h3 class="section">Recent runs across all schemas</h3>
    <div class="card" *ngIf="recent().length > 0">
      <table>
        <thead>
          <tr>
            <th>Schema</th>
            <th>Label</th>
            <th>Status</th>
            <th>Submitted</th>
            <th>Duration</th>
            <th>Rels</th>
            <th>PII</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr *ngFor="let j of recent()">
            <td class="mono">{{ j.schema_name }}</td>
            <td>{{ j.label }}</td>
            <td><span class="status-pill {{ j.status }}">{{ j.status }}</span></td>
            <td class="mono small">{{ j.submitted_at | date:'short' }}</td>
            <td class="mono small">{{ duration(j) }}</td>
            <td>{{ j.relationships_count ?? '-' }}</td>
            <td>{{ j.pii_count ?? '-' }}</td>
            <td><a [routerLink]="['/jobs', j.job_id]" class="link small">View</a></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="muted" *ngIf="recent().length === 0">No jobs yet.</p>
  `,
  styles: [`
    .header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 24px;
      margin-bottom: 18px;
    }
    .header h2 { margin: 0 0 4px; }
    .header .actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .primary {
      background: #238636;
      border: 1px solid #2ea043;
      color: white;
      padding: 6px 16px;
      border-radius: 6px;
      cursor: pointer;
    }
    .primary:hover:not(:disabled) { background: #2ea043; }
    .primary:disabled { opacity: 0.6; cursor: not-allowed; }
    .link {
      color: #58a6ff;
      padding: 4px 8px;
    }
    .link.small { font-size: 12px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }
    .schema-card {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-head h3 {
      margin: 0;
      font-size: 16px;
    }
    .metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px 16px;
    }
    .metric {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .metric-label {
      font-size: 11px;
      text-transform: uppercase;
      color: #8b949e;
      letter-spacing: 0.4px;
    }
    .metric-value {
      font-size: 16px;
      font-weight: 600;
      color: #e6edf3;
    }
    .metric-value.small { font-size: 12px; font-weight: 400; }
    .card-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      border-top: 1px solid #30363d;
      padding-top: 10px;
    }
    .card-actions button {
      background: #21262d;
      border: 1px solid #30363d;
      color: #e6edf3;
      padding: 4px 12px;
      border-radius: 4px;
      cursor: pointer;
    }
    .card-actions button:hover:not(:disabled) { background: #30363d; }
    .section {
      margin: 24px 0 12px;
    }
    .error {
      background: #3a0d0d;
      border: 1px solid #f85149;
      color: #ffabab;
      padding: 8px 12px;
      border-radius: 6px;
      margin-bottom: 12px;
    }
    .small { font-size: 12px; }
  `],
})
export class DashboardComponent implements OnInit, OnDestroy {
  private jobsSvc = inject(JobService);

  schemas = signal<SchemaInfo[]>([]);
  jobs = signal<Job[]>([]);
  loading = signal(true);
  running = signal(false);
  error = signal<string | null>(null);
  source = signal({
    host: SOURCE_DEFAULTS.host,
    port: SOURCE_DEFAULTS.port,
    database: SOURCE_DEFAULTS.database,
    user: SOURCE_DEFAULTS.user,
  });

  cards = computed<SchemaCard[]>(() => {
    const byName = new Map<string, Job[]>();
    for (const j of this.jobs()) {
      const arr = byName.get(j.schema_name) ?? [];
      arr.push(j);
      byName.set(j.schema_name, arr);
    }
    return this.schemas().map(s => {
      const all = (byName.get(s.schema_name) ?? []).slice().sort((a, b) =>
        new Date(b.submitted_at).getTime() - new Date(a.submitted_at).getTime(),
      );
      const last = all[0];
      return {
        schema_name: s.schema_name,
        table_count: s.table_count,
        job_count: all.length,
        last_job: last,
        last_status: last?.status ?? 'never run',
        last_duration: last ? this.duration(last) : '-',
        relationships: last?.relationships_count ?? 0,
        pii: last?.pii_count ?? 0,
        clusters: last?.cluster_count ?? 0,
      };
    });
  });

  recent = computed<Job[]>(() =>
    this.jobs().slice(0, 10),
  );

  private sub?: Subscription;

  ngOnInit(): void {
    this.refresh(true);
    // Poll for fresh job state every 4s; refresh schemas only on first load.
    this.sub = interval(4000)
      .pipe(
        switchMap(() => this.jobsSvc.list()),
        catchError(() => of([] as Job[])),
      )
      .subscribe(jobs => this.jobs.set(jobs));
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  refresh(initial = false): void {
    if (initial) this.loading.set(true);
    forkJoin({
      schemas: this.jobsSvc.schemas().pipe(catchError(() => of({
        schemas: [], total: 0,
        source: { host: '', port: 0, database: '', user: '' },
      }))),
      jobs: this.jobsSvc.list().pipe(catchError(() => of([] as Job[]))),
    }).subscribe(({ schemas, jobs }) => {
      this.schemas.set(schemas.schemas);
      if (schemas.source && schemas.source.host) {
        this.source.set(schemas.source);
      }
      this.jobs.set(jobs);
      this.loading.set(false);
    });
  }

  runOne(schema: string): void {
    this.submitOne(schema);
  }

  runAll(): void {
    if (this.schemas().length === 0) return;
    this.running.set(true);
    this.error.set(null);
    let pending = this.schemas().length;
    let failed = 0;
    for (const s of this.schemas()) {
      this.jobsSvc.submit(this.payloadFor(s.schema_name)).subscribe({
        next: () => {
          pending--;
          if (pending === 0) this.finishRun(failed);
        },
        error: err => {
          failed++;
          pending--;
          this.error.set(err?.error?.detail ?? err?.message ?? 'submission failed');
          if (pending === 0) this.finishRun(failed);
        },
      });
    }
  }

  private submitOne(schema: string): void {
    this.running.set(true);
    this.error.set(null);
    this.jobsSvc.submit(this.payloadFor(schema)).subscribe({
      next: () => {
        this.running.set(false);
        this.refresh();
      },
      error: err => {
        this.running.set(false);
        this.error.set(err?.error?.detail ?? err?.message ?? 'submission failed');
      },
    });
  }

  private finishRun(failed: number): void {
    this.running.set(false);
    if (failed === 0) this.refresh();
  }

  private payloadFor(schema: string): JobRequest {
    return {
      label: `${schema} (dashboard)`,
      host: SOURCE_DEFAULTS.host,
      port: SOURCE_DEFAULTS.port,
      database: SOURCE_DEFAULTS.database,
      user: SOURCE_DEFAULTS.user,
      password: SOURCE_DEFAULTS.password,
      schema,
    };
  }

  duration(j: Job): string {
    if (!j.started_at) return '-';
    const start = new Date(j.started_at).getTime();
    const end = j.ended_at ? new Date(j.ended_at).getTime() : Date.now();
    const sec = Math.max(0, Math.round((end - start) / 1000));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
  }
}
