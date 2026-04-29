import {
  Component,
  OnInit,
  inject,
  input,
  signal,
  computed,
} from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { RouterLink } from '@angular/router';
import { catchError, of } from 'rxjs';
import { ErdCardComponent } from '../erd-card/erd-card.component';
import { clusterColor } from '../cluster-graph/cluster-graph.component';

// ---------------------------------------------------------------------------
// Local cluster types (mirrors the API contract from CL-3)
// ---------------------------------------------------------------------------

export interface ClusterMemberTable {
  table_id: number;
  table_name: string;
  row_count: number;
  archetype: string;          // FACT | DIMENSION | LOOKUP | BRIDGE | UNKNOWN
  subject_kinds: string[];
}

export interface ClusterEdge {
  from: string;
  to: string;
  child_column: string;
  parent_column: string;
  confidence: number | null;
  cardinality: string | null;
}

export interface ClusterPiiFinding {
  table_name: string;
  column_name: string;
  pii_type: string;
  score: number | null;
  validated: boolean;
  name_prior?: boolean;
}

export interface ClusterBridgeTable {
  table_name: string;
  to_cluster_id: number | null;
  to_cluster_name: string;
}

export interface ClusterDetail {
  cluster_id: number;
  name: string;
  tables: ClusterMemberTable[];
  edges: ClusterEdge[];
  pii_findings: ClusterPiiFinding[];
  /** Tables OUTSIDE this cluster that are joined via FK from a member.
   *  Rendered as ghost cards in the cluster ERD. */
  bridge_tables?: ClusterBridgeTable[];
  /** FK edges where exactly one endpoint is in this cluster. */
  cross_cluster_edges?: ClusterEdge[];
}

// ---------------------------------------------------------------------------
// Archetype colour helpers
// ---------------------------------------------------------------------------

type Archetype = 'FACT' | 'DIMENSION' | 'LOOKUP' | 'BRIDGE' | 'UNKNOWN' | string;

function archetypeColour(a: Archetype): string {
  switch (a) {
    case 'FACT':      return '#f78166';  // coral-red
    case 'DIMENSION': return '#79c0ff';  // sky-blue
    case 'LOOKUP':    return '#56d364';  // green
    case 'BRIDGE':    return '#d2a8ff';  // lavender
    default:          return '#8b949e';  // muted grey
  }
}

function cardLabel(c: string | null | undefined): string {
  if (!c) return '—';
  switch (c) {
    case 'ONE_TO_ONE':  return '1:1';
    case 'ONE_TO_MANY': return '1:N';
    case 'MANY_TO_ONE': return 'N:1';
    case 'MANY_TO_MANY': return 'N:M';
    default: return c;
  }
}

