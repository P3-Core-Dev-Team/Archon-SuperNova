import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subscription, interval, switchMap } from 'rxjs';
import { Job } from '../../models/job.model';
import { JobService } from '../../services/job.service';
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
          <pre class="log card">{{ logText() }}</pre>
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
  `],
})
export class JobDetailComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private jobsSvc = inject(JobService);

  job = signal<Job | null>(null);
  loadError = signal<string | null>(null);
  tab = signal<Tab>('clusters');
  logText = signal<string>('');
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

    // Log polling — only fetches when the log tab is open.
    this.logSub = interval(2000).subscribe(() => {
      if (this.tab() === 'log') {
        this.jobsSvc.log(id, 200).subscribe({
          next: r => this.logText.set(r.log),
          error: () => {},
        });
      }
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
}
