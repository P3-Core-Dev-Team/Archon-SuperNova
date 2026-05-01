import { Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { JobService } from '../../services/job.service';
import { DataQualityFinding } from '../../models/job.model';

/**
 * "Data quality" tab on the job-detail page (Sprint 5b).
 *
 * Renders findings produced by the data_quality pipeline phase.  Each
 * row is one (table, column, issue_type) tuple with severity-coloured
 * chip, count + fraction, and (where available) up to 3 redacted
 * sample values for whitespace / mixed-case issues.
 *
 * Sort defaults to severity DESC then fraction DESC; the user can
 * filter by severity via the toggle row at the top.
 */
@Component({
  selector: 'app-data-quality',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="dq-toolbar">
      <input type="search"
             placeholder="Filter table / column / issue…"
             [ngModel]="filter()"
             (ngModelChange)="filter.set($event)"
             class="filter" />
      <span class="muted small">filter</span>
      <span class="spacer"></span>
      <span class="muted">{{ filtered().length }} of {{ total() }} findings</span>
      <div class="sev-toggle">
        @for (s of allSeverities; track s) {
          <button type="button"
                  class="sev-pill"
                  [class]="'sev-' + s.toLowerCase()"
                  [class.active]="severityFilter().has(s)"
                  (click)="toggleSeverity(s)">
            {{ s }}
            <span class="sev-count">{{ countBySeverity()[s] || 0 }}</span>
          </button>
        }
      </div>
    </div>

    @if (!loading() && !error() && total() === 0) {
      <div class="card muted center pad">
        No data-quality findings — every column passed the profiler's
        default thresholds.  Either the data is clean or the
        data_quality phase didn't run for this job.
      </div>
    }

    @if (total() > 0) {
      <div class="card no-pad">
        <table>
          <thead>
            <tr>
              <th>Severity</th>
              <th>Table</th>
              <th>Column</th>
              <th>Issue</th>
              <th class="r">Count</th>
              <th class="r">Fraction</th>
              <th>Examples</th>
            </tr>
          </thead>
          <tbody>
            @for (f of filtered(); track $index) {
              <tr [class]="'row-sev-' + f.severity.toLowerCase()">
                <td>
                  <span class="sev-chip" [class]="'sev-' + f.severity.toLowerCase()"
                        [title]="f.severity + ' severity'">{{ f.severity }}</span>
                </td>
                <td class="mono">
                  <a class="tlink"
                     [routerLink]="['/jobs', jobId(), 'tables', f.table_name]"
                     title="Open the queryviz-style page for this table">{{ f.table_name }}</a>
                </td>
                <td class="mono">{{ f.column_name }}</td>
                <td>
                  <span class="issue" [title]="issueDescription(f.issue_type)">
                    {{ issueLabel(f.issue_type) }}
                  </span>
                </td>
                <td class="r mono">{{ f.count | number }}</td>
                <td class="r mono">{{ (f.fraction * 100) | number:'1.1-1' }}%</td>
                <td class="mono small ex" [title]="exampleTooltip(f)">
                  {{ exampleText(f) }}
                </td>
              </tr>
            }
            @if (filtered().length === 0) {
              <tr><td colspan="7" class="muted center">No findings match the filter.</td></tr>
            }
          </tbody>
        </table>
      </div>
    }

    @if (loading()) { <div class="muted">Loading…</div> }
    @if (error()) { <div class="error">{{ error() }}</div> }
  `,
  styles: [`
    .dq-toolbar {
      display: flex;
      gap: 14px;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .dq-toolbar .filter { min-width: 280px; }
    .dq-toolbar .spacer { flex: 1; }
    .dq-toolbar .muted { color: #8b949e; font-size: 12px; }
    .sev-toggle { display: inline-flex; gap: 6px; }
    .sev-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: #161b22;
      border: 1px solid #30363d;
      color: #8b949e;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 11px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      cursor: pointer;
      font-family: inherit;
    }
    .sev-pill .sev-count {
      font-weight: 700;
      font-size: 10px;
      opacity: 0.85;
    }
    .sev-pill.sev-high.active   { background: rgba(248, 81, 73, 0.18);  border-color: #f85149; color: #ffabab; }
    .sev-pill.sev-medium.active { background: rgba(210, 153, 34, 0.18); border-color: #d29922; color: #e3b341; }
    .sev-pill.sev-low.active    { background: rgba(139, 148, 158, 0.18); border-color: #8b949e; color: #c9d1d9; }
    .sev-pill:hover { color: #e6edf3; }

    .card.no-pad { padding: 0; overflow: auto; max-height: 720px; }
    .card.pad { padding: 24px; text-align: center; }
    .card { background: #0d1117; border: 1px solid #21262d; border-radius: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    thead th {
      text-align: left;
      padding: 7px 10px;
      font-size: 11px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #8b949e;
      border-bottom: 1px solid #30363d;
      background: #161b22;
      font-weight: 600;
      position: sticky;
      top: 0;
    }
    th.r, td.r { text-align: right; }
    tbody td {
      padding: 6px 10px;
      border-bottom: 1px solid #1a1f26;
    }
    tbody tr:hover td { background: #161b22; }
    /* Severity row tint — subtle left-border accent */
    tr.row-sev-high   td:first-child { box-shadow: inset 3px 0 0 #f85149; }
    tr.row-sev-medium td:first-child { box-shadow: inset 3px 0 0 #d29922; }
    tr.row-sev-low    td:first-child { box-shadow: inset 3px 0 0 #6e7681; }

    .sev-chip {
      display: inline-block;
      padding: 1px 7px;
      border-radius: 8px;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.5px;
      cursor: help;
      border: 1px solid currentColor;
    }
    .sev-chip.sev-high   { color: #ff7b8b; background: rgba(248, 81, 73, 0.16); }
    .sev-chip.sev-medium { color: #e3b341; background: rgba(210, 153, 34, 0.16); }
    .sev-chip.sev-low    { color: #8b949e; background: rgba(139, 148, 158, 0.10); }

    .issue {
      font-size: 12px;
      color: #c9d1d9;
      cursor: help;
    }
    td.ex {
      max-width: 320px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      cursor: help;
    }
    .tlink { color: #58a6ff; text-decoration: none; }
    .tlink:hover { text-decoration: underline; }
    .center { text-align: center; padding: 24px 0 !important; }
    .small { font-size: 12px; color: #8b949e; }
    .muted { color: #8b949e; }
    .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }
    .error {
      color: #ffabab;
      padding: 12px;
      background: #3a0d0d;
      border: 1px solid #f85149;
      border-radius: 6px;
    }
  `],
})
export class DataQualityComponent implements OnInit {
  jobId = input.required<string>();

  private jobsSvc = inject(JobService);

  findings = signal<DataQualityFinding[]>([]);
  total = signal(0);
  loading = signal(true);
  error = signal<string | null>(null);

  filter = signal('');
  /** Active severity filter — empty Set means "show all". */
  severityFilter = signal<Set<'HIGH' | 'MEDIUM' | 'LOW'>>(new Set());

  readonly allSeverities: ('HIGH' | 'MEDIUM' | 'LOW')[] = ['HIGH', 'MEDIUM', 'LOW'];

  /** Issue-type → friendly label mapping.  Keep in sync with
   * discovery.data_quality.IssueType. */
  private static readonly _LABEL: Record<string, string> = {
    NULL_HEAVY: 'Null-heavy',
    ALL_NULL: 'All NULL',
    DUPLICATE_PK: 'Duplicate PK',
    LEADING_TRAILING_WHITESPACE: 'Whitespace',
    EMPTY_STRING: 'Empty string',
    MIXED_CASE: 'Mixed case',
    LOW_CARDINALITY: 'Low cardinality',
  };

  private static readonly _DESCRIPTION: Record<string, string> = {
    NULL_HEAVY: 'Column has more than 50% NULL values — consider dropping or backfilling.',
    ALL_NULL: 'Every value in the sample is NULL.  Column may be deprecated.',
    DUPLICATE_PK: 'Putative primary key has duplicate values; PK constraint will fail to apply.',
    LEADING_TRAILING_WHITESPACE: 'Some values have leading or trailing whitespace.  Causes silent join misses.',
    EMPTY_STRING: 'Column has empty-string ("") values mixed with proper NULLs.  Consider normalising.',
    MIXED_CASE: 'The same logical value appears in different cases (e.g. "USA" + "usa").',
    LOW_CARDINALITY: 'Fewer than 5 distinct values across 1000+ rows — likely a status / type column.',
  };

  filtered = computed(() => {
    const q = this.filter().trim().toLowerCase();
    const sevs = this.severityFilter();
    let rows = this.findings();
    if (sevs.size > 0) rows = rows.filter(f => sevs.has(f.severity));
    if (q) {
      rows = rows.filter(f =>
        f.table_name.toLowerCase().includes(q) ||
        f.column_name.toLowerCase().includes(q) ||
        f.issue_type.toLowerCase().includes(q),
      );
    }
    return rows;
  });

  countBySeverity = computed<Record<string, number>>(() => {
    const out: Record<string, number> = { HIGH: 0, MEDIUM: 0, LOW: 0 };
    for (const f of this.findings()) {
      out[f.severity] = (out[f.severity] || 0) + 1;
    }
    return out;
  });

  ngOnInit(): void {
    this.jobsSvc.dataQuality(this.jobId()).subscribe({
      next: r => {
        this.findings.set(r.findings);
        this.total.set(r.total);
        this.loading.set(false);
      },
      error: err => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load data-quality findings.',
        );
      },
    });
  }

  toggleSeverity(s: 'HIGH' | 'MEDIUM' | 'LOW'): void {
    const cur = new Set(this.severityFilter());
    if (cur.has(s)) cur.delete(s);
    else cur.add(s);
    this.severityFilter.set(cur);
  }

  issueLabel(t: string): string {
    return DataQualityComponent._LABEL[t] ?? t;
  }
  issueDescription(t: string): string {
    return DataQualityComponent._DESCRIPTION[t] ?? '';
  }

  exampleText(f: DataQualityFinding): string {
    if (!Array.isArray(f.samples) || f.samples.length === 0) return '—';
    return f.samples.slice(0, 3).join(', ');
  }
  exampleTooltip(f: DataQualityFinding): string {
    if (!Array.isArray(f.samples) || f.samples.length === 0) return '';
    return f.samples.join('\n');
  }
}
