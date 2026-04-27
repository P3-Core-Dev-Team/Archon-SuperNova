import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subscription, combineLatest, interval, of, switchMap } from 'rxjs';
import { catchError, filter } from 'rxjs/operators';
import { Job } from '../../models/job.model';
import { JobService, RunLogEntry } from '../../services/job.service';
import { RelationshipGraphComponent } from '../relationship-graph/relationship-graph.component';
import { PiiTableComponent } from '../pii-table/pii-table.component';
import { ExportBarComponent } from '../export-bar/export-bar.component';
import { TableDetailComponent } from '../table-detail/table-detail.component';
import { ClusterOverviewComponent } from '../cluster-overview/cluster-overview.component';

type Tab = 'clusters' | 'relationships' | 'pii' | 'log';

@Component({
  selector: 'app-job-detail',
  standalone: true,
  imports: [
    CommonModule, DatePipe, RouterLink,
    RelationshipGraphComponent, PiiTableComponent,
    ExportBarComponent, TableDetailComponent,
    ClusterOverviewComponent,
  ],
  template: `
    <a routerLink="/jobs" class="back">← Back to jobs</a>

    @if (loadError()) {
      <div class="error card">{{ loadError() }}</div>
    }

    @if (!job() && !loadError()) {
      <p class="muted">Loading job…</p>
    }

    <ng-container *ngIf="job() as j">
      <div class="header">
        <h2>{{ j.label }}</h2>
        <span class="status-pill {{ j.status }}">{{ j.status }}</span>
        <span class="spacer"></span>
        <app-export-bar [jobId]="j.job_id" />
      </div>
      <div class="meta">
        <span>Schema: <code>{{ j.schema_name }}</code></span>
        <span>Job ID: <code>{{ j.job_id }}</code></span>
        <span>Submitted: {{ j.submitted_at | date:'medium' }}</span>
        @if (j.ended_at) {
          <span>Ended: {{ j.ended_at | date:'medium' }}</span>
        }
        <span>Duration: <strong>{{ duration(j) }}</strong></span>
        @if (j.relationships_count !== null && j.relationships_count !== undefined) {
          <span>Relationships: <strong>{{ j.relationships_count }}</strong></span>
        }
        @if (j.pii_count !== null && j.pii_count !== undefined) {
          <span>PII findings: <strong>{{ j.pii_count }}</strong></span>
        }
        <!-- B2: link to the dbdiagram-style ERD card view -->
        <a [routerLink]="['/jobs', j.job_id, 'erd']" class="erd-link">[ERD card view]</a>
      </div>

      @if (j.error) {
        <div class="error card">
          <strong>Error:</strong> {{ j.error }}
        </div>
      }

      <div class="tabs">
        <button [class.active]="tab() === 'clusters'" (click)="tab.set('clusters')">
          Clusters
        </button>
        <button [class.active]="tab() === 'relationships'" (click)="tab.set('relationships')">
          Relationships graph
        </button>
        <button [class.active]="tab() === 'pii'" (click)="tab.set('pii')">
          PII findings
        </button>
        <button [class.active]="tab() === 'log'" (click)="tab.set('log')">
          Run log
        </button>
      </div>

      <div class="tab-body">
        @if (tab() === 'clusters') {
          <app-cluster-overview [jobId]="j.job_id" />
        }
        @if (tab() === 'relationships') {
          <app-relationship-graph [jobId]="j.job_id" />
          <!-- Detail panel sits BELOW the graph and renders when a node is clicked.
               relationship-graph publishes the selection on JobService.selectedTable. -->
          <app-table-detail [jobId]="j.job_id" />
        }
        @if (tab() === 'pii') {
          <app-pii-table [jobId]="j.job_id" />
        }
        @if (tab() === 'log') {
          <div class="card runlog-card">
            <h3 class="runlog-title">Phase status</h3>
            @if (runLog().length === 0) {
              <p class="muted">No phase entries yet.</p>
            } @else {
              <table class="runlog-table">
                <thead>
                  <tr>
                    <th>Phase</th>
                    <th>Scope</th>
                    <th>Status</th>
                    <th>Sub-tasks</th>
                    <th>Started</th>
                    <th>Duration</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  @for (e of runLog(); track $index) {
                    <tr [class.row-failed]="e.status === 'failed'">
                      <td><code>{{ e.phase }}</code></td>
                      <td>{{ e.scope_type }}{{ e.scope_id ? ('/' + e.scope_id) : '' }}</td>
                      <td><span class="pill {{ e.status }}">{{ e.status }}</span></td>
                      <td>{{ subTasks(e) }}</td>
                      <td>{{ e.started_at | date:'HH:mm:ss.SSS' }}</td>
                      <td>{{ phaseDuration(e) }}</td>
                      <td class="err">{{ e.error_message }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          </div>
          <pre class="log card">{{ logText() || '(no log output yet)' }}</pre>
        }
      </div>
    </ng-container>
  `,
  styles: [`
    .back { color: #8b949e; font-size: 13px; }
    .header {
      display: flex;
      align-items: center;
      gap: 16px;
      margin: 12px 0 6px;
    }
    .header h2 { margin: 0; }
    .header .spacer { flex: 1; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      color: #8b949e;
      font-size: 13px;
      margin-bottom: 18px;
    }
    .meta strong { color: #e6edf3; }
    .meta .erd-link {
      color: #58a6ff;
      text-decoration: none;
      font-size: 12px;
    }
    .meta .erd-link:hover { text-decoration: underline; }
    .meta code {
      background: #21262d;
      padding: 1px 6px;
      border-radius: 4px;
      color: #e6edf3;
    }
    .tabs {
      display: flex;
      gap: 4px;
      border-bottom: 1px solid #30363d;
      margin-bottom: 18px;
    }
    .tabs button {
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0;
      color: #8b949e;
      padding: 10px 16px;
    }
    .tabs button.active {
      color: #e6edf3;
      border-bottom-color: #58a6ff;
    }
    .error { color: #ffabab; background: #3a0d0d; border-color: #f85149; }
    .log {
      max-height: 540px;
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
      background: #0d1117;
    }
    .runlog-card { margin-bottom: 16px; }
    .runlog-title { margin: 0 0 12px 0; font-size: 14px; color: #c9d1d9; }
    .runlog-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
    }
    .runlog-table th, .runlog-table td {
      padding: 6px 10px;
      border-bottom: 1px solid #30363d;
      text-align: left;
      vertical-align: top;
    }
    .runlog-table th { color: #8b949e; font-weight: 500; }
    .runlog-table tr.row-failed { background: #2a0b0b; }
    .runlog-table .err { color: #ffabab; max-width: 360px; word-break: break-word; }
    .pill {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 10px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }
    .pill.succeeded { background: #1f6f3f; color: #a3f0c2; }
    .pill.running   { background: #1f4e7e; color: #aedcff; }
    .pill.failed    { background: #6f1f1f; color: #ffb3b3; }
    .pill.skipped   { background: #3a3a3a; color: #c9c9c9; }
  `],
})
export class JobDetailComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private jobsSvc = inject(JobService);

  job = signal<Job | null>(null);
  loadError = signal<string | null>(null);
  tab = signal<Tab>('clusters');
  logText = signal<string>('');
  runLog = signal<RunLogEntry[]>([]);
  private sub?: Subscription;
  private logSub?: Subscription;

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (!id) {
      this.loadError.set('No job id in URL.');
      return;
    }

    this.jobsSvc.get(id).subscribe({
      next: j => this.job.set(j),
      error: err => this.loadError.set(
        err?.error?.detail ?? err?.message ?? 'Failed to load job.',
      ),
    });

    // Poll every 3s while not terminal
    this.sub = interval(3000)
      .pipe(switchMap(() => this.jobsSvc.get(id)))
      .subscribe({
        next: j => {
          this.job.set(j);
          if (j.status === 'succeeded' || j.status === 'failed') {
            this.sub?.unsubscribe();
            // Once the job is terminal there's no reason to keep polling logs.
            this.logSub?.unsubscribe();
          }
        },
        // Swallow transient poll errors — the user already has prior data.
        error: () => {},
      });

    // Log polling — only fetches when the log tab is open.  switchMap
    // cancels in-flight HTTP requests when the next tick fires, preventing
    // out-of-order responses on slow networks from clobbering newer state.
    // catchError keeps the outer stream alive if a single tick fails.
    this.logSub = interval(2000)
      .pipe(
        filter(() => this.tab() === 'log'),
        switchMap(() => combineLatest([
          this.jobsSvc.log(id, 200).pipe(
            catchError(() => of({ log: this.logText() })),
          ),
          this.jobsSvc.runLog(id).pipe(
            catchError(() => of({ entries: this.runLog() })),
          ),
        ])),
      )
      .subscribe(([logResp, runLogResp]) => {
        this.logText.set(logResp.log);
        this.runLog.set(runLogResp.entries);
      });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
    this.logSub?.unsubscribe();
  }

  duration(j: Job): string {
    if (!j.started_at) return '—';
    const start = new Date(j.started_at).getTime();
    const end = j.ended_at ? new Date(j.ended_at).getTime() : Date.now();
    const sec = Math.max(0, Math.round((end - start) / 1000));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
  }

  subTasks(e: RunLogEntry): string {
    if (e.sub_total === undefined) return '—';
    if (e.sub_failed) return `${e.sub_total} (${e.sub_failed} failed)`;
    return `${e.sub_total}`;
  }

  phaseDuration(e: RunLogEntry): string {
    if (!e.started_at) return '—';
    const start = new Date(e.started_at).getTime();
    const end = e.ended_at ? new Date(e.ended_at).getTime() : Date.now();
    const ms = Math.max(0, end - start);
    if (ms < 1000) return `${ms}ms`;
    const sec = ms / 1000;
    return sec < 60 ? `${sec.toFixed(1)}s` : `${Math.floor(sec/60)}m ${Math.round(sec%60)}s`;
  }
}
