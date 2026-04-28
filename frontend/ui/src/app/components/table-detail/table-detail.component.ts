import {
  Component, OnDestroy, OnInit, computed, inject, input, signal,
} from '@angular/core';
import { CommonModule, DecimalPipe, PercentPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { catchError, of } from 'rxjs';
import { JobService } from '../../services/job.service';
import {
  ColumnInfo, JobColumns, PiiFinding, RelationshipEdge, RelationshipGraph,
} from '../../models/job.model';

/**
 * Table-detail side panel.
 *
 * Renders metadata for the currently-selected table:
 *   - inferred PK / FK columns (derived from the relationships endpoint's edge
 *     labels, which are formatted as "child_col → parent_col")
 *   - outbound foreign keys (this table is the child)
 *   - inbound foreign keys (this table is the parent)
 *   - PII findings filtered to this table
 *
 * The panel slides in from the right when a table is selected and slides out
 * when `tableName()` is null. Selection is driven via `JobService.selectedTable`
 * — set either by the graph component on node-click, or via this panel's own
 * dropdown (fallback when the graph wiring isn't ready).
 *
 * Limitation: the relationships endpoint doesn't return full column inventory,
 * so the Columns section can only enumerate columns that participate in a
 * relationship as a PK or FK. Other columns are noted as omitted.
 */
@Component({
  selector: 'app-table-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, DecimalPipe, PercentPipe],
  template: `
    <aside class="panel" [class.open]="open()">
      <div class="head">
        <div class="title">
          @if (tableName()) {
            <span class="muted small">Table</span>
            <h3 class="mono">{{ tableName() }}</h3>
          } @else {
            <h3>Table detail</h3>
          }
        </div>
        <button type="button" class="close" (click)="close()" title="Close panel">✕</button>
      </div>

      <div class="picker">
        <label>
          <span class="muted small">Select table</span>
          <select [ngModel]="tableName()" (ngModelChange)="onPick($event)">
            <option [ngValue]="null">— none —</option>
            @for (n of nodeNames(); track n) {
              <option [ngValue]="n">{{ n }}</option>
            }
          </select>
        </label>
      </div>

      @if (loading()) {
        <div class="muted center pad">Loading…</div>
      }
      @if (error()) {
        <div class="error">{{ error() }}</div>
      }

      @if (!tableName() && !loading() && !error()) {
        <div class="empty muted center pad">Select a table to view details</div>
      }

      @if (tableName() && !loading() && !error()) {
        <section>
          <div class="sec-title">Columns
            <span class="count">({{ columnsTable().length }})</span>
          </div>
          @if (columnsTable().length === 0) {
            <div class="muted small">No columns available for this table.</div>
          } @else {
            <table class="data">
              <thead>
                <tr>
                  <th class="num">#</th>
                  <th>Name</th>
                  <th>Type</th>
                  <th class="cell-center">PK</th>
                  <th class="cell-center">FK</th>
                </tr>
              </thead>
              <tbody>
                @for (c of columnsTable(); track c.column) {
                  <tr [class.pk-row]="c.is_pk" [class.fk-row]="!c.is_pk && c.is_fk">
                    <td class="num">{{ c.ordinal }}</td>
                    <td><code class="mono">{{ c.column }}</code></td>
                    <td class="muted small">{{ c.data_type }}</td>
                    <td class="cell-center">
                      @if (c.is_pk) { <span class="badge pk">PK</span> }
                    </td>
                    <td class="cell-center">
                      @if (c.is_fk) { <span class="badge fk">FK</span> }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
            @if (columnsFromEndpoint() === false) {
              <div class="muted small foot">(columns endpoint unavailable — showing PK/FK columns inferred from edges only)</div>
            }
          }
        </section>

        <section>
          <div class="sec-title">Foreign Keys — outbound
            <span class="count">({{ outFks().length }})</span>
          </div>
          @if (outFks().length === 0) {
            <div class="muted small">No outbound FKs.</div>
          } @else {
            <table class="data">
              <thead>
                <tr>
                  <th>Child column</th>
                  <th></th>
                  <th>Parent table</th>
                  <th>Parent column</th>
                  <th class="cell-right">Conf.</th>
                  <th class="cell-center">Card.</th>
                </tr>
              </thead>
              <tbody>
                @for (e of outFks(); track $index) {
                  <tr>
                    <td><code class="mono">{{ e.childCol }}</code></td>
                    <td class="arrow">→</td>
                    <td><code class="mono">{{ e.parentTable }}</code></td>
                    <td><code class="mono">{{ e.parentCol }}</code></td>
                    <td class="cell-right mono">{{ e.confidence == null ? '—' : (e.confidence | number:'1.2-2') }}</td>
                    <td class="cell-center muted small">{{ cardLabel(e.cardinality) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section>
          <div class="sec-title">Foreign Keys — inbound
            <span class="count">({{ inFks().length }})</span>
          </div>
          @if (inFks().length === 0) {
            <div class="muted small">No inbound FKs.</div>
          } @else {
            <table class="data">
              <thead>
                <tr>
                  <th>Source table</th>
                  <th>Source column</th>
                  <th></th>
                  <th>Local column</th>
                  <th class="cell-right">Conf.</th>
                  <th class="cell-center">Card.</th>
                </tr>
              </thead>
              <tbody>
                @for (e of inFks(); track $index) {
                  <tr>
                    <td><code class="mono">{{ e.childTable }}</code></td>
                    <td><code class="mono">{{ e.childCol }}</code></td>
                    <td class="arrow">→</td>
                    <td><code class="mono">{{ e.parentCol }}</code></td>
                    <td class="cell-right mono">{{ e.confidence == null ? '—' : (e.confidence | number:'1.2-2') }}</td>
                    <td class="cell-center muted small">{{ cardLabel(e.cardinality) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section>
          <div class="sec-title">PII findings
            <span class="count">({{ piiForTable().length }})</span>
          </div>
          @if (piiForTable().length === 0) {
            <div class="muted small">No PII findings for this table.</div>
          } @else {
            <table class="data">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Detector</th>
                  <th class="cell-right">Matches</th>
                  <th class="cell-right">Rate</th>
                  <th class="cell-right">Score</th>
                  <th>Sample</th>
                </tr>
              </thead>
              <tbody>
                @for (p of piiForTable(); track $index) {
                  <tr [class.pii-validated]="p.validated">
                    <td><code class="mono">{{ p.column_name }}</code></td>
                    <td><span class="badge pii-type">{{ p.pii_type }}</span></td>
                    <td class="muted small">{{ p.detector }}</td>
                    <td class="cell-right mono">{{ p.match_count }} / {{ p.sample_count }}</td>
                    <td class="cell-right mono">{{ p.match_rate == null ? '—' : (p.match_rate | percent:'1.0-0') }}</td>
                    <td class="cell-right mono">{{ p.score == null ? '—' : (p.score | number:'1.2-2') }}</td>
                    <td class="muted small ex">{{ sample(p) || '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>
      }
    </aside>
  `,
  styles: [`
    .panel {
      position: relative;
      width: 100%;
      background: #ffffff;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      margin-top: 16px;
      max-height: 0;
      opacity: 0;
      overflow: hidden;
      transition: max-height 240ms ease, opacity 200ms ease, padding 240ms ease;
      padding: 0 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .panel.open {
      max-height: 1200px;
      opacity: 1;
      padding: 16px 18px 24px;
      overflow-y: auto;
    }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-top: 4px;
    }
    .title h3 {
      margin: 2px 0 0;
      font-size: 16px;
      word-break: break-word;
    }
    .close {
      background: transparent;
      border: 1px solid #d0d7de;
      color: #656d76;
      width: 28px;
      height: 28px;
      border-radius: 4px;
      cursor: pointer;
      padding: 0;
      line-height: 1;
    }
    .close:hover { color: #1f2328; border-color: #0969da; }
    .picker label {
      display: flex;
      flex-direction: column;
      gap: 4px;
      text-transform: none;
      letter-spacing: 0;
    }
    .picker select {
      width: 100%;
      background: #f6f8fa;
      border: 1px solid #d0d7de;
      color: #1f2328;
      padding: 6px 8px;
      border-radius: 4px;
      font: inherit;
    }
    section { border-top: 1px solid #f6f8fa; padding-top: 10px; }
    .sec-title {
      font-size: 11px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #656d76;
      margin-bottom: 8px;
    }
    table.data {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      background: #f6f8fa;
      border: 1px solid #f6f8fa;
      border-radius: 4px;
      overflow: hidden;
    }
    table.data thead th {
      text-align: left;
      padding: 7px 10px;
      font-size: 11px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #656d76;
      border-bottom: 1px solid #d0d7de;
      background: #ffffff;
      font-weight: 600;
    }
    table.data tbody td {
      padding: 6px 10px;
      border-bottom: 1px solid #ddf4ff;
      vertical-align: top;
    }
    table.data tbody tr:last-child td { border-bottom: none; }
    table.data tbody tr:hover { background: #f6f8fa; }
    table.data .num { color: #656d76; font-family: ui-monospace, monospace; }
    table.data .cell-center { text-align: center; }
    table.data .cell-right { text-align: right; font-variant-numeric: tabular-nums; }
    table.data tr.pk-row { background: rgba(63, 185, 80, 0.06); }
    table.data tr.fk-row { background: rgba(88, 166, 255, 0.04); }
    table.data tr.pii-validated { background: rgba(248, 81, 73, 0.06); }
    table.data .ex { max-width: 220px; word-break: break-word; }
    .count {
      color: #656d76;
      font-weight: 400;
      letter-spacing: 0;
      text-transform: none;
      font-size: 11px;
      margin-left: 6px;
    }
    .badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 8px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.4px;
    }
    .badge.pk { background: #1a7f37; color: #f6f8fa; }
    .badge.fk { background: #0969da; color: #f6f8fa; }
    .badge.pii-type { background: #0969da; color: white; }
    .arrow { color: #656d76; }
    .conf {
      margin-left: auto;
      color: #656d76;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
      font-size: 12px;
    }
    .ex {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
      word-break: break-word;
    }
    .foot { margin-top: 6px; }
    .center { text-align: center; }
    .pad { padding: 24px 0; }
    .empty { padding: 40px 0; }
    .small { font-size: 12px; }
    .muted { color: #656d76; }
    .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }
    .error {
      color: #cf222e;
      padding: 10px 12px;
      background: #ffebe9;
      border: 1px solid #cf222e;
      border-radius: 6px;
      font-size: 13px;
    }
  `],
})
export class TableDetailComponent implements OnInit, OnDestroy {
  jobId = input.required<string>();

  private jobsSvc = inject(JobService);

  /** Current selection (mirrors JobService.selectedTable). */
  tableName = computed(() => this.jobsSvc.selectedTable());

  /** Whether the panel slides in. */
  open = computed(() => this.tableName() != null);

  graph = signal<RelationshipGraph | null>(null);
  pii = signal<PiiFinding[]>([]);
  cols = signal<JobColumns | null>(null);
  /** True if the /columns endpoint returned data, False if it 404'd. */
  columnsFromEndpoint = signal<boolean | null>(null);
  loading = signal(false);
  error = signal<string | null>(null);

  /** Names of all tables that appear in the relationship graph (for the dropdown). */
  nodeNames = computed(() => {
    const g = this.graph();
    if (!g) return [];
    return [...g.nodes].map(n => n.label).sort();
  });

  outFks = computed(() => {
    const t = this.tableName();
    const g = this.graph();
    if (!t || !g) return [];
    return g.edges
      .filter(e => e.from === t)
      .map(e => parseEdge(e));
  });

  inFks = computed(() => {
    const t = this.tableName();
    const g = this.graph();
    if (!t || !g) return [];
    return g.edges
      .filter(e => e.to === t)
      .map(e => parseEdge(e));
  });

  /**
   * Columns for the selected table: prefers the /columns endpoint output (full
   * column list with ordinal + data type). Falls back to inferring PK/FK columns
   * from edge labels when the endpoint is unavailable.
   */
  columnsTable = computed<ColumnInfo[]>(() => {
    const t = this.tableName();
    if (!t) return [];

    const allCols = this.cols()?.columns;
    if (allCols && allCols.length) {
      return allCols
        .filter(c => c.table === t)
        .slice()
        .sort((a, b) => a.ordinal - b.ordinal);
    }

    // Fallback: infer columns from edges.
    const g = this.graph();
    if (!g) return [];
    const pkSet = new Set<string>();
    const fkSet = new Set<string>();
    for (const e of g.edges) {
      const p = parseEdge(e);
      if (e.from === t) fkSet.add(p.childCol);
      if (e.to === t) pkSet.add(p.parentCol);
    }
    return [...new Set([...pkSet, ...fkSet])]
      .sort()
      .map((name, i) => ({
        table: t,
        column: name,
        ordinal: i + 1,
        data_type: '?',
        is_pk: pkSet.has(name),
        is_fk: fkSet.has(name),
      }));
  });

  piiForTable = computed(() => {
    const t = this.tableName();
    if (!t) return [];
    return this.pii().filter(p => p.table_name === t);
  });

  constructor() { /* nothing */ }

  ngOnInit(): void {
    // jobId is `input.required` so it's set by mount time. Loading from
    // ngOnInit (instead of an effect) avoids NG0600 — the load() body writes
    // signals (loading/error/graph/cols/pii), which is illegal inside effect()
    // without allowSignalWrites.
    const id = this.jobId();
    if (id) this.load(id);
  }
  ngOnDestroy(): void { /* nothing to dispose */ }

  onPick(name: string | null): void {
    this.jobsSvc.selectedTable.set(name && name !== 'null' ? name : null);
  }

  close(): void {
    this.jobsSvc.selectedTable.set(null);
  }

  sample(f: PiiFinding): string {
    if (!Array.isArray(f.redacted_examples) || f.redacted_examples.length === 0) {
      return '';
    }
    return f.redacted_examples.slice(0, 3).map(String).join(', ');
  }

  cardLabel(c: string | null | undefined): string {
    if (!c) return '—';
    switch (c) {
      case 'ONE_TO_ONE': return '1:1';
      case 'ONE_TO_MANY': return '1:N';
      case 'MANY_TO_ONE': return 'N:1';
      case 'MANY_TO_MANY': return 'N:M';
      default: return c;
    }
  }

  private load(jobId: string): void {
    this.loading.set(true);
    this.error.set(null);
    let pending = 3;
    const done = () => { if (--pending === 0) this.loading.set(false); };
    this.jobsSvc.relationships(jobId, 1000).subscribe({
      next: g => { this.graph.set(g); done(); },
      error: err => {
        this.error.set(err?.error?.detail ?? err?.message ?? 'Failed to load relationships.');
        done();
      },
    });
    this.jobsSvc.pii(jobId).subscribe({
      next: r => { this.pii.set(r.findings); done(); },
      error: () => { done(); },
    });
    this.jobsSvc.columns(jobId).pipe(catchError(() => of(null))).subscribe(r => {
      this.cols.set(r);
      this.columnsFromEndpoint.set(r != null);
      done();
    });
  }
}

/**
 * Parse the edge.label produced by the API ("child_col → parent_col") into
 * structured fields. Handles the U+2192 arrow as well as a plain "->" fallback.
 */
function parseEdge(e: RelationshipEdge): {
  childTable: string;
  childCol: string;
  parentTable: string;
  parentCol: string;
  confidence: number | null;
  cardinality: string | null;
} {
  const sep = e.label.includes('→') ? '→' : '->';
  const [c, p] = e.label.split(sep).map(s => s.trim());
  return {
    childTable: e.from,
    childCol: c ?? '?',
    parentTable: e.to,
    parentCol: p ?? '?',
    confidence: e.confidence,
    cardinality: e.cardinality ?? null,
  };
}