@Component({
  selector: 'app-cluster-detail',
  standalone: true,
  imports: [CommonModule, DecimalPipe, RouterLink, ErdCardComponent],
  template: `
    <div class="cluster-wrapper">

      <!-- ── Header ─────────────────────────────────────────────────────── -->
      <div class="header">
        <div class="title-row">
          <h2 class="mono">{{ detail()?.name ?? ('Cluster ' + clusterId()) }}</h2>
          <button class="back-btn" (click)="goBack()">← back to clusters</button>
        </div>
        @if (detail()) {
          <p class="subtitle muted">
            {{ detail()!.tables.length }} table(s)
            · {{ detail()!.edges.length }} intra-edge(s)
          </p>
        }
      </div>

      <!-- ── Loading / Error states ──────────────────────────────────────── -->
      @if (loading()) {
        <p class="muted">Loading cluster…</p>
      }

      @if (notFound()) {
        <div class="not-found-box">
          Cluster not found — was the pipeline re-run with different boundaries?
        </div>
      }

      @if (error() && !notFound()) {
        <div class="error-box">{{ error() }}</div>
      }

      <!-- ── Main content ─────────────────────────────────────────────────── -->
      @if (detail() && !loading()) {

        <!-- Archetype bar -->
        <div class="arch-bar-row">
          @for (seg of archetypeSegments(); track seg.label) {
            <div
              class="arch-seg"
              [style.width.%]="seg.pct"
              [style.background]="seg.colour"
              [title]="seg.label + ': ' + seg.count">
            </div>
          }
        </div>
        <div class="arch-legend">
          @for (seg of archetypeSegments(); track seg.label) {
            <span class="arch-badge" [style.border-color]="seg.colour">
              {{ seg.label }} {{ seg.count }}
            </span>
          }
        </div>

        <!-- ── View toggle: table list ↔ cluster ERD ─────────────────────── -->
        <div class="view-toggle">
          <button
            class="seg-btn"
            [class.active]="view() === 'list'"
            (click)="view.set('list')">
            Table list
          </button>
          <button
            class="seg-btn"
            [class.active]="view() === 'erd'"
            (click)="view.set('erd')">
            ERD diagram
          </button>
        </div>

        <!-- ── ERD diagram (cluster-scoped, with bridge "super-points") ─── -->
        @if (view() === 'erd') {
          <section class="erd-section">
            <div class="sec-title">
              Cluster ERD
              <span class="count">
                ({{ detail()!.tables.length }} tables
                @if (bridgeTableNames().length > 0) {
                  · {{ bridgeTableNames().length }} bridge
                  super-point{{ bridgeTableNames().length !== 1 ? 's' : '' }}
                }
                )
              </span>
            </div>
            @if (bridgeTableNames().length > 0) {
              <div class="bridge-legend muted small">
                <span class="bridge-swatch"></span>
                Dashed amber cards are tables OUTSIDE this cluster joined via FK
                — your cluster's "super-points" connecting to:
                @for (b of detail()!.bridge_tables; track b.table_name; let last = $last) {
                  <a class="tlink mono" [routerLink]="['/jobs', jobId(), 'tables', b.table_name]"
                     title="Open the queryviz-style page">{{ b.table_name }}</a>
                  @if (b.to_cluster_name) {
                    <span class="muted">→ {{ b.to_cluster_name }}</span>
                  }{{ last ? '' : ', ' }}
                }
              </div>
            }
            <app-erd-card
              [jobId]="jobId()"
              [filterTableNames]="memberTableNames()"
              [bridgeTableNames]="bridgeTableNames()"
              [bridgeColors]="bridgeColors()" />
          </section>
        }

        @if (view() === 'list') {
        <!-- ── Tables section ──────────────────────────────────────────────── -->
        <section>
          <div class="sec-title">
            Tables
            <span class="count">({{ detail()!.tables.length }})</span>
          </div>
          <table class="data">
            <thead>
              <tr>
                <th>Table</th>
                <th class="cell-right">Rows</th>
                <th>Archetype</th>
                <th>PII tags</th>
              </tr>
            </thead>
            <tbody>
              @for (t of detail()!.tables; track t.table_id) {
                <tr>
                  <td>
                    <a class="tlink mono" [routerLink]="['/jobs', jobId(), 'tables', t.table_name]"
                       title="Open the queryviz-style page">{{ t.table_name }}</a>
                  </td>
                  <td class="cell-right mono">{{ t.row_count | number }}</td>
                  <td>
                    <span class="arch-chip" [style.border-color]="archetypeColour(t.archetype)">
                      {{ t.archetype }}
                    </span>
                  </td>
                  <td>
                    @if (piiTagsForTable(t.table_name).length === 0) {
                      <span class="muted small">-</span>
                    } @else {
                      @for (tag of piiTagsForTable(t.table_name); track tag.pii_type) {
                        <span
                          class="pii-chip"
                          [class.pii-direct]="!tag.name_prior"
                          [class.pii-advisory]="tag.name_prior">
                          {{ tag.pii_type }}
                        </span>
                      }
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </section>

        <!-- ── Edges section ──────────────────────────────────────────────── -->
        <section>
          <div class="sec-title">
            Intra-cluster edges
            <span class="count">({{ detail()!.edges.length }})</span>
          </div>
          @if (detail()!.edges.length === 0) {
            <div class="muted small">No intra-cluster edges.</div>
          } @else {
            <table class="data">
              <thead>
                <tr>
                  <th>Child table</th>
                  <th>Child column</th>
                  <th></th>
                  <th>Parent table</th>
                  <th>Parent column</th>
                  <th class="cell-right">Conf.</th>
                  <th class="cell-center">Card.</th>
                </tr>
              </thead>
              <tbody>
                @for (e of detail()!.edges; track $index) {
                  <tr>
                    <td><code class="mono">{{ e.from }}</code></td>
                    <td><code class="mono">{{ e.child_column }}</code></td>
                    <td class="arrow">→</td>
                    <td><code class="mono">{{ e.to }}</code></td>
                    <td><code class="mono">{{ e.parent_column }}</code></td>
                    <td class="cell-right mono">
                      {{ e.confidence == null ? '—' : (e.confidence | number:'1.2-2') }}
                    </td>
                    <td class="cell-center muted small">{{ cardLabel(e.cardinality) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <!-- ── PII findings section ───────────────────────────────────────── -->
        <section>
          <div class="sec-title">
            PII findings
            <span class="count">({{ detail()!.pii_findings.length }})</span>
          </div>
          @if (detail()!.pii_findings.length === 0) {
            <div class="muted small">No PII findings for this cluster.</div>
          } @else {
            <table class="data">
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Column</th>
                  <th>PII type</th>
                  <th class="cell-right">Score</th>
                  <th class="cell-center">Validated</th>
                </tr>
              </thead>
              <tbody>
                @for (p of detail()!.pii_findings; track $index) {
                  <tr [class.pii-validated]="p.validated">
                    <td>
                      <a class="tlink mono" [routerLink]="['/jobs', jobId(), 'tables', p.table_name]"
                         title="Open the queryviz-style page">{{ p.table_name }}</a>
                    </td>
                    <td><code class="mono">{{ p.column_name }}</code></td>
                    <td>
                      <span
                        class="pii-chip"
                        [class.pii-direct]="!p.name_prior"
                        [class.pii-advisory]="p.name_prior">
                        {{ p.pii_type }}
                      </span>
                    </td>
                    <td class="cell-right mono">
                      {{ p.score == null ? '—' : (p.score | number:'1.2-2') }}
                    </td>
                    <td class="cell-center">
                      @if (p.validated) { <span class="badge-yes">✓</span> }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        }   <!-- /@if (view() === 'list') -->

        <!-- ── Archival policy placeholder ──────────────────────────────── -->
        <div class="archival-row">
          <button class="archival-btn" disabled>
            Set archival policy for this cluster
          </button>
        </div>

      }
    </div>
  `,
  styles: [`
    .cluster-wrapper {
      padding: 20px 0;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    /* Segmented "Table list ↔ ERD diagram" toggle */
    .view-toggle {
      display: inline-flex;
      gap: 0;
      align-self: flex-start;
      border: 1px solid #30363d;
      border-radius: 6px;
      overflow: hidden;
      background: #0d1117;
    }
    .view-toggle .seg-btn {
      background: transparent;
      color: #8b949e;
      border: none;
      padding: 7px 14px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }
    .view-toggle .seg-btn:hover { color: #e6edf3; background: #161b22; }
    .view-toggle .seg-btn.active {
      background: #1f6feb;
      color: white;
    }
    .erd-section {
      border-top: 1px solid #21262d;
      padding-top: 12px;
    }
    .bridge-legend {
      margin: 4px 0 12px;
      font-size: 12px;
      line-height: 1.6;
    }
    .bridge-legend .bridge-swatch {
      display: inline-block;
      width: 14px; height: 14px;
      vertical-align: middle;
      margin-right: 6px;
      border: 2px dashed #d29922;
      background: #181308;
      border-radius: 2px;
    }
    .bridge-legend code.mono {
      color: #e3b341;
      background: #2a210b;
      padding: 1px 5px;
      border-radius: 3px;
      margin-right: 2px;
    }
    .header { display: flex; flex-direction: column; gap: 6px; }
    .title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h2 { margin: 0; font-size: 20px; color: #e6edf3; }
    .subtitle { margin: 0; font-size: 13px; }
    .back-btn {
      background: #21262d;
      border: 1px solid #30363d;
      color: #58a6ff;
      padding: 4px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
    }
    .back-btn:hover { background: #30363d; }

    /* Archetype bar */
    .arch-bar-row {
      display: flex;
      height: 10px;
      border-radius: 4px;
      overflow: hidden;
      gap: 1px;
    }
    .arch-seg { height: 100%; transition: width 0.2s ease; }
    .arch-legend { display: flex; gap: 8px; flex-wrap: wrap; }
    .arch-badge {
      display: inline-block;
      border: 1px solid #8b949e;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
      padding: 1px 8px;
      color: #e6edf3;
      letter-spacing: 0.4px;
    }
    .arch-chip {
      display: inline-block;
      border: 1px solid #8b949e;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      padding: 1px 6px;
      color: #e6edf3;
      letter-spacing: 0.3px;
    }

    /* PII chips */
    .pii-chip {
      display: inline-block;
      border-radius: 8px;
      font-size: 10px;
      font-weight: 700;
      padding: 1px 6px;
      margin-right: 4px;
      border: 1.5px solid transparent;
    }
    .pii-direct  { border-color: #f85149; color: #f85149; background: rgba(248,81,73,.08); }
    .pii-advisory{ border-color: #d29922; color: #d29922; background: rgba(210,153,34,.08); }

    /* Section headers */
    section { border-top: 1px solid #21262d; padding-top: 12px; }
    .sec-title {
      font-size: 11px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
      margin-bottom: 8px;
      font-weight: 600;
    }
    .count {
      color: #8b949e;
      font-weight: 400;
      letter-spacing: 0;
      text-transform: none;
      font-size: 11px;
      margin-left: 6px;
    }

    /* Data tables — mirroring table-detail.component.ts exactly */
    table.data {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      background: #0d1117;
      border: 1px solid #21262d;
      border-radius: 4px;
      overflow: hidden;
    }
    table.data thead th {
      text-align: left;
      padding: 7px 10px;
      font-size: 11px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #8b949e;
      border-bottom: 1px solid #30363d;
      background: #161b22;
      font-weight: 600;
    }
    table.data tbody td {
      padding: 6px 10px;
      border-bottom: 1px solid #1a1f26;
      vertical-align: top;
    }
    table.data tbody tr:last-child td { border-bottom: none; }
    table.data tbody tr:hover { background: #161b22; }
    table.data .cell-center { text-align: center; }
    table.data .cell-right  { text-align: right; font-variant-numeric: tabular-nums; }
    table.data tr.pii-validated { background: rgba(248,81,73,0.06); }
    .arrow { color: #8b949e; }

    /* Archival placeholder */
    .archival-row { padding: 8px 0; }
    .archival-btn {
      background: #21262d;
      border: 1px solid #30363d;
      color: #484f58;
      padding: 6px 14px;
      border-radius: 6px;
      cursor: not-allowed;
      font-size: 13px;
    }

    .badge-yes { color: #3fb950; font-weight: 700; }

    /* Error / not-found boxes */
    .not-found-box, .error-box {
      padding: 12px 16px;
      border-radius: 6px;
      font-size: 13px;
    }
    .not-found-box {
      background: #261a08;
      border: 1px solid #d29922;
      color: #e3b341;
    }
    .error-box {
      background: #3a0d0d;
      border: 1px solid #f85149;
      color: #ffabab;
    }
    .muted  { color: #8b949e; }
    .small  { font-size: 12px; }
    .mono   { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }
  `],
})
export class ClusterDetailComponent implements OnInit {
  /** job_id from parent route */
  jobId    = input.required<string>();
  /** cluster_id from parent route */
  clusterId = input.required<number>();

