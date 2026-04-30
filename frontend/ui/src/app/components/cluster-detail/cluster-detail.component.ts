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
import { Router, RouterLink } from '@angular/router';
import { catchError, of } from 'rxjs';
import { ClusterErdComponent } from '../cluster-erd/cluster-erd.component';
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
  imports: [CommonModule, DecimalPipe, RouterLink, ClusterErdComponent],
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

        <!-- Archetype proportion bar — full width.  Inline middot-
             separated labels render BELOW the bar so segment names
             never crowd a thin band.  The filter pills on the row
             after toggle archetype visibility on the table list. -->
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
        <div class="arch-bar-labels mono">
          @for (seg of archetypeSegments(); track seg.label; let last = $last) {
            <span class="arch-bar-label" [style.color]="seg.colour">{{ seg.label }}&nbsp;{{ seg.count }}</span>
            @if (!last) { <span class="dot-sep">·</span> }
          }
        </div>
        <div class="arch-legend">
          @for (seg of archetypeSegments(); track seg.label) {
            <button type="button"
                    class="arch-badge"
                    [class.active]="archFilter().has(seg.label)"
                    [style.border-color]="seg.colour"
                    [style.color]="archFilter().has(seg.label) ? '#0d1117' : seg.colour"
                    [style.background]="archFilter().has(seg.label) ? seg.colour : 'transparent'"
                    (click)="toggleArchFilter(seg.label)"
                    [title]="archFilter().has(seg.label) ? 'Click to hide' : 'Click to filter'">
              {{ seg.label }} {{ seg.count }}
            </button>
          }
          @if (archFilter().size > 0 && archFilter().size < archetypeSegments().length) {
            <button type="button" class="arch-clear" (click)="clearArchFilter()" title="Clear filter">clear</button>
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
            <!-- Structured collapsible "external links" panel — replaces
                 the inline-link wall of prose.  Collapsed by default;
                 the expanded state lists each super-point column → the
                 external table it joins, both as deep links. -->
            @if (bridgeTableNames().length > 0) {
              <details class="bridge-panel" [open]="bridgePanelOpen()">
                <summary class="bridge-summary"
                         (click)="$event.preventDefault(); bridgePanelOpen.set(!bridgePanelOpen())">
                  <span class="bridge-icon">&#9432;</span>
                  This cluster references
                  <strong>{{ externalTableCount() }}</strong> external
                  table{{ externalTableCount() === 1 ? '' : 's' }} via
                  <strong>{{ superPointCount() }}</strong> super-point{{ superPointCount() === 1 ? '' : 's' }}
                  <span class="bridge-toggle">{{ bridgePanelOpen() ? 'hide details' : 'show details' }}</span>
                </summary>
                <div class="bridge-list">
                  @for (sp of superPoints(); track sp.key) {
                    <div class="bridge-row">
                      <a class="tlink mono"
                         [routerLink]="['/jobs', jobId(), 'tables', sp.fromTable]"
                         [title]="'Open ' + sp.fromTable">{{ sp.fromTable }}.{{ sp.column }}</a>
                      <span class="bridge-arrow">&rarr;</span>
                      @if (sp.toClusterId !== null) {
                        <a class="tlink mono"
                           [routerLink]="['/jobs', jobId(), 'clusters', sp.toClusterId]"
                           [title]="'Open ' + (sp.toClusterName || ('cluster ' + sp.toClusterId))">{{ sp.toTable }}</a>
                      } @else {
                        <a class="tlink mono"
                           [routerLink]="['/jobs', jobId(), 'tables', sp.toTable]"
                           [title]="'Open ' + sp.toTable">{{ sp.toTable }}</a>
                      }
                      @if (sp.toClusterName) {
                        <span class="muted small">({{ sp.toClusterName }})</span>
                      }
                    </div>
                  }
                </div>
              </details>
            }
            <app-cluster-erd
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
          <table class="data tables-list">
            <colgroup>
              <col class="col-table" />
              <col class="col-rows" />
              <col class="col-arch" />
              <col class="col-pii" />
            </colgroup>
            <thead>
              <tr>
                <th>Table</th>
                <th class="cell-right">Rows</th>
                <th>Archetype</th>
                <th>PII tags</th>
              </tr>
            </thead>
            <tbody>
              @for (t of filteredTables(); track t.table_id) {
                <tr class="row-clickable" (click)="goToTable(t.table_name)" [title]="'Open ' + t.table_name">
                  <td>
                    <a class="tlink mono" [routerLink]="['/jobs', jobId(), 'tables', t.table_name]"
                       (click)="$event.stopPropagation()"
                       title="Open the queryviz-style page">{{ t.table_name }}</a>
                  </td>
                  <td class="cell-right mono">{{ t.row_count | number }}</td>
                  <td>
                    <span class="arch-chip" [style.border-color]="archetypeColour(t.archetype)">
                      {{ t.archetype }}
                    </span>
                  </td>
                  <td class="pii-cell">
                    @if (piiTagsForTable(t.table_name).length === 0) {
                      <span class="muted small">-</span>
                    } @else {
                      @for (tag of piiTagsForTable(t.table_name); track tag.pii_type) {
                        <span
                          class="pii-chip"
                          [class.pii-direct]="!tag.name_prior"
                          [class.pii-advisory]="tag.name_prior"
                          [attr.title]="tag.count > 1 ? tag.pii_type + ' on ' + tag.count + ' columns' : tag.pii_type">
                          {{ tag.pii_type }}@if (tag.count > 1) {<span class="pii-count">&times;{{ tag.count }}</span>}
                        </span>
                      }
                    }
                  </td>
                </tr>
              }
              @if (filteredTables().length === 0) {
                <tr><td colspan="4" class="muted small cell-center">No tables match the active archetype filter.</td></tr>
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
    /* Structured external-references panel — replaces the prose para
       that mixed link soup and instructions on one line.  Closed by
       default; the summary line carries counts so the user knows the
       "weight" before expanding. */
    .bridge-panel {
      margin: 6px 0 14px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 10px 14px;
    }
    .bridge-panel summary {
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: #c9d1d9;
    }
    .bridge-panel summary::-webkit-details-marker { display: none; }
    .bridge-panel .bridge-icon { color: #58a6ff; font-size: 14px; }
    .bridge-panel .bridge-toggle {
      margin-left: auto;
      color: #58a6ff;
      font-size: 12px;
      letter-spacing: 0.4px;
      text-transform: uppercase;
    }
    .bridge-panel .bridge-list {
      display: grid;
      grid-template-columns: 1fr;
      gap: 4px;
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid #21262d;
      max-height: 240px;
      overflow-y: auto;
    }
    .bridge-panel .bridge-row {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 12.5px;
      padding: 3px 0;
    }
    .bridge-panel .bridge-arrow { color: #6e7681; }
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
    /* Inline middot-separated label row — same vocabulary as the
       cluster cards (FACT 7 · DIM 5 · LOOKUP 5).  Sits BELOW the bar,
       not overlaid on it. */
    .arch-bar-labels {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 4px;
      font-size: 12px;
      letter-spacing: 0.4px;
      color: #8b949e;
    }
    .arch-bar-labels .arch-bar-label { font-weight: 600; }
    .arch-bar-labels .dot-sep { color: #444c56; }
    .arch-legend { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    /* Filter pills — clicking toggles the table-list visibility.
       Active state fills the pill with the archetype colour. */
    .arch-badge {
      display: inline-block;
      border: 1px solid #8b949e;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
      padding: 1px 8px;
      color: #e6edf3;
      letter-spacing: 0.4px;
      background: transparent;
      cursor: pointer;
      font-family: inherit;
      transition: background 0.12s, color 0.12s;
    }
    .arch-badge:hover { filter: brightness(1.15); }
    .arch-badge.active { color: #0d1117 !important; }
    .arch-clear {
      background: transparent;
      border: 1px dashed #30363d;
      color: #8b949e;
      border-radius: 8px;
      font-size: 11px;
      padding: 1px 8px;
      cursor: pointer;
      letter-spacing: 0.4px;
    }
    .arch-clear:hover { color: #e6edf3; border-color: #58a6ff; }
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
    table.data tr.row-clickable { cursor: pointer; }
    table.data tr.row-clickable:hover { background: #1c2129; }
    /* Per-spec column widths for the cluster's tables list — TABLE
       column expands; ROWS narrow + right-aligned; ARCHETYPE fixed;
       PII TAGS wraps without forcing the row taller in the common case. */
    table.tables-list .col-table { width: auto; }
    table.tables-list .col-rows  { width: 96px; }
    table.tables-list .col-arch  { width: 130px; }
    table.tables-list .col-pii   { width: 36%; }
    table.tables-list td.pii-cell { white-space: normal; line-height: 1.9; }
    .pii-chip .pii-count {
      margin-left: 3px;
      font-weight: 500;
      opacity: 0.7;
    }
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
  private router = inject(Router);

  detail   = signal<ClusterDetail | null>(null);
  loading  = signal(true);
  notFound = signal(false);
  error    = signal<string | null>(null);

  /** Toggle between the existing tabular detail and a cluster-scoped ERD. */
  view = signal<'list' | 'erd'>('list');

  /** Active archetype filter — empty Set means "show all".  Click a
   * pill to toggle membership; the table list re-filters reactively. */
  archFilter = signal<Set<string>>(new Set());

  /** External-references panel collapsed by default.  Open/close is
   * fully UI-local (not URL-synced) — it's a presentation detail. */
  bridgePanelOpen = signal<boolean>(false);

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

  /** Deduplicated PII chip list per table — group findings by pii_type
   * so the same tag isn't rendered N times when N columns of one
   * table happen to share the same type (the previous "ADDRESS
   * ADDRESS POSTAL_CODE" rendering bug).  Returns one entry per
   * distinct type with a ``count`` for the title tooltip. */
  piiTagsForTable(tableName: string): Array<ClusterPiiFinding & { count: number }> {
    const findings = this.piiMap().get(tableName) ?? [];
    const merged = new Map<string, ClusterPiiFinding & { count: number }>();
    for (const f of findings) {
      const cur = merged.get(f.pii_type);
      if (cur) {
        cur.count += 1;
        // Promote to a "direct" finding (non-name-prior) if any
        // contributing finding is direct — direct evidence beats
        // advisory in chip styling.
        if (!f.name_prior) cur.name_prior = false;
        if ((f.score ?? 0) > (cur.score ?? 0)) cur.score = f.score;
        if (f.validated) cur.validated = true;
      } else {
        merged.set(f.pii_type, { ...f, count: 1 });
      }
    }
    // Stable order: validated direct hits first, then alpha by type.
    return [...merged.values()].sort((a, b) => {
      const aw = (a.validated ? 0 : 1) + (a.name_prior ? 1 : 0);
      const bw = (b.validated ? 0 : 1) + (b.name_prior ? 1 : 0);
      if (aw !== bw) return aw - bw;
      return a.pii_type.localeCompare(b.pii_type);
    });
  }

  /** Tables filtered by the active archetype filter.  Empty filter
   * (default) returns every member table — matches the previous
   * unfiltered behaviour. */
  filteredTables = computed<ClusterMemberTable[]>(() => {
    const all = this.detail()?.tables ?? [];
    const f = this.archFilter();
    if (f.size === 0) return all;
    return all.filter(t => f.has(t.archetype));
  });

  toggleArchFilter(label: string): void {
    const cur = new Set(this.archFilter());
    if (cur.has(label)) cur.delete(label);
    else cur.add(label);
    this.archFilter.set(cur);
  }
  clearArchFilter(): void { this.archFilter.set(new Set()); }

  /** Click a row → open the table-detail page.  The inner <a> already
   * routes via [routerLink]; this is the click-anywhere convenience. */
  goToTable(tableName: string): void {
    this.router.navigate(['/jobs', this.jobId(), 'tables', tableName]);
  }

  /** Distinct "super-point" rows: each (member table, FK column) pair
   * that joins to an external table.  Built from cross_cluster_edges
   * so the panel reflects the actual edges, not just bridge presence. */
  superPoints = computed<Array<{
    key: string;
    fromTable: string;
    column: string;
    toTable: string;
    toClusterId: number | null;
    toClusterName: string;
  }>>(() => {
    const d = this.detail();
    if (!d) return [];
    const memberSet = new Set((d.tables ?? []).map(t => t.table_name));
    const bridgeMeta = new Map<string, ClusterBridgeTable>();
    for (const b of d.bridge_tables ?? []) {
      bridgeMeta.set(b.table_name, b);
    }
    const seen = new Set<string>();
    const out: Array<any> = [];
    for (const e of d.cross_cluster_edges ?? []) {
      // Direction: super-point lives on the member side; external
      // table is the non-member endpoint.
      const fromIsMember = memberSet.has(e.from);
      const fromTable = fromIsMember ? e.from : e.to;
      const column   = fromIsMember ? e.child_column : e.parent_column;
      const toTable   = fromIsMember ? e.to : e.from;
      const key = `${fromTable}.${column}→${toTable}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const meta = bridgeMeta.get(toTable);
      out.push({
        key,
        fromTable, column, toTable,
        toClusterId: meta?.to_cluster_id ?? null,
        toClusterName: meta?.to_cluster_name ?? '',
      });
    }
    return out.sort((a, b) =>
      a.fromTable.localeCompare(b.fromTable) ||
      a.column.localeCompare(b.column),
    );
  });

  /** Distinct external-table count for the collapsed-summary line. */
  externalTableCount = computed<number>(() =>
    new Set(this.superPoints().map(s => s.toTable)).size,
  );
  superPointCount = computed<number>(() => this.superPoints().length);

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
