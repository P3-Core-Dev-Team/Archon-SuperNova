import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { JobService } from '../../services/job.service';
import {
  ColumnInfo,
  Job,
  JobColumns,
  PiiFinding,
  PiiTable,
  RelationshipEdge,
  RelationshipGraph,
} from '../../models/job.model';

interface ColumnRow {
  ordinal: number;
  name: string;
  type: string;
  length: string;
  is_pk: boolean;
  is_fk: boolean;
  pii_types: string[];
}

interface FkRow {
  childTable: string;
  childCol: string;
  parentTable: string;
  parentCol: string;
  confidence: number | null;
  cardinality: string | null;
}

@Component({
  selector: 'app-table-card-page',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <a [routerLink]="['/jobs', jobId]" class="back">← Back to job</a>

    @if (loading()) {
      <p class="muted">Loading…</p>
    }

    @if (error()) {
      <div class="error card">{{ error() }}</div>
    }

    @if (!loading() && !error()) {
      <div class="header">
        <h1 class="mono">{{ tableName }}</h1>
        <div class="badges">
          <span class="badge schema">{{ job()?.schema_name }}</span>
          <span class="badge stat">{{ columns().length }} columns</span>
          <span class="badge stat">{{ outFks().length }} fk-out · {{ inFks().length }} fk-in</span>
          @if (piiCount() > 0) {
            <span class="badge pii">{{ piiCount() }} pii</span>
          }
        </div>
      </div>

      <div class="layout">
        <div class="main">
          <!-- COLUMNS card -->
          <section class="card">
            <h3 class="section-title">Columns</h3>
            <table class="cols">
              <thead>
                <tr>
                  <th class="num">#</th>
                  <th>Field</th>
                  <th>Type</th>
                  <th class="num">Length</th>
                  <th class="center">Key</th>
                  <th class="center">PII</th>
                </tr>
              </thead>
              <tbody>
                @for (c of columns(); track c.name) {
                  <tr [class.col-pk]="c.is_pk" [class.col-fk]="c.is_fk">
                    <td class="num muted">{{ c.ordinal }}</td>
                    <td><code>{{ c.name }}</code></td>
                    <td class="muted small">{{ c.type }}</td>
                    <td class="num muted small">{{ c.length }}</td>
                    <td class="center">
                      @if (c.is_pk) { <span class="kbadge pk">PK</span> }
                      @if (c.is_fk) { <span class="kbadge fk">FK</span> }
                    </td>
                    <td class="center">
                      @for (p of c.pii_types; track p) {
                        <span class="kbadge pii">{{ p }}</span>
                      }
                    </td>
                  </tr>
                }
                @if (columns().length === 0) {
                  <tr><td colspan="6" class="muted center">No columns inventoried.</td></tr>
                }
              </tbody>
            </table>
          </section>

          <!-- PII findings card -->
          @if (piiRows().length > 0) {
            <section class="card">
              <h3 class="section-title">PII findings ({{ piiRows().length }})</h3>
              <table class="pii">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Detector</th>
                    <th class="num">Match rate</th>
                    <th class="num">Score</th>
                  </tr>
                </thead>
                <tbody>
                  @for (p of piiRows(); track p.column_name + p.pii_type + p.detector) {
                    <tr>
                      <td><code>{{ p.column_name }}</code></td>
                      <td><span class="kbadge pii">{{ p.pii_type }}</span></td>
                      <td class="muted small">{{ p.detector }}</td>
                      <td class="num muted small">{{ formatRate(p.match_rate) }}</td>
                      <td class="num">
                        <span [class.score-high]="(p.score ?? 0) >= 0.85"
                              [class.score-mid]="(p.score ?? 0) >= 0.5 && (p.score ?? 0) < 0.85"
                              [class.score-low]="(p.score ?? 0) < 0.5">
                          {{ (p.score ?? 0).toFixed(2) }}
                        </span>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </section>
          }
        </div>

        <!-- Sidebar -->
        <aside class="sidebar">
          <section class="card sticky">
            <h3 class="section-title">Foreign keys — outbound ({{ outFks().length }})</h3>
            @if (outFks().length === 0) {
              <p class="muted small">None.</p>
            } @else {
              <ul class="rel-list">
                @for (f of outFks(); track f.childCol + f.parentTable + f.parentCol) {
                  <li>
                    <code class="mono small">{{ f.childCol }}</code>
                    <span class="arrow">→</span>
                    <a class="parent-link mono small"
                       [routerLink]="['/jobs', jobId, 'tables', f.parentTable]">
                      {{ f.parentTable }}.{{ f.parentCol }}
                    </a>
                    @if (f.confidence !== null) {
                      <span class="conf">{{ f.confidence!.toFixed(2) }}</span>
                    }
                    @if (f.cardinality) {
                      <span class="card-tag">{{ cardLabel(f.cardinality) }}</span>
                    }
                  </li>
                }
              </ul>
            }
          </section>

          <section class="card">
            <h3 class="section-title">Foreign keys — inbound ({{ inFks().length }})</h3>
            @if (inFks().length === 0) {
              <p class="muted small">None.</p>
            } @else {
              <ul class="rel-list">
                @for (f of inFks(); track f.childTable + f.childCol + f.parentCol) {
                  <li>
                    <a class="parent-link mono small"
                       [routerLink]="['/jobs', jobId, 'tables', f.childTable]">
                      {{ f.childTable }}.{{ f.childCol }}
                    </a>
                    <span class="arrow">→</span>
                    <code class="mono small">{{ f.parentCol }}</code>
                    @if (f.confidence !== null) {
                      <span class="conf">{{ f.confidence!.toFixed(2) }}</span>
                    }
                    @if (f.cardinality) {
                      <span class="card-tag">{{ cardLabel(f.cardinality) }}</span>
                    }
                  </li>
                }
              </ul>
            }
          </section>
        </aside>
      </div>
    }
  `,
  styles: [`
    :host { display: block; max-width: 1400px; margin: 0 auto; padding: 0 4px; }
    .back { color: #8b949e; font-size: 13px; }
    .header {
      margin: 16px 0 18px;
      display: flex;
      align-items: baseline;
      gap: 18px;
      flex-wrap: wrap;
    }
    .header h1 {
      margin: 0;
      font-size: 26px;
      font-weight: 500;
      letter-spacing: -0.3px;
      color: #e6edf3;
    }
    .badges {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .badge {
      display: inline-block;
      padding: 2px 10px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.3px;
      text-transform: lowercase;
    }
    .badge.schema { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
    .badge.archetype { background: #161b22; color: #58a6ff; border: 1px solid #1f6feb; }
    .badge.cluster { background: #161b22; color: #d2a8ff; border: 1px solid #30363d; cursor: pointer; }
    .badge.cluster:hover { background: #21262d; text-decoration: none; }
    .badge.stat { background: #161b22; color: #8b949e; border: 1px solid #30363d; }
    .badge.pii { background: #3a0d0d; color: #ffabab; border: 1px solid #f85149; }

    .layout {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 18px;
      align-items: start;
    }
    @media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }

    .main { display: flex; flex-direction: column; gap: 14px; }
    .sidebar { display: flex; flex-direction: column; gap: 14px; }
    .sidebar .sticky { position: sticky; top: 12px; }

    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 18px 20px;
    }
    .section-title {
      margin: 0 0 12px;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
    }

    table.cols, table.pii {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    table.cols th, table.pii th {
      text-align: left;
      padding: 8px 10px;
      border-bottom: 1px solid #30363d;
      font-size: 11px;
      font-weight: 500;
      color: #8b949e;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }
    table.cols td, table.pii td {
      padding: 8px 10px;
      border-bottom: 1px solid #21262d;
      vertical-align: middle;
    }
    table.cols tr:last-child td, table.pii tr:last-child td { border-bottom: none; }
    table.cols tr.col-pk { background: rgba(63, 185, 80, 0.04); }
    table.cols tr.col-fk { background: rgba(31, 111, 235, 0.04); }
    table.cols tr:hover td, table.pii tr:hover td { background: #1c222b; }

    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .center { text-align: center; }
    .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }
    .small { font-size: 12px; }
    .muted { color: #8b949e; }

    .kbadge {
      display: inline-block;
      padding: 1px 7px;
      border-radius: 8px;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.3px;
      margin-left: 4px;
      text-transform: uppercase;
    }
    .kbadge.pk { background: #1f6f3f; color: #aaf0c1; }
    .kbadge.fk { background: #1f4e7e; color: #aedcff; }
    .kbadge.pii { background: #3a0d0d; color: #ffabab; }

    .score-high { color: #3fb950; font-weight: 600; }
    .score-mid  { color: #d29922; }
    .score-low  { color: #8b949e; }

    .rel-list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .rel-list li {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 0;
      border-bottom: 1px solid #21262d;
      flex-wrap: wrap;
    }
    .rel-list li:last-child { border-bottom: none; }
    .arrow { color: #6e7681; }
    .parent-link {
      color: #58a6ff;
      text-decoration: none;
    }
    .parent-link:hover { text-decoration: underline; }
    .conf {
      margin-left: auto;
      font-size: 11px;
      font-variant-numeric: tabular-nums;
      color: #8b949e;
      padding: 1px 6px;
      background: #21262d;
      border-radius: 8px;
    }
    .card-tag {
      font-size: 10px;
      color: #6e7681;
      letter-spacing: 0.3px;
      text-transform: uppercase;
    }

    .error { color: #ffabab; background: #3a0d0d; border-color: #f85149; }
  `],
})
export class TableCardPageComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private jobsSvc = inject(JobService);

  jobId = '';
  tableName = '';

  loading = signal(true);
  error = signal<string | null>(null);

  job = signal<Job | null>(null);
  private allColumns = signal<ColumnInfo[]>([]);
  private allEdges = signal<RelationshipEdge[]>([]);
  private allPii = signal<PiiFinding[]>([]);

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    const tbl = this.route.snapshot.paramMap.get('table_name');
    if (!id || !tbl) {
      this.error.set('Missing job id or table name in URL.');
      this.loading.set(false);
      return;
    }
    this.jobId = id;
    this.tableName = tbl;

    forkJoin({
      job: this.jobsSvc.get(id),
      cols: this.jobsSvc.columns(id),
      rels: this.jobsSvc.relationships(id, 5000),
      pii: this.jobsSvc.pii(id),
    }).subscribe({
      next: (r: { job: Job; cols: JobColumns; rels: RelationshipGraph; pii: PiiTable }) => {
        this.job.set(r.job);
        this.allColumns.set(r.cols.columns ?? []);
        this.allEdges.set(r.rels.edges ?? []);
        this.allPii.set(r.pii.findings ?? []);
        this.loading.set(false);
      },
      error: err => {
        this.error.set(err?.error?.detail ?? err?.message ?? 'Failed to load table.');
        this.loading.set(false);
      },
    });
  }

  // Columns owned by this table.
  columns = computed<ColumnRow[]>(() => {
    const piiByCol = new Map<string, string[]>();
    for (const f of this.allPii()) {
      if (f.table_name === this.tableName) {
        const arr = piiByCol.get(f.column_name) ?? [];
        if (!arr.includes(f.pii_type)) arr.push(f.pii_type);
        piiByCol.set(f.column_name, arr);
      }
    }
    return this.allColumns()
      .filter(c => c.table === this.tableName)
      .sort((a, b) => a.ordinal - b.ordinal)
      .map(c => ({
        ordinal: c.ordinal,
        name: c.column,
        type: c.data_type,
        length: this.lengthFromType(c.data_type),
        is_pk: c.is_pk,
        is_fk: c.is_fk,
        pii_types: piiByCol.get(c.column) ?? [],
      }));
  });

  outFks = computed<FkRow[]>(() =>
    this.allEdges()
      .filter(e => e.from === this.tableName)
      .map(e => this.parseEdge(e))
  );

  inFks = computed<FkRow[]>(() =>
    this.allEdges()
      .filter(e => e.to === this.tableName)
      .map(e => this.parseEdge(e))
  );

  piiRows = computed<PiiFinding[]>(() =>
    this.allPii()
      .filter(p => p.table_name === this.tableName)
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
  );

  piiCount = computed(() => this.piiRows().length);

  // Helpers --------------------------------------------------------------

  // edge.label is "child_col → parent_col" produced by the API.
  private parseEdge(e: RelationshipEdge): FkRow {
    const arrow = ' → ';
    let childCol = '';
    let parentCol = '';
    if (e.label && e.label.includes(arrow)) {
      const [a, b] = e.label.split(arrow);
      childCol = (a ?? '').trim();
      parentCol = (b ?? '').trim();
    }
    return {
      childTable: e.from,
      childCol,
      parentTable: e.to,
      parentCol,
      confidence: e.confidence ?? null,
      cardinality: e.cardinality ?? null,
    };
  }

  // Crude length extraction from type strings like "varchar(255)" / "char(8)".
  private lengthFromType(t: string): string {
    const m = /\(([^)]+)\)/.exec(t || '');
    return m ? m[1] : '—';
  }

  formatRate(rate: number | null | undefined): string {
    if (rate === null || rate === undefined) return '—';
    return `${(rate * 100).toFixed(0)}%`;
  }

  cardLabel(c: string): string {
    return c
      .toLowerCase()
      .replace('one_to_one', '1:1')
      .replace('many_to_one', 'N:1')
      .replace('one_to_many', '1:N')
      .replace('many_to_many', 'N:N')
      .replace('partial', '~');
  }
}