  private http = inject(HttpClient);

  detail   = signal<ClusterDetail | null>(null);
  loading  = signal(true);
  notFound = signal(false);
  error    = signal<string | null>(null);

  /** Toggle between the existing tabular detail and a cluster-scoped ERD. */
  view = signal<'list' | 'erd'>('list');

  /** Member table names — feeds the ERD card view's filter input. */
  memberTableNames = computed<string[]>(() =>
    (this.detail()?.tables ?? []).map(t => t.table_name)
  );

  /** Cross-cluster bridge table names — rendered as ghost cards in the ERD. */
  bridgeTableNames = computed<string[]>(() =>
    (this.detail()?.bridge_tables ?? []).map(b => b.table_name)
  );

  /** Per-bridge color (table_name → cluster color) so super-points pick up
   *  the same color as their owning cluster's bubble in the macro graph. */
  bridgeColors = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const b of (this.detail()?.bridge_tables ?? [])) {
      if (b.to_cluster_id != null) {
        out[b.table_name] = clusterColor(b.to_cluster_id);
      }
    }
    return out;
  });

  // Lookup: table_name → pii findings
  piiMap = computed<Map<string, ClusterPiiFinding[]>>(() => {
    const d = this.detail();
    if (!d) return new Map();
    const m = new Map<string, ClusterPiiFinding[]>();
    for (const p of d.pii_findings) {
      const arr = m.get(p.table_name) ?? [];
      arr.push(p);
      m.set(p.table_name, arr);
    }
    return m;
  });

  archetypeSegments = computed<{ label: string; count: number; pct: number; colour: string }[]>(() => {
    const d = this.detail();
    if (!d || d.tables.length === 0) return [];
    const counts = new Map<string, number>();
    for (const t of d.tables) {
      counts.set(t.archetype, (counts.get(t.archetype) ?? 0) + 1);
    }
    const total = d.tables.length;
    return [...counts.entries()].map(([label, count]) => ({
      label,
      count,
      pct: (count / total) * 100,
      colour: archetypeColour(label),
    }));
  });

  ngOnInit(): void {
    const jobId    = this.jobId();
    const clusterId = this.clusterId();
    // BUG-FIX: cluster_id=0 is a VALID id (0-indexed local id); the previous
    // `!clusterId` guard short-circuited and left the page stuck on "Loading…".
    if (!jobId || clusterId === null || clusterId === undefined || Number.isNaN(clusterId)) {
      this.loading.set(false);
      this.error.set('Invalid cluster URL.');
      return;
    }

    this.http
      .get<ClusterDetail>(`/api/jobs/${jobId}/clusters/${clusterId}`)
      .pipe(
        catchError(err => {
          if (err?.status === 404) {
            this.notFound.set(true);
          } else {
            this.error.set(err?.error?.detail ?? err?.message ?? 'Failed to load cluster.');
          }
          this.loading.set(false);
          return of(null);
        }),
      )
      .subscribe(d => {
        if (d) this.detail.set(d);
        this.loading.set(false);
      });
  }

  piiTagsForTable(tableName: string): ClusterPiiFinding[] {
    return this.piiMap().get(tableName) ?? [];
  }

  /** Exposed so the template can call it without importing the function. */
  archetypeColour(a: string): string {
    return archetypeColour(a);
  }

  cardLabel(c: string | null | undefined): string {
    return cardLabel(c);
  }

  goBack(): void {
    history.back();
  }
}
