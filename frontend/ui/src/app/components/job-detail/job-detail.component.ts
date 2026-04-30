import { Component, OnDestroy, OnInit, computed, effect, inject, signal } from '@angular/core';
import { toObservable } from '@angular/core/rxjs-interop';
import { CommonModule, DatePipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, Subscription, combineLatest, interval, of, switchMap, timer } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { Job } from '../../models/job.model';
import { JobService, RunLogEntry } from '../../services/job.service';
import { RelationshipGraphComponent } from '../relationship-graph/relationship-graph.component';
import { PiiTableComponent } from '../pii-table/pii-table.component';
import { ExportBarComponent } from '../export-bar/export-bar.component';
import { TableCardPageComponent } from '../table-card/table-card-page.component';
import { ClusterOverviewComponent } from '../cluster-overview/cluster-overview.component';

type Tab = 'clusters' | 'relationships' | 'pii' | 'log';

@Component({
  selector: 'app-job-detail',
  standalone: true,
  imports: [
    CommonModule, DatePipe, RouterLink,
    RelationshipGraphComponent, PiiTableComponent,
    ExportBarComponent, TableCardPageComponent,
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
        <button [class.active]="tab() === 'clusters'" (click)="setTab('clusters')">
          Clusters
        </button>
        <button [class.active]="tab() === 'relationships'" (click)="setTab('relationships')">
          Relationships graph
        </button>
        <button [class.active]="tab() === 'pii'" (click)="setTab('pii')">
          PII findings
        </button>
        <button [class.active]="tab() === 'log'" (click)="setTab('log')">
          Run log
        </button>
      </div>

      <div class="tab-body">
        @if (tab() === 'clusters') {
          <app-cluster-overview [jobId]="j.job_id" />
        }
        @if (tab() === 'relationships') {
          <!-- Inline three-state mode toggle: overview (global hairball) /
               map (focal-table 1-hop) / table (per-table detail).  All
               three modes render in this same tab — no navigation away. -->
          <div class="rel-mode-row">
            <div class="rel-mode-toggle" role="tablist" aria-label="Relationships view mode">
              <button type="button" role="tab"
                      [class.active]="relMode() === 'overview'"
                      (click)="setRelMode('overview')">overview</button>
              <span class="sep">|</span>
              <button type="button" role="tab"
                      [class.active]="relMode() === 'map'"
                      [disabled]="!selectedTable()"
                      [title]="selectedTable() ? '' : 'Select a table from the overview first'"
                      (click)="setRelMode('map')">map</button>
              <span class="sep">|</span>
              <button type="button" role="tab"
                      [class.active]="relMode() === 'table'"
                      [disabled]="!selectedTable()"
                      [title]="selectedTable() ? '' : 'Select a table from the overview first'"
                      (click)="setRelMode('table')">table</button>
            </div>
            @if (relMode() !== 'overview') {
              <div class="rel-table-picker">
                <span class="muted small">Selected table:</span>
                <code class="mono">{{ selectedTable() }}</code>
                <button type="button" class="link-btn"
                        (click)="setRelMode('overview')"
                        title="Return to the all-tables overview">
                  back to overview ↺
                </button>
              </div>
            }
          </div>

          @if (relMode() === 'overview') {
            <app-relationship-graph [jobId]="j.job_id" />
          }
          @if (relMode() === 'map' && selectedTable()) {
            <app-table-card-page
              [embedded]="true"
              [jobId]="j.job_id"
              [tableName]="selectedTable()!"
              [view]="'map'"
              (tableSelected)="onRelTableSelected($event)" />
          }
          @if (relMode() === 'table' && selectedTable()) {
            <app-table-card-page
              [embedded]="true"
              [jobId]="j.job_id"
              [tableName]="selectedTable()!"
              [view]="'table'"
              (tableSelected)="onRelTableSelected($event)" />
          }
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
    /* Relationships-tab inline mode toggle (overview | map | table). */
    .rel-mode-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .rel-mode-toggle {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 999px;
    }
    .rel-mode-toggle button {
      background: transparent;
      border: none;
      color: #8b949e;
      padding: 4px 14px;
      border-radius: 999px;
      font-size: 13px;
      letter-spacing: 0;
      text-transform: lowercase;
      cursor: pointer;
    }
    .rel-mode-toggle button.active {
      background: #1f6feb;
      color: #fff;
    }
    .rel-mode-toggle button:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
    .rel-mode-toggle .sep {
      color: #30363d;
      font-size: 12px;
      user-select: none;
    }
    .rel-table-picker {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
    }
    .rel-table-picker .link-btn {
      background: transparent;
      border: 1px solid #30363d;
      color: #58a6ff;
      padding: 3px 12px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
    }
    .rel-table-picker .link-btn:hover { border-color: #58a6ff; }

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
  private router = inject(Router);
  private jobsSvc = inject(JobService);

  job = signal<Job | null>(null);
  loadError = signal<string | null>(null);
  tab = signal<Tab>('clusters');
  logText = signal<string>('');
  runLog = signal<RunLogEntry[]>([]);

  /** Three-state mode for the Relationships tab.  Default 'overview'
   * (global hairball graph); switches to 'map' when a node is clicked
   * or the user picks the toggle; 'table' shows per-table detail.
   * Synced with the URL: ?tab=relationships → overview,
   * ?tab=relationships&table=X → map, &view=table → table. */
  relMode = signal<'overview' | 'map' | 'table'>('overview');
  selectedTable = signal<string | null>(null);

  private sub?: Subscription;
  private logSub?: Subscription;
  private selSub?: Subscription;
  private qpSub?: Subscription;
  // toObservable() requires an injection context (ctor / class field init).
  // Capture both observables here so the subscriptions in ngOnInit can
  // consume them — calling toObservable() inside ngOnInit throws NG0203
  // at runtime and silently kills the dependent feature.
  private tab$ = toObservable(this.tab);
  private selectedTable$ = toObservable(this.jobsSvc.selectedTable);

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

    // Log polling — only fetches while the log tab is open.  Driven by tab
    // changes so switching TO the log tab triggers an immediate fetch (t=0)
    // followed by a 2s poll; switching AWAY cancels both the in-flight
    // request and the pending timer via the outer switchMap.
    // combineLatest waits for both single-emit HttpClient observables to
    // complete before emitting — that's fine; they always complete.
    // catchError on each inner observable returns stale state so a transient
    // network hiccup doesn't wipe the display.
    this.logSub = this.tab$
      .pipe(
        switchMap(t => t === 'log' ? timer(0, 2000) : EMPTY),
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

    // --- Relationships-tab URL sync ---------------------------------
    // Hydrate from ?tab + ?table + ?view, then write back on changes
    // (browser back/forward stays consistent with the on-screen toggle).
    this.qpSub = this.route.queryParamMap.subscribe(qp => {
      const t = qp.get('tab');
      if (t === 'relationships' || t === 'clusters' || t === 'pii' || t === 'log') {
        this.tab.set(t);
      }
      const tbl = qp.get('table');
      const view = qp.get('view');
      this.selectedTable.set(tbl || null);
      if (this.tab() === 'relationships') {
        if (!tbl) {
          this.relMode.set('overview');
        } else {
          this.relMode.set(view === 'table' ? 'table' : 'map');
        }
      }
    });

    // --- Node-click bridge ------------------------------------------
    // The all-tables relationship-graph publishes node clicks via
    // JobService.selectedTable.  Catch them here: set the table and
    // switch the mode to 'map' (per the spec) without leaving the tab.
    this.selSub = this.selectedTable$.subscribe(t => {
      if (this.tab() !== 'relationships') return;
      if (t && t !== this.selectedTable()) {
        this.selectedTable.set(t);
        this.relMode.set('map');
        this.pushQueryParams();
      }
    });
  }

  /** Update the URL to reflect (tab, selectedTable, relMode) without
   * triggering a re-navigation cycle.  Uses replaceUrl so toggle clicks
   * don't pollute browser history. */
  private pushQueryParams(): void {
    const params: Record<string, string | null> = { tab: this.tab() };
    if (this.tab() === 'relationships' && this.relMode() !== 'overview' && this.selectedTable()) {
      params['table'] = this.selectedTable()!;
      params['view'] = this.relMode() === 'table' ? 'table' : 'map';
    } else {
      params['table'] = null;
      params['view'] = null;
    }
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: params,
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Tab-button click handler — keeps the URL in sync. */
  setTab(t: Tab): void {
    if (t === this.tab()) return;
    this.tab.set(t);
    this.pushQueryParams();
  }

  /** Three-state Relationships-mode toggle handler. */
  setRelMode(mode: 'overview' | 'map' | 'table'): void {
    if (mode === this.relMode()) return;
    if (mode !== 'overview' && !this.selectedTable()) return; // disabled
    this.relMode.set(mode);
    if (mode === 'overview') this.selectedTable.set(null);
    this.pushQueryParams();
  }

  /** Embedded TableCardPageComponent emits this when its internal
   * neighbour-card click or search-hit click would normally navigate. */
  onRelTableSelected(ev: { table: string; view: 'table' | 'map' }): void {
    this.selectedTable.set(ev.table);
    this.relMode.set(ev.view);
    this.pushQueryParams();
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
    this.logSub?.unsubscribe();
    this.selSub?.unsubscribe();
    this.qpSub?.unsubscribe();
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
