import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
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

type RelType = 'header_item' | 'master_lookup' | 'config' | 'text' | 'history';

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
      <!-- Page header: table name + description on the left, table | map
           toggle on the right.  Shared between both modes per the spec. -->
      <div class="page-header">
        <div class="title-row">
          <h1 class="mono">{{ tableName }}</h1>
          <span class="title-desc muted">{{ headerDescription() }}</span>
        </div>
        <div class="view-toggle" role="tablist" aria-label="Switch view">
          <button type="button"
                  role="tab"
                  [class.active]="view() === 'table'"
                  [attr.aria-selected]="view() === 'table'"
                  (click)="setView('table')">table</button>
          <span class="sep">|</span>
          <button type="button"
                  role="tab"
                  [class.active]="view() === 'map'"
                  [attr.aria-selected]="view() === 'map'"
                  (click)="setView('map')">map</button>
        </div>
      </div>

      <div class="header">
        <div class="badges">
          <span class="badge schema">{{ job()?.schema_name }}</span>
          <span class="badge stat">{{ columns().length }} columns</span>
          <span class="badge stat">{{ outFks().length }} fk-out · {{ inFks().length }} fk-in</span>
          @if (piiCount() > 0) {
            <span class="badge pii">{{ piiCount() }} pii</span>
          }
        </div>
      </div>

      @if (view() === 'map') {
        <!-- MAP mode placeholder — full implementation lands in the next
             step.  For now we surface focal-table + 1-hop summary so the
             toggle is visible and functional. -->
        <div class="map-placeholder card">
          <div class="muted">
            <strong>Map view</strong> — focal-table graph with 1-hop neighbours
            coming next.  This is the queryviz "map" mode skeleton; the
            cards-and-bezier renderer is already in place on the
            Relationships graph tab.  Switch back to <em>table</em> to see
            the full per-table detail page.
          </div>
          <div class="map-stats muted small">
            Focal: <code>{{ tableName }}</code> ·
            {{ outFks().length + inFks().length }} connections ·
            {{ uniqueNeighborCount() }} neighbour table(s)
          </div>
        </div>
      }

      @if (view() === 'table') {

      <div class="layout">
        <div class="main">
          <!-- FIELDS panel — queryviz: bordered card, header "fields (N)",
               table FIELD | TYPE | LENGTH | KEY | DESCRIPTION -->
          <section class="card panel">
            <header class="panel-head">
              <h3 class="panel-title">fields ({{ columns().length }})</h3>
            </header>
            <table class="fields">
              <thead>
                <tr>
                  <th>field</th>
                  <th>type</th>
                  <th class="num">length</th>
                  <th class="center key-col">key</th>
                  <th>description</th>
                </tr>
              </thead>
              <tbody>
                @for (c of columns(); track c.name) {
                  <tr>
                    <td><code class="field-name">{{ c.name }}</code></td>
                    <td class="muted small lower">{{ c.type }}</td>
                    <td class="num muted small">{{ c.length }}</td>
                    <td class="center">
                      @if (c.is_pk) { <span class="kbadge pk" title="Primary key">PK</span> }
                      @if (c.is_fk && !c.is_pk) { <span class="kbadge fk" title="Foreign key">FK</span> }
                    </td>
                    <td class="muted small">
                      @if (c.pii_types.length > 0) {
                        @for (p of c.pii_types; track p) {
                          <span class="kbadge pii">{{ p }}</span>
                        }
                      } @else {
                        <span class="dash">—</span>
                      }
                    </td>
                  </tr>
                }
                @if (columns().length === 0) {
                  <tr><td colspan="5" class="muted center">No columns inventoried.</td></tr>
                }
              </tbody>
            </table>
          </section>
        </div>

        <!-- RELATIONSHIPS panel — grouped by relationship type per the
             queryviz layout.  Each group: type name + subtitle, then
             "→ references" (outbound) and "← referenced by" (inbound)
             sections, each item is a clickable target-table card. -->
        <aside class="sidebar">
          <section class="card panel">
            <header class="panel-head">
              <h3 class="panel-title">relationships ({{ outFks().length + inFks().length }})</h3>
            </header>

            @if (outFks().length + inFks().length === 0) {
              <p class="muted small">No relationships discovered.</p>
            }

            @for (g of groupedRelationships(); track g.type) {
              <div class="rel-group">
                <div class="rel-group-head">
                  <div class="rel-group-name" [style.color]="relTypeColor(g.type)">
                    {{ relTypeLabel(g.type) }}
                  </div>
                  <div class="rel-group-sub muted small">{{ relTypeSubtitle(g.type) }}</div>
                </div>

                @if (g.outbound.length > 0) {
                  <div class="rel-direction">→ references</div>
                  @for (f of g.outbound; track f.parentTable + f.childCol + f.parentCol) {
                    <a class="rel-card"
                       [routerLink]="['/jobs', jobId, 'tables', f.parentTable]"
                       [queryParams]="{ view: 'table' }">
                      <div class="rel-card-head">
                        <span class="rel-target mono">{{ f.parentTable }}</span>
                      </div>
                      <div class="rel-mappings">
                        <span class="map-row">
                          <span class="col-pill">{{ f.childCol }}</span>
                          <span class="map-arrow">→</span>
                          <span class="col-pill">{{ f.parentCol }}</span>
                        </span>
                      </div>
                      <div class="rel-foot muted small">
                        @if (f.cardinality) {
                          <span class="card-tag">{{ cardLabel(f.cardinality) }}</span>
                        }
                        @if (f.confidence !== null) {
                          <span class="conf">conf {{ f.confidence!.toFixed(2) }}</span>
                        }
                      </div>
                    </a>
                  }
                }

                @if (g.inbound.length > 0) {
                  <div class="rel-direction">← referenced by</div>
                  @for (f of g.inbound; track f.childTable + f.childCol + f.parentCol) {
                    <a class="rel-card"
                       [routerLink]="['/jobs', jobId, 'tables', f.childTable]"
                       [queryParams]="{ view: 'table' }">
                      <div class="rel-card-head">
                        <span class="rel-target mono">{{ f.childTable }}</span>
                      </div>
                      <div class="rel-mappings">
                        <span class="map-row">
                          <span class="col-pill">{{ f.childCol }}</span>
                          <span class="map-arrow">→</span>
                          <span class="col-pill">{{ f.parentCol }}</span>
                        </span>
                      </div>
                      <div class="rel-foot muted small">
                        @if (f.cardinality) {
                          <span class="card-tag">{{ cardLabel(f.cardinality) }}</span>
                        }
                        @if (f.confidence !== null) {
                          <span class="conf">conf {{ f.confidence!.toFixed(2) }}</span>
                        }
                      </div>
                    </a>
                  }
                }
              </div>
            }
          </section>
        </aside>
      </div>
      }
    }
  `,
  styles: [`
    :host { display: block; max-width: 1400px; margin: 0 auto; padding: 0 4px; }
    .back { color: #8b949e; font-size: 13px; }

    /* Top page header — table name + description on the left, view toggle on
       the right.  Shared by both modes per the queryviz two-mode spec. */
    .page-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 24px;
      margin: 16px 0 6px;
      flex-wrap: wrap;
    }
    .title-row {
      display: flex;
      align-items: baseline;
      gap: 12px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .title-row h1 {
      margin: 0;
      font-size: 26px;
      font-weight: 500;
      letter-spacing: -0.3px;
      color: #e6edf3;
    }
    .title-desc {
      font-size: 13px;
      max-width: 600px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .view-toggle {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 999px;
      flex-shrink: 0;
    }
    .view-toggle button {
      background: transparent;
      border: none;
      color: #8b949e;
      padding: 4px 16px;
      border-radius: 999px;
      font-size: 13px;
      letter-spacing: 0;
      text-transform: lowercase;
      cursor: pointer;
    }
    .view-toggle button.active {
      background: #1f6feb;
      color: #fff;
    }
    .view-toggle .sep {
      color: #30363d;
      font-size: 12px;
      user-select: none;
    }

    /* Placeholder shown while MAP-mode skeleton ships in a follow-up. */
    .map-placeholder {
      padding: 28px;
      text-align: center;
    }
    .map-placeholder .map-stats {
      margin-top: 12px;
      font-family: ui-monospace, SFMono-Regular, monospace;
    }

    .header {
      margin: 6px 0 18px;
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

    /* === queryviz-flavoured panel + fields/relationships layout === */

    .panel { padding: 0; overflow: hidden; }
    .panel-head {
      padding: 14px 18px;
      border-bottom: 1px solid #30363d;
      background: #1c222b;
    }
    .panel-title {
      margin: 0;
      font-size: 13px;
      font-weight: 600;
      color: #c9d1d9;
      text-transform: lowercase;
      letter-spacing: 0;
    }

    /* Fields table (left column) */
    table.fields {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    table.fields th {
      text-align: left;
      padding: 10px 14px;
      font-size: 11px;
      font-weight: 500;
      color: #8b949e;
      text-transform: lowercase;
      letter-spacing: 0;
      border-bottom: 1px solid #21262d;
      background: transparent;
      position: static;
    }
    table.fields th.num { text-align: right; }
    table.fields th.center { text-align: center; }
    table.fields td {
      padding: 8px 14px;
      border-bottom: 1px solid #1c222b;
      vertical-align: middle;
    }
    table.fields tr:last-child td { border-bottom: none; }
    table.fields tr:hover td { background: #1c222b; }
    .field-name {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 13px;
      font-weight: 500;
      color: #e6edf3;
      text-transform: uppercase;
      letter-spacing: 0.2px;
    }
    .lower { text-transform: lowercase; }
    .key-col { width: 60px; }
    .dash { color: #484f58; }

    /* Relationships panel (right column) */
    .rel-group {
      padding: 14px 18px;
      border-bottom: 1px solid #21262d;
    }
    .rel-group:last-child { border-bottom: none; }
    .rel-group-head { margin-bottom: 8px; }
    .rel-group-name {
      font-size: 13px;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: lowercase;
    }
    .rel-group-sub {
      margin-top: 1px;
      font-size: 11px;
    }
    .rel-direction {
      font-size: 10px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
      margin: 10px 0 6px;
    }
    .rel-card {
      display: block;
      background: #0d1117;
      border: 1px solid #21262d;
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 8px;
      text-decoration: none;
      transition: border-color 0.12s, transform 0.12s;
      cursor: pointer;
    }
    .rel-card:hover {
      border-color: #58a6ff;
      transform: translateY(-1px);
    }
    .rel-card-head { margin-bottom: 6px; }
    .rel-target {
      font-weight: 600;
      font-size: 13px;
      color: #e6edf3;
      letter-spacing: -0.2px;
    }
    .rel-mappings { margin: 4px 0 6px; }
    .map-row {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .col-pill {
      display: inline-block;
      background: #1c222b;
      color: #c9d1d9;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 6px;
      border: 1px solid #30363d;
    }
    .map-arrow { color: #6e7681; font-size: 11px; }
    .rel-foot {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      align-items: baseline;
    }
    .rel-foot .card-tag {
      font-size: 10px;
      letter-spacing: 0.4px;
      color: #c9d1d9;
      text-transform: uppercase;
    }
    .rel-foot .conf {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 10px;
      color: #6e7681;
    }

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
  private router = inject(Router);
  private jobsSvc = inject(JobService);

  jobId = '';
  tableName = '';

  // 'table' (queryviz field-detail page) | 'map' (focal-table 1-hop graph).
  // Synced with the URL ?view= param so the toggle is shareable + bookmarkable.
  view = signal<'table' | 'map'>('table');

  loading = signal(true);
  error = signal<string | null>(null);

  job = signal<Job | null>(null);
  private allColumns = signal<ColumnInfo[]>([]);
  private allEdges = signal<RelationshipEdge[]>([]);
  private allPii = signal<PiiFinding[]>([]);

  setView(v: 'table' | 'map'): void {
    if (this.view() === v) return;
    this.view.set(v);
    // Update URL without reloading the component.  ``replaceUrl: true`` so
    // the browser back-button doesn't bounce between toggle states.
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { view: v },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Short one-liner shown next to the table name in the page header. */
  headerDescription = computed(() => {
    const j = this.job();
    const tbl = this.tableName;
    if (!j) return '';
    const cols = this.columns().length;
    const fkOut = this.outFks().length;
    const fkIn = this.inFks().length;
    return `${cols} field${cols === 1 ? '' : 's'} · ${fkOut + fkIn} relationship${
      fkOut + fkIn === 1 ? '' : 's'} in ${j.schema_name}`;
  });

  /** Distinct neighbour tables for the focal-map summary placeholder. */
  uniqueNeighborCount = computed(() => {
    const set = new Set<string>();
    for (const f of this.outFks()) set.add(f.parentTable);
    for (const f of this.inFks()) set.add(f.childTable);
    set.delete(this.tableName);
    return set.size;
  });

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
    // Hydrate the view signal from the URL on first load.
    const v0 = this.route.snapshot.queryParamMap.get('view');
    this.view.set(v0 === 'map' ? 'map' : 'table');
    // Stay in sync if the param changes via browser back/forward.
    this.route.queryParamMap.subscribe(qp => {
      const v = qp.get('view');
      const next = v === 'map' ? 'map' : 'table';
      if (next !== this.view()) this.view.set(next);
    });

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

  // Relationship-type grouping ------------------------------------------
  // 5-bucket classifier matching the relationship-graph component's
  // colour palette: header_item / master_lookup / config / text / history.
  // Outbound + inbound FKs are grouped under the same type so the queryviz
  // panel shows "header / item → references {N}" + "← referenced by {M}"
  // sub-sections within each group.

  groupedRelationships = computed(() => {
    const all = [
      ...this.outFks().map(f => ({ ...f, dir: 'out' as const })),
      ...this.inFks().map(f => ({ ...f, dir: 'in' as const })),
    ];
    const groups = new Map<RelType, { outbound: FkRow[]; inbound: FkRow[] }>();
    for (const f of all) {
      const t = this.classifyRelType(f);
      const g = groups.get(t) ?? { outbound: [], inbound: [] };
      if (f.dir === 'out') g.outbound.push(f);
      else g.inbound.push(f);
      groups.set(t, g);
    }
    // Stable type order — most common first.
    const order: RelType[] = ['header_item', 'master_lookup', 'config', 'text', 'history'];
    const out: { type: RelType; outbound: FkRow[]; inbound: FkRow[] }[] = [];
    for (const t of order) {
      const g = groups.get(t);
      if (g) out.push({ type: t, ...g });
    }
    return out;
  });

  private classifyRelType(f: FkRow): RelType {
    const childT = f.childTable.toLowerCase();
    const parentT = f.parentTable.toLowerCase();
    const join = `${f.childCol} → ${f.parentCol}`.toLowerCase();
    if (/_audit|_log|_history|_event|change_log/.test(childT) ||
        /_audit|_log|_history|_event|change_log/.test(parentT)) return 'history';
    if (/config|setting|policy|rule|param/.test(parentT)) return 'config';
    if (/_text|_desc|_note|_message|_comment|_summary|_body/.test(join)) return 'text';
    if (/status|category|country|language|currency|type|kind|locale|state|region|department|priority|level|code$/.test(parentT)) return 'master_lookup';
    return 'header_item';
  }

  relTypeLabel(t: RelType): string {
    switch (t) {
      case 'header_item':   return 'header / item';
      case 'master_lookup': return 'master lookup';
      case 'config':        return 'config';
      case 'text':          return 'text';
      case 'history':       return 'history';
    }
  }

  relTypeSubtitle(t: RelType): string {
    switch (t) {
      case 'header_item':   return 'regular FK relationships between transactional tables';
      case 'master_lookup': return 'references to a tiny dictionary / vocabulary table';
      case 'config':        return 'links into configuration / policy / rule tables';
      case 'text':          return 'free-form prose columns (notes / description / body)';
      case 'history':       return 'historical / audit / event tracking records';
    }
  }

  relTypeColor(t: RelType): string {
    switch (t) {
      case 'header_item':   return '#58a6ff';
      case 'master_lookup': return '#3fb950';
      case 'config':        return '#bc8cff';
      case 'text':          return '#d29922';
      case 'history':       return '#8b949e';
    }
  }

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
