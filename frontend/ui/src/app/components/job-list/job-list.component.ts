import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Subscription, interval } from 'rxjs';
import { Job } from '../../models/job.model';
import { JobService } from '../../services/job.service';

@Component({
  selector: 'app-job-list',
  standalone: true,
  imports: [CommonModule, DatePipe, RouterLink],
  template: `
    <div class="header">
      <h2>Jobs</h2>
      <a class="primary-link" routerLink="/submit">+ New run</a>
    </div>

    <p class="muted" *ngIf="loading() && jobs().length === 0">Loading jobs…</p>

    <div class="error" *ngIf="error()">{{ error() }}</div>

    <p class="muted" *ngIf="!loading() && !error() && jobs().length === 0">
      No jobs yet. <a routerLink="/submit">Submit one</a> to get started.
    </p>

    <div class="card no-pad" *ngIf="jobs().length > 0">
      <table>
        <thead>
          <tr>
            <th>Label</th>
            <th>Schema</th>
            <th>Status</th>
            <th>Submitted</th>
            <th>Duration</th>
            <th>Relationships</th>
            <th>PII</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr *ngFor="let j of jobs()"
              class="row-clickable"
              [class.highlight]="j.job_id === highlight()"
              (click)="goTo(j)">
            <td>{{ j.label }}</td>
            <td class="mono">{{ j.schema_name }}</td>
            <td><span class="status-pill {{ j.status }}">{{ j.status }}</span></td>
            <td class="mono">{{ j.submitted_at | date:'medium' }}</td>
            <td class="mono">{{ duration(j) }}</td>
            <td>{{ j.relationships_count ?? '—' }}</td>
            <td>{{ j.pii_count ?? '—' }}</td>
            <td>
              <a [routerLink]="['/jobs', j.job_id]"
                 (click)="$event.stopPropagation()"
                 *ngIf="j.status === 'succeeded' || j.status === 'failed' || j.status === 'running'">
                View
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
  styles: [`
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }
    h2 { margin: 0; }
    .primary-link {
      background: #238636;
      border: 1px solid #2ea043;
      color: white;
      padding: 6px 14px;
      border-radius: 6px;
    }
    .primary-link:hover { background: #2ea043; text-decoration: none; }
    .card.no-pad { padding: 0; overflow: auto; }
    tr.row-clickable { cursor: pointer; }
    tr.row-clickable:hover td { background: #1c222b; }
    tr.highlight td { background: #1f2937 !important; }
    table th:nth-child(6), table th:nth-child(7),
    table td:nth-child(6), table td:nth-child(7) { text-align: right; }
    .error {
      color: #ffabab;
      padding: 10px 14px;
      background: #3a0d0d;
      border: 1px solid #f85149;
      border-radius: 6px;
      margin-bottom: 12px;
    }
  `],
})
export class JobListComponent implements OnInit, OnDestroy {
  private jobsSvc = inject(JobService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  jobs = signal<Job[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  highlight = signal<string | null>(null);
  private pollSub?: Subscription;

  ngOnInit(): void {
    this.highlight.set(this.route.snapshot.queryParamMap.get('highlight'));
    this.refresh();
    // Tick every 5s; only actually refetch when at least one job is running.
    this.pollSub = interval(5000).subscribe(() => {
      if (this.jobs().some(j => j.status === 'running' || j.status === 'queued')) {
        this.refresh(/*silent*/ true);
      }
    });
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  refresh(silent = false): void {
    if (!silent) this.loading.set(true);
    this.jobsSvc.list().subscribe({
      next: jobs => {
        this.jobs.set(jobs);
        this.loading.set(false);
        this.error.set(null);
      },
      error: err => {
        this.loading.set(false);
        // Don't clobber existing rows on a transient poll failure.
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load jobs.',
        );
      },
    });
  }

  goTo(j: Job): void {
    this.router.navigate(['/jobs', j.job_id]);
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
