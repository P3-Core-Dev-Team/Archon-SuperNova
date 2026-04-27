// dbdiagram.io-style ERD "card" view (B2).
//
// One card per table; each card lists columns with PK/FK badges; FK
// relationships are drawn as SVG paths whose endpoints attach to the
// individual column rows (not the table card edge), matching the
// rendering used by tools like dbdiagram.io and DBeaver.
//
// Rendering pipeline:
//   1. JobService.relationships()  -> nodes + edges (label encodes
//      "child_col -> parent_col" using U+2192).
//   2. JobService.columns()        -> per-table column inventory with
//      PK/FK flags. Falls back to deriving columns from edge labels
//      if the endpoint 404s (uvicorn hasn't restarted yet).
//   3. Cards are positioned in a manual 4-column grid inside an
//      overflow-auto canvas (no force-directed solve -- intentional;
//      keeps the bundle lean and the layout stable across reloads).
//   4. After view init we use getBoundingClientRect on each
//      .column-row to compute SVG endpoints. ResizeObserver +
//      scroll handler keep them aligned after layout reflow.
//
// Standalone Angular 17 component, no third-party deps.
import {
  AfterViewInit, Component, ElementRef, OnDestroy,
  ViewChild, inject, input, signal, computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { JobService } from '../../services/job.service';
import {
  ColumnInfo,
  RelationshipEdge,
  RelationshipNode,
} from '../../models/job.model';

/** A column row as we render it -- one per `.column-row` DOM element. */
interface CardColumn {
  name: string;
  dataType?: string;
  isPk: boolean;
  isFk: boolean;
  // populated dynamically as we infer columns from edges
  inferred: boolean;
}

/** A table card (one per table). */
interface Card {
  table: string;
  // grid coords (col, row) within the canvas grid -- 4 cards/row.
  gridCol: number;
  gridRow: number;
  columns: CardColumn[];
  /** True when we have NO column info -- shows "(columns unknown)" placeholder. */
  unknown: boolean;
}

/** Geometric description of an SVG edge between two column rows. */
interface EdgeLine {
  edgeIdx: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  // routing controls -- bezier handle offsets pulled out for the path "d"
  c1x: number;
  c2x: number;
  color: string;
  fromTable: string;
  toTable: string;
  label: string;
  confidence: number | null;
}

@Component({
  selector: 'app-erd-card',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <div class="erd-toolbar">
      <span class="muted">Schema: <strong>{{ schema() }}</strong></span>
      <span class="muted">Tables: <strong>{{ cards().length }}</strong></span>
      <span class="muted">Edges: <strong>{{ allEdges().length }}</strong></span>
      @if (columnsUnavailable()) {
        <span class="warn">columns endpoint unavailable -- inferred from edges only</span>
      }
      <span class="spacer"></span>
      <a [routerLink]="['/jobs', jobId()]" class="back">back to graph view</a>
    </div>

    @if (loadError()) {
      <div class="error card">{{ loadError() }}</div>
    }

    @if (loading()) {
      <p class="muted">Loading ERD…</p>
    }

    @if (!loading() && !loadError() && cards().length === 0) {
      <div class="empty">
        @if (allEdges().length === 0) {
          No tables or relationships discovered for this job.
        } @else {
          No tables to display.
        }
      </div>
    }

    @if (!loading() && cards().length > 0) {
      <div #canvas class="erd-canvas" (scroll)="recomputeEdges()">
        <!-- One card per table; columns rendered as rows. Cards are flexed in a
             grid via flex-wrap; we do NOT use absolute positioning so the
             browser handles reflow on resize, and we just measure post-layout. -->
        <div class="erd-grid">
          @for (c of cards(); track c.table) {
            <div class="erd-card"
                 [class.dim]="hoveredTable() && !isRelated(c.table)"
                 [class.bridge]="isBridge(c.table)"
                 [style.borderColor]="bridgeColorFor(c.table) || null"
                 [attr.data-table]="c.table"
                 (mouseenter)="hoveredTable.set(c.table)"
                 (mouseleave)="hoveredTable.set(null)">
              <div class="erd-card-header"
                   [style.background]="bridgeColorFor(c.table) || null"
                   [style.color]="bridgeColorFor(c.table) ? '#0d1117' : null">
                @if (isBridge(c.table)) { <span class="bridge-pin" title="Cross-cluster bridge">⤴</span> }
                {{ c.table }}
              </div>
              <div class="erd-card-body">
                @if (c.unknown) {
                  <div class="column-row unknown">(columns unknown)</div>
                } @else {
                  @for (col of c.columns; track col.name) {
                    <div class="column-row"
                         [attr.data-table]="c.table"
                         [attr.data-column]="col.name">
                      <span class="badges">
                        @if (col.isPk) {
                          <span class="badge pk" title="Primary key">PK</span>
                        }
                        @if (col.isFk) {
                          <span class="badge fk" title="Foreign key">FK</span>
                        }
                        @if (!col.isPk && !col.isFk) {
                          <span class="badge none">&nbsp;</span>
                        }
                      </span>
                      <span class="col-name">{{ col.name }}</span>
                      @if (col.dataType) {
                        <span class="col-type">{{ col.dataType }}</span>
                      }
                    </div>
                  }
                }
              </div>
            </div>
          }
        </div>
        <!-- SVG layer overlaid on the grid. pointer-events: none so the cards
             remain hoverable/clickable. Sized to match the grid's scrollable
             dimensions so bezier paths line up across the entire canvas. -->
        <svg class="erd-svg"
             [attr.width]="svgWidth()" [attr.height]="svgHeight()">
          @for (e of edgeLines(); track e.edgeIdx) {
            <g [class.dim]="hoveredTable() && !edgeTouches(e, hoveredTable()!)">
              <path
                [attr.d]="bezier(e)"
                [attr.stroke]="e.color"
                fill="none"
                stroke-width="1.6">
              </path>
              <!-- FK side dot (child / "from") -->
              <circle [attr.cx]="e.x1" [attr.cy]="e.y1" r="3.2"
                      [attr.fill]="e.color"></circle>
              <!-- PK side arrow head -->
              <polygon
                [attr.points]="arrowHead(e)"
                [attr.fill]="e.color">
              </polygon>
            </g>
          }
        </svg>
      </div>

      <div class="legend">
        <div class="legend-row"><span class="swatch" style="background:#3fb950"></span> &ge; 0.95</div>
        <div class="legend-row"><span class="swatch" style="background:#d29922"></span> 0.85 &ndash; 0.95</div>
        <div class="legend-row"><span class="swatch" style="background:#8b949e"></span> &lt; 0.85</div>
        <div class="legend-row"><span class="badge pk">PK</span> primary key</div>
        <div class="legend-row"><span class="badge fk">FK</span> foreign key</div>
      </div>
    }
  `,
  styles: [`
    :host {
      display: block;
      color: #e6edf3;
      font-size: 13px;
    }
    .erd-toolbar {
      display: flex;
      gap: 18px;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .erd-toolbar .spacer { flex: 1; }
    .muted { color: #8b949e; }
    .warn {
      color: #d29922;
      background: rgba(210, 153, 34, 0.12);
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
    }
    .back {
      color: #58a6ff;
      text-decoration: none;
      font-size: 13px;
    }
    .back:hover { text-decoration: underline; }
    .error {
      color: #ffabab;
      background: #3a0d0d;
      border: 1px solid #f85149;
      border-radius: 6px;
      padding: 12px;
    }
    .empty {
      padding: 32px;
      text-align: center;
      color: #8b949e;
      border: 1px dashed #30363d;
      border-radius: 6px;
    }
    .erd-canvas {
      position: relative;
      width: 100%;
      max-height: 720px;
      overflow: auto;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 18px;
      /* Don't let card hover styles bleed -- isolate stacking. */
      isolation: isolate;
    }
    .erd-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(220px, 1fr));
      gap: 60px 32px;
      position: relative;
      z-index: 1;
    }
    .erd-card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      overflow: hidden;
      transition: opacity 0.15s ease, border-color 0.15s ease;
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
    }
    .erd-card.dim { opacity: 0.3; }
    /* Cross-cluster bridge ("super-point") cards: dashed amber outline,
       muted background — visually distinct from cluster members. */
    .erd-card.bridge {
      border-style: dashed;
      border-color: #d29922;
      background: #181308;
    }
    .erd-card.bridge .erd-card-header {
      background: #2a210b;
      color: #e3b341;
    }
    .bridge-pin {
      display: inline-block;
      margin-right: 4px;
      color: #d29922;
      font-weight: 600;
    }
    .erd-card-header {
      background: #1f6feb;
      color: #fff;
      padding: 8px 12px;
      font-weight: 600;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
      border-bottom: 1px solid #30363d;
      letter-spacing: 0.2px;
    }
    .erd-card-body {
      display: flex;
      flex-direction: column;
    }
    .column-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 5px 12px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
      font-size: 12px;
      border-bottom: 1px solid #21262d;
      min-height: 24px;
    }
    .column-row:last-child { border-bottom: none; }
    .column-row.unknown {
      color: #8b949e;
      font-style: italic;
      justify-content: center;
    }
    .badges {
      flex: 0 0 auto;
      display: inline-flex;
      gap: 2px;
      width: 56px;
    }
    .badge {
      font-size: 10px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: 3px;
      letter-spacing: 0.5px;
      text-align: center;
      min-width: 22px;
    }
    .badge.pk { background: #d29922; color: #0d1117; }
    .badge.fk { background: #1f6feb; color: #fff; }
    .badge.none { background: transparent; }
    .col-name {
      flex: 1 1 auto;
      color: #e6edf3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .col-type {
      flex: 0 0 auto;
      color: #6e7681;
      font-size: 11px;
    }
    .erd-svg {
      position: absolute;
      top: 0;
      left: 0;
      pointer-events: none;
      z-index: 2;
    }
    .erd-svg g { transition: opacity 0.15s ease; }
    .erd-svg g.dim { opacity: 0.15; }
    .legend {
      margin-top: 10px;
      display: flex;
      gap: 18px;
      flex-wrap: wrap;
      font-size: 11px;
      color: #8b949e;
    }
    .legend-row { display: inline-flex; align-items: center; gap: 6px; }
    .swatch {
      display: inline-block;
      width: 14px;
      height: 3px;
      border-radius: 2px;
    }
  `],
})
export class ErdCardComponent implements AfterViewInit, OnDestroy {
  jobId = input.required<string>();

  /** Optional filter: when set, only render cards for these table names AND
   *  edges whose endpoints are BOTH in the set.  Used by the cluster-detail
   *  page to render a per-cluster ERD without forking the component.
   *  Pass an empty array (default) for the full-schema view. */
  filterTableNames = input<string[]>([]);

  /** Cross-cluster "super-point" tables — outside the cluster but linked to
   *  a member.  Rendered as outline-only ghost cards so the user sees where
   *  this cluster connects out.  Empty array = no bridge cards. */
  bridgeTableNames = input<string[]>([]);

  /** Optional per-table color override (table_name → CSS color string).
   *  Used for bridges to color them by the cluster they belong to,
   *  matching the macro cluster-graph palette. */
  bridgeColors = input<Record<string, string>>({});

  @ViewChild('canvas') canvas?: ElementRef<HTMLDivElement>;

  private jobsSvc = inject(JobService);

  schema = signal('');
  loading = signal(true);
  loadError = signal<string | null>(null);
  /** True when /columns 404'd -- we fall back to edge-only inference. */
  columnsUnavailable = signal(false);

  cards = signal<Card[]>([]);
  allEdges = signal<RelationshipEdge[]>([]);
  edgeLines = signal<EdgeLine[]>([]);

  hoveredTable = signal<string | null>(null);

  /** Scrollable canvas dims -- match the grid's bounding rect so SVG covers it. */
  svgWidth = signal(0);
  svgHeight = signal(0);

  /** Tables connected to the hovered table (used for dimming non-related cards). */
  private relatedCache = computed(() => {
    const t = this.hoveredTable();
    if (!t) return new Set<string>();
    const set = new Set<string>([t]);
    for (const e of this.allEdges()) {
      if (e.from === t) set.add(e.to);
      if (e.to === t) set.add(e.from);
    }
    return set;
  });

  private resizeObs?: ResizeObserver;
  private windowResizeHandler = () => this.recomputeEdges();
  private rafId = 0;

  ngAfterViewInit(): void {
    this.load();
    window.addEventListener('resize', this.windowResizeHandler);
  }

  ngOnDestroy(): void {
    this.resizeObs?.disconnect();
    window.removeEventListener('resize', this.windowResizeHandler);
    if (this.rafId) cancelAnimationFrame(this.rafId);
  }

  isRelated(table: string): boolean {
    return this.relatedCache().has(table);
  }

  /** True if this table is a cross-cluster bridge (super-point). */
  isBridge(table: string): boolean {
    const b = this.bridgeTableNames();
    return Array.isArray(b) && b.includes(table);
  }

  /** Color override for bridge cards (matches macro cluster-graph). */
  bridgeColorFor(table: string): string | null {
    if (!this.isBridge(table)) return null;
    return this.bridgeColors()[table] ?? null;
  }

  /** True when this edge touches the given table (for dimming). */
  edgeTouches(e: EdgeLine, table: string): boolean {
    return e.fromTable === table || e.toTable === table;
  }

  private load(): void {
    this.loading.set(true);
    this.loadError.set(null);
    this.jobsSvc.relationships(this.jobId(), 1000).subscribe({
      next: g => {
        this.schema.set(g.schema);
        this.allEdges.set(g.edges);
        // Fetch column inventory; on 404, derive columns from edge labels.
        this.jobsSvc.columns(this.jobId()).subscribe({
          next: c => {
            this.columnsUnavailable.set(false);
            this.buildCards(g.nodes, g.edges, c.columns, c.tables);
            this.loading.set(false);
            this.scheduleRecompute();
          },
          error: () => {
            this.columnsUnavailable.set(true);
            this.buildCards(g.nodes, g.edges, [], []);
            this.loading.set(false);
            this.scheduleRecompute();
          },
        });
      },
      error: err => {
        this.loading.set(false);
        this.loadError.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load ERD data.',
        );
      },
    });
  }

  /**
   * Build cards from (preferred) the columns endpoint, falling back to
   * inferring columns from edge labels of the form "child_col → parent_col".
   *
   * Tables seen in edges or nodes but not in `columnsByTable` get an "unknown"
   * card -- happens when /columns isn't available.
   */
  private buildCards(
    nodes: RelationshipNode[],
    edges: RelationshipEdge[],
    columns: ColumnInfo[],
    tables: string[],
  ): void {
    // Optional cluster-scope filter: drop everything outside the cluster
    // EXCEPT bridge tables — those are rendered as ghost cards so the user
    // sees how this cluster connects to neighbours.
    const filt = this.filterTableNames();
    const bridges = new Set(this.bridgeTableNames() ?? []);
    if (filt && filt.length > 0) {
      const allow = new Set([...filt, ...bridges]);
      nodes  = nodes.filter(n => allow.has(n.id));
      // Keep edges where AT LEAST one endpoint is a member; the other can
      // be a member OR a bridge.  Drop bridge↔bridge.
      const member = new Set(filt);
      edges  = edges.filter(e =>
        (member.has(e.from) || member.has(e.to)) &&
        allow.has(e.from) && allow.has(e.to)
      );
      columns = columns.filter(c => allow.has(c.table));
      tables = tables.filter(t => allow.has(t));
    }
    const byTable = new Map<string, CardColumn[]>();
    for (const ci of columns) {
      const arr = byTable.get(ci.table) ?? [];
      arr.push({
        name: ci.column,
        dataType: ci.data_type,
        isPk: ci.is_pk,
        isFk: ci.is_fk,
        inferred: false,
      });
      byTable.set(ci.table, arr);
    }

    // Tables we know exist: union of (node ids) U (column endpoint tables) U
    // (edge endpoints).
    const allTables = new Set<string>();
    for (const t of tables) allTables.add(t);
    for (const n of nodes) allTables.add(n.id);
    for (const e of edges) {
      allTables.add(e.from);
      allTables.add(e.to);
    }

    // Fallback: parse edge labels to populate columns when /columns 404'd
    // OR when the endpoint returned no columns for some table.
    if (columns.length === 0) {
      for (const e of edges) {
        const [childCol, parentCol] = this.parseEdgeLabel(e.label);
        if (childCol) this.addInferred(byTable, e.from, childCol, true, false);
        if (parentCol) this.addInferred(byTable, e.to, parentCol, false, true);
      }
    } else {
      // Even with the endpoint, the label may surface columns not in the
      // returned set if the endpoint and graph are slightly out of sync.
      // Keep the loop additive (only adds missing names).
      for (const e of edges) {
        const [childCol, parentCol] = this.parseEdgeLabel(e.label);
        if (childCol) this.addInferred(byTable, e.from, childCol, true, false);
        if (parentCol) this.addInferred(byTable, e.to, parentCol, false, true);
      }
    }

    const sortedTables = Array.from(allTables).sort();
    const cards: Card[] = sortedTables.map((t, i) => {
      const cols = byTable.get(t) ?? [];
      // Sort: PK first, then FK, then alpha.
      cols.sort((a, b) => {
        if (a.isPk !== b.isPk) return a.isPk ? -1 : 1;
        if (a.isFk !== b.isFk) return a.isFk ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      return {
        table: t,
        gridCol: i % 4,
        gridRow: Math.floor(i / 4),
        columns: cols,
        unknown: cols.length === 0,
      };
    });
    this.cards.set(cards);
  }

  /** Adds a column to the table's row list if it isn't already there. */
  private addInferred(
    byTable: Map<string, CardColumn[]>,
    table: string,
    col: string,
    isFk: boolean,
    isPk: boolean,
  ): void {
    const arr = byTable.get(table) ?? [];
    const existing = arr.find(c => c.name === col);
    if (existing) {
      // Promote flags if we already have the column.
      existing.isFk = existing.isFk || isFk;
      existing.isPk = existing.isPk || isPk;
      return;
    }
    arr.push({
      name: col,
      isFk,
      isPk,
      inferred: true,
    });
    byTable.set(table, arr);
  }

  /**
   * Parse an edge label of the form "child_col -> parent_col" (the U+2192
   * arrow used by /api/jobs/{id}/relationships). Returns [child, parent] or
   * [null, null] if the label doesn't match the pattern.
   */
  private parseEdgeLabel(label: string): [string | null, string | null] {
    if (!label) return [null, null];
    // U+2192 first; ASCII -> as a permissive fallback.
    const arrow = label.includes('→') ? '→' : '->';
    const idx = label.indexOf(arrow);
    if (idx < 0) return [null, null];
    const child = label.slice(0, idx).trim();
    const parent = label.slice(idx + arrow.length).trim();
    return [child || null, parent || null];
  }

  /** Schedule recompute on next frame after Angular finishes rendering. */
  private scheduleRecompute(): void {
    if (this.rafId) cancelAnimationFrame(this.rafId);
    this.rafId = requestAnimationFrame(() => {
      this.recomputeEdges();
      // Set up ResizeObserver only once we know the canvas exists. Watching
      // the canvas itself catches grid reflow when window resizes shrink the
      // canvas width and force more rows.
      if (this.canvas && !this.resizeObs) {
        this.resizeObs = new ResizeObserver(() => this.recomputeEdges());
        this.resizeObs.observe(this.canvas.nativeElement);
      }
    });
  }

  /**
   * Walk every edge, resolve the child- and parent-column DOM rows, and
   * compute the bezier endpoints relative to the canvas (NOT viewport).
   *
   * Coordinate system: SVG is positioned absolute inside `.erd-canvas`,
   * which has overflow:auto. We use the canvas's scrollable content as the
   * reference frame, so we add `scrollLeft`/`scrollTop` to the offsets we
   * derive from getBoundingClientRect.
   */
  recomputeEdges(): void {
    if (!this.canvas) return;
    const canvas = this.canvas.nativeElement;
    const rect = canvas.getBoundingClientRect();
    const scrollX = canvas.scrollLeft;
    const scrollY = canvas.scrollTop;
    // Make the SVG cover the full scrollable content area.
    this.svgWidth.set(canvas.scrollWidth);
    this.svgHeight.set(canvas.scrollHeight);

    const lines: EdgeLine[] = [];
    const edges = this.allEdges();
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const [childCol, parentCol] = this.parseEdgeLabel(e.label);
      if (!childCol || !parentCol) continue;
      const fromRow = canvas.querySelector<HTMLElement>(
        `.column-row[data-table="${cssEscape(e.from)}"][data-column="${cssEscape(childCol)}"]`,
      );
      const toRow = canvas.querySelector<HTMLElement>(
        `.column-row[data-table="${cssEscape(e.to)}"][data-column="${cssEscape(parentCol)}"]`,
      );
      if (!fromRow || !toRow) continue;
      const fr = fromRow.getBoundingClientRect();
      const tr = toRow.getBoundingClientRect();
      // Decide which side of each card to anchor on -- pick whichever side
      // is closer to the other endpoint, so lines avoid crossing through
      // their own card.
      const fromMidY = fr.top + fr.height / 2 - rect.top + scrollY;
      const toMidY = tr.top + tr.height / 2 - rect.top + scrollY;
      const fromLeftX = fr.left - rect.left + scrollX;
      const fromRightX = fr.right - rect.left + scrollX;
      const toLeftX = tr.left - rect.left + scrollX;
      const toRightX = tr.right - rect.left + scrollX;
      // Anchor on whichever side is closest to the other card.
      const fromCardCenter = (fromLeftX + fromRightX) / 2;
      const toCardCenter = (toLeftX + toRightX) / 2;
      const fromOnRight = fromCardCenter < toCardCenter;
      const x1 = fromOnRight ? fromRightX : fromLeftX;
      const x2 = fromOnRight ? toLeftX : toRightX;
      // Bezier control offset proportional to horizontal distance, but
      // capped so very-close cards don't get crazy curves.
      const dx = Math.max(40, Math.min(160, Math.abs(x2 - x1) / 2));
      const c1x = fromOnRight ? x1 + dx : x1 - dx;
      const c2x = fromOnRight ? x2 - dx : x2 + dx;
      lines.push({
        edgeIdx: i,
        x1, y1: fromMidY,
        x2, y2: toMidY,
        c1x, c2x,
        color: this.colorFor(e.confidence),
        fromTable: e.from,
        toTable: e.to,
        label: e.label,
        confidence: e.confidence,
      });
    }
    this.edgeLines.set(lines);
  }

  /** Build the bezier path "d" attribute. */
  bezier(e: EdgeLine): string {
    return `M ${e.x1} ${e.y1} C ${e.c1x} ${e.y1}, ${e.c2x} ${e.y2}, ${e.x2} ${e.y2}`;
  }

  /** Triangle arrowhead at the parent (PK) endpoint. */
  arrowHead(e: EdgeLine): string {
    // Determine direction: the arrow points INTO the PK side. If x2 > x1
    // the arrow points right; otherwise left.
    const dir = e.x2 > e.x1 ? -1 : 1;
    const tipX = e.x2;
    const tipY = e.y2;
    const baseX = tipX + dir * 8;
    return `${tipX},${tipY} ${baseX},${tipY - 4} ${baseX},${tipY + 4}`;
  }

  private colorFor(c: number | null): string {
    if (c == null) return '#666';
    if (c >= 0.95) return '#3fb950';
    if (c >= 0.85) return '#d29922';
    return '#8b949e';
  }
}

/**
 * Minimal CSS.escape polyfill -- table/column names may contain characters
 * (dots, dashes) that need escaping in attribute selectors. We use the
 * native CSS.escape when available and fall back to a regex.
 */
function cssEscape(value: string): string {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(value);
  return value.replace(/[^a-zA-Z0-9_-]/g, ch => `\\${ch}`);
}
