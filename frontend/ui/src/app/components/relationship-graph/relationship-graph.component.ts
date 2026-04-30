import {
  AfterViewInit, Component, ElementRef, OnChanges, OnDestroy,
  ViewChild, computed, inject, input, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import * as dagre from '@dagrejs/dagre';
import { JobService } from '../../services/job.service';
import {
  ColumnInfo,
  RelationshipEdge,
  RelationshipNode,
} from '../../models/job.model';

type RelType = 'header_item' | 'master_lookup' | 'config' | 'text' | 'history';

interface ColumnRow {
  name: string;
  dataType: string;
  typeGlyph: string;       // single-char abbreviation (i / c / t / # / b / ·)
  isPk: boolean;
  isFk: boolean;
}

interface CardNode {
  id: string;            // table name
  label: string;
  rows: number;
  fieldCount: number;    // edge degree
  module: string | null;
  columns: ColumnRow[];  // FULL column list (DbSchema-style detailed card)
  width: number;
  height: number;        // dynamic = HEADER_H + columns.length * ROW_H
  x: number;             // top-left after layout (auto + drag offset)
  y: number;
}

interface EdgeRoute {
  id: string;
  type: RelType;
  color: string;
  raw: RelationshipEdge;
  // Cubic bezier path "M x0 y0 C cx0 cy0 cx1 cy1 x1 y1"
  path: string;
  // Endpoint coords for cardinality glyph placement.
  fromX: number; fromY: number; toX: number; toY: number;
  fromOnRight: boolean; toOnRight: boolean;
  fromGlyph: string;
  toGlyph: string;
  fromGlyphX: number;
  fromGlyphY: number;
  toGlyphX: number;
  toGlyphY: number;
  // Tooltip text (rendered via <title> on the path).
  tooltip: string;
  // For dim-others-on-hover.
  fromTable: string;
  toTable: string;
}

const CARD_W = 240;
const HEADER_H = 32;
const ROW_H = 22;
// Floor on card height so empty/columnless tables still get a usable bbox
// to drag and route edges through (keeps the layout from collapsing).
const MIN_CARD_H = HEADER_H + ROW_H;
const RANK_SEP = 90;        // vertical gap between dagre ranks
const NODE_SEP = 36;        // horizontal gap within a rank

@Component({
  selector: 'app-relationship-graph',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <div class="toolbar">
      <span class="muted">Schema: <strong>{{ schema() }}</strong></span>
      <span class="muted">Tables: <strong>{{ totalTables() }}</strong></span>
      <span class="counter"
            [title]="'Showing ' + visibleEdgeCount() + ' of ' + totalEdges() + ' relationships at confidence ≥ ' + (minConfidence() | number:'1.2-2')">
        <strong class="big">{{ visibleEdgeCount() }}</strong>
        <span class="muted">of {{ totalEdges() }} relationships</span>
      </span>
      <label class="slider-label">
        Min confidence:
        <input type="range" min="0" max="1" step="0.05"
               [value]="minConfidence()"
               (input)="onConfidenceChange($event)" />
        <span class="mono">{{ minConfidence() | number:'1.2-2' }}</span>
      </label>
      <button type="button" (click)="toggleDirection()"
              [class.active]="layoutDir() === 'LR'"
              title="Switch dagre rank direction (top-down vs left-to-right)">
        Layout: <strong>{{ layoutDir() === 'TB' ? 'top-down' : 'left-right' }}</strong>
      </button>
      <button type="button" (click)="fitToScreen()">Fit to screen</button>
      <button type="button" (click)="resetZoom()">Reset zoom</button>
    </div>

    <div class="graph-wrap"
         #wrap
         (wheel)="onWheel($event)"
         (mousedown)="onMouseDown($event)"
         (mousemove)="onMouseMove($event)"
         (mouseup)="onMouseUp($event)"
         (mouseleave)="onMouseUp($event)">
      <div class="canvas"
           [style.transform]="canvasTransform()"
           [style.width.px]="contentSize().w"
           [style.height.px]="contentSize().h">
        <!-- SVG layer for edges sits BEHIND the cards (lower z-index). -->
        <svg class="edges"
             [attr.width]="contentSize().w"
             [attr.height]="contentSize().h"
             xmlns="http://www.w3.org/2000/svg">
          @for (e of routes(); track e.id) {
            <g [attr.data-edge]="e.id"
               [class.edge-selected]="selectedEdgeId() === e.id"
               [class.edge-hover]="hoveredEdgeId() === e.id"
               [class.edge-dim]="(hoveredEdgeId() && hoveredEdgeId() !== e.id) ||
                                 (hoveredCardId() && !isEdgeAdjacentToHover(e))"
               class="edge-group"
               (click)="onEdgeClick(e)"
               (mouseenter)="hoveredEdgeId.set(e.id)"
               (mouseleave)="hoveredEdgeId.set(null)">
              <!-- Wide invisible stroke catches mouse events so thin
                   edges remain easy to hover. -->
              <path class="edge-hit"
                    [attr.d]="e.path" />
              <path class="edge"
                    [attr.d]="e.path"
                    [attr.stroke]="e.color">
                <title>{{ e.tooltip }}</title>
              </path>
              <!-- Cardinality glyph at FROM endpoint -->
              <text class="card-glyph"
                    [attr.x]="e.fromGlyphX"
                    [attr.y]="e.fromGlyphY"
                    [attr.fill]="e.color">{{ e.fromGlyph }}</text>
              <!-- Cardinality glyph at TO endpoint -->
              <text class="card-glyph"
                    [attr.x]="e.toGlyphX"
                    [attr.y]="e.toGlyphY"
                    [attr.fill]="e.color">{{ e.toGlyph }}</text>
            </g>
          }
        </svg>

        <!-- HTML card layer: full column list (DbSchema-style). -->
        @for (n of cards(); track n.id) {
          <div class="card-node"
               [class.selected]="selectedCardId() === n.id"
               [class.dragging]="cardDrag?.cardId === n.id && cardDrag?.hasMoved"
               [class.dim]="hoveredCardId() && hoveredCardId() !== n.id && !isCardAdjacentToHover(n.id)"
               [style.left.px]="n.x"
               [style.top.px]="n.y"
               [style.width.px]="n.width"
               [style.height.px]="n.height"
               (mouseenter)="hoveredCardId.set(n.id)"
               (mouseleave)="hoveredCardId.set(null)">
            <!-- HEADER (table-name strip) — the ONLY draggable surface.
                 Click without drag promotes to focal + switches to map. -->
            <div class="card-head"
                 (mousedown)="onHeaderMouseDown($event, n)">
              <span class="card-table mono">{{ n.label }}</span>
              @if (n.module) {
                <span class="card-module">{{ n.module }}</span>
              }
            </div>
            <!-- COLUMN ROWS — column rows do NOT start a drag.  Mouse-down
                 stops propagation so the wrapping graph-wrap doesn't
                 mistake it for a pan-start either. -->
            <div class="card-cols">
              @for (c of n.columns; track c.name) {
                <div class="col-row"
                     [class.col-pk]="c.isPk"
                     [class.col-fk]="c.isFk"
                     (mousedown)="$event.stopPropagation()">
                  <span class="col-name mono">{{ c.name }}</span>
                  <span class="col-type" [title]="c.dataType">{{ c.typeGlyph }}</span>
                  <span class="col-key">
                    @if (c.isPk) {
                      <span class="key-pk" title="Primary key">pk</span>
                    } @else if (c.isFk) {
                      <span class="key-fk" title="Foreign key">fk</span>
                    }
                  </span>
                </div>
              }
              @if (n.columns.length === 0) {
                <div class="col-row col-empty muted">— no columns inventoried —</div>
              }
            </div>
          </div>
        }
      </div>

      <!-- Floating relationship-type legend -->
      <div class="legend">
        <div class="legend-title">Relationship</div>
        <div class="legend-row"><span class="swatch" style="background:#58a6ff"></span> header / item</div>
        <div class="legend-row"><span class="swatch" style="background:#3fb950"></span> master lookup</div>
        <div class="legend-row"><span class="swatch" style="background:#bc8cff"></span> config</div>
        <div class="legend-row"><span class="swatch" style="background:#d29922"></span> text</div>
        <div class="legend-row"><span class="swatch" style="background:#8b949e"></span> history</div>
        <div class="legend-divider"></div>
        <div class="legend-title">Cardinality</div>
        <div class="legend-row"><span class="card-glyph-leg">|—|</span> 1:1</div>
        <div class="legend-row"><span class="card-glyph-leg">|—&lt;</span> 1:N</div>
        <div class="legend-row"><span class="card-glyph-leg">&gt;—|</span> N:1</div>
        <div class="legend-row"><span class="card-glyph-leg">&gt;—&lt;</span> N:M</div>
      </div>

      @if (loading()) {
        <div class="overlay muted">Loading graph…</div>
      }
      @if (!loading() && !error() && totalEdges() === 0) {
        <div class="overlay muted">No relationships discovered yet.</div>
      }
    </div>

    @if (selectedEdge()) {
      <div class="edge-panel card">
        <div class="title">Edge detail</div>
        <div><strong>{{ selectedEdge()!.from }}</strong> → <strong>{{ selectedEdge()!.to }}</strong></div>
        <div class="muted">{{ selectedEdge()!.label }}</div>
        <div>Cardinality: <code>{{ selectedEdge()!.cardinality }}</code></div>
        <div>Containment: {{ selectedEdge()!.containment | number:'1.3-3' }}</div>
        <div>Confidence: {{ selectedEdge()!.confidence | number:'1.3-3' }}</div>
      </div>
    }

    @if (error()) {
      <div class="error">{{ error() }}</div>
    }
  `,
  styles: [`
    .toolbar {
      display: flex;
      gap: 22px;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .toolbar label {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      text-transform: none;
      letter-spacing: 0;
      font-size: 13px;
      color: #e6edf3;
    }
    .counter {
      display: inline-flex;
      align-items: baseline;
      gap: 6px;
      padding: 4px 10px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
    }
    .counter .big {
      font-size: 18px;
      color: #58a6ff;
      font-variant-numeric: tabular-nums;
    }
    .slider-label input[type=range] { width: 160px; }
    .slider-label .mono {
      min-width: 36px;
      text-align: right;
      color: #58a6ff;
      font-weight: 600;
    }
    .toolbar button.active {
      background: #1f6feb;
      border-color: #58a6ff;
      color: #fff;
    }

    .graph-wrap {
      position: relative;
      width: 100%;
      height: 760px;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 8px;
      overflow: hidden;
      cursor: grab;
    }
    .graph-wrap:active { cursor: grabbing; }

    .canvas {
      position: absolute;
      top: 0;
      left: 0;
      transform-origin: 0 0;
    }

    /* Edge SVG sits behind cards. */
    svg.edges {
      position: absolute;
      top: 0;
      left: 0;
      pointer-events: none;
      overflow: visible;
    }
    .edge-group {
      pointer-events: auto;
      cursor: pointer;
      transition: opacity 0.12s;
    }
    .edge-hit {
      fill: none;
      stroke: transparent;
      stroke-width: 14;
      pointer-events: stroke;
    }
    .edge {
      fill: none;
      stroke-width: 1.5;
      transition: stroke-width 0.1s;
      pointer-events: none;
    }
    .edge-group.edge-hover .edge { stroke-width: 3; }
    .edge-group.edge-selected .edge { stroke-width: 3.5; }
    .edge-group.edge-dim { opacity: 0.25; }
    .edge-group.edge-hover { opacity: 1 !important; }

    .card-glyph {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 12px;
      font-weight: 700;
      pointer-events: none;
    }

    /* === Detailed table cards (DbSchema-style) ====================== */
    .card-node {
      position: absolute;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 4px 14px rgba(0, 0, 0, 0.18);
      overflow: hidden;
      user-select: none;
      transition: border-color 0.12s, opacity 0.15s;
    }
    .card-node:hover { border-color: #4a5159; }
    .card-node.selected {
      border-color: #58a6ff;
      border-width: 2px;
    }
    .card-node.dim { opacity: 0.32; }
    .card-node.dragging {
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 8px 26px rgba(0, 0, 0, 0.5);
      transition: none;
    }

    /* Header strip: tinted background, draggable cursor.  Click without
       drag promotes to focal + switches mode toggle to "map". */
    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      height: ${HEADER_H}px;
      padding: 0 10px;
      background: #21262d;
      border-bottom: 1px solid #30363d;
      cursor: grab;
    }
    .card-node.dragging .card-head { cursor: grabbing; }
    .card-table {
      font-weight: 600;
      font-size: 13px;
      color: #e6edf3;
      letter-spacing: -0.2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .card-module {
      flex-shrink: 0;
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      color: #a371f7;
      background: rgba(163, 113, 247, 0.14);
    }

    /* Column rows: tight, scannable, key-marker on the right. */
    .card-cols {
      display: flex;
      flex-direction: column;
    }
    .col-row {
      display: grid;
      grid-template-columns: 1fr auto auto;
      align-items: center;
      gap: 6px;
      height: ${ROW_H}px;
      padding: 0 10px;
      font-size: 12px;
      color: #c9d1d9;
      border-bottom: 1px solid rgba(48, 54, 61, 0.55);
    }
    .col-row:last-child { border-bottom: none; }
    .col-row:hover { background: rgba(88, 166, 255, 0.07); }
    .col-row.col-pk { color: #e6edf3; }
    .col-row.col-fk { color: #c9d1d9; }
    .col-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 12px;
    }
    .col-type {
      color: #6e7681;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 11px;
      width: 10px;
      text-align: center;
      cursor: help;
    }
    .col-key { width: 22px; text-align: right; }
    .col-key .key-pk,
    .col-key .key-fk {
      display: inline-block;
      padding: 0 4px;
      border-radius: 3px;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      line-height: 14px;
    }
    .col-key .key-pk {
      color: #3fb950;
      background: rgba(63, 185, 80, 0.14);
    }
    .col-key .key-fk {
      color: #58a6ff;
      background: rgba(88, 166, 255, 0.14);
    }
    .col-empty {
      color: #6e7681;
      font-size: 11px;
      font-style: italic;
      grid-template-columns: 1fr;
      justify-content: center;
    }

    .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }

    .legend {
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: rgba(13, 17, 23, 0.92);
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 12px;
      pointer-events: none;
    }
    .legend-title {
      font-size: 10px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
      margin-bottom: 4px;
    }
    .legend-row { display: flex; align-items: center; gap: 6px; line-height: 1.6; }
    .swatch {
      display: inline-block;
      width: 14px;
      height: 3px;
      border-radius: 2px;
    }
    .legend-divider {
      height: 1px;
      background: #30363d;
      margin: 6px -2px 4px;
    }
    .card-glyph-leg {
      display: inline-block;
      min-width: 30px;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 11px;
      color: #c9d1d9;
      text-align: center;
      letter-spacing: -1px;
    }
    .overlay {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      pointer-events: none;
    }
    .edge-panel {
      margin-top: 12px;
      max-width: 600px;
    }
    .edge-panel .title {
      font-size: 11px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
      margin-bottom: 6px;
    }
    .error {
      color: #ffabab;
      padding: 12px;
      background: #3a0d0d;
      border: 1px solid #f85149;
      border-radius: 6px;
      margin-top: 8px;
    }
  `],
})
export class RelationshipGraphComponent implements AfterViewInit, OnChanges, OnDestroy {
  jobId = input.required<string>();
  @ViewChild('wrap') wrap!: ElementRef<HTMLDivElement>;

  private jobsSvc = inject(JobService);

  // --- UI state -----------------------------------------------------
  schema = signal('');
  totalTables = signal(0);
  totalEdges = signal(0);
  loading = signal(true);
  error = signal<string | null>(null);
  minConfidence = signal(0);
  layoutDir = signal<'TB' | 'LR'>('TB');
  selectedEdgeId = signal<string | null>(null);
  selectedCardId = signal<string | null>(null);
  hoveredEdgeId = signal<string | null>(null);
  hoveredCardId = signal<string | null>(null);

  selectedEdge = computed<RelationshipEdge | null>(() => {
    const id = this.selectedEdgeId();
    if (!id) return null;
    const r = this.routes().find(x => x.id === id);
    return r ? r.raw : null;
  });

  // --- Pan / zoom state ---------------------------------------------
  zoom = signal(1);
  panX = signal(0);
  panY = signal(0);
  private dragging = false;
  private dragStartX = 0;
  private dragStartY = 0;
  private dragOriginPanX = 0;
  private dragOriginPanY = 0;

  canvasTransform = computed(
    () => `translate(${this.panX()}px, ${this.panY()}px) scale(${this.zoom()})`,
  );

  // --- Card-drag state ---------------------------------------------
  // Parallels the table-card-page map view: header-only mousedown starts
  // a potential drag; the cursor must travel CARD_DRAG_THRESHOLD before
  // we promote it from a click (focal-switch) to a drag.
  cardDrag: {
    cardId: string;
    startCursorX: number;
    startCursorY: number;
    startOffsetX: number;
    startOffsetY: number;
    hasMoved: boolean;
  } | null = null;
  private static readonly CARD_DRAG_THRESHOLD = 5;
  cardOffsets = signal<Record<string, { dx: number; dy: number }>>({});

  // --- Data + layout output -----------------------------------------
  // Source data — unfiltered.
  private allEdges = signal<RelationshipEdge[]>([]);
  private allNodes = signal<RelationshipNode[]>([]);
  /** Per-table column inventory keyed by table name.  Loaded alongside
   * the relationship graph; used to render the column rows + drive
   * column-anchored edge endpoints. */
  private columnsByTable = signal<Map<string, ColumnRow[]>>(new Map());

  /** Pure auto-layout output (dagre-positioned).  Drag offsets layer on
   * top in the public ``cards`` computed below. */
  private layoutCards = signal<CardNode[]>([]);
  /** Cards as actually rendered = auto-layout + per-card drag offsets.
   * Kept reactive so dragging a card causes ``routes`` to re-route the
   * edges live without a manual re-layout pass. */
  cards = computed<CardNode[]>(() => {
    const offsets = this.cardOffsets();
    return this.layoutCards().map(n => {
      const off = offsets[n.id];
      if (!off) return n;
      return { ...n, x: n.x + off.dx, y: n.y + off.dy };
    });
  });

  /** Edge set after the confidence slider's threshold is applied. */
  private filteredEdges = computed<RelationshipEdge[]>(() => {
    const min = this.minConfidence();
    return this.allEdges().filter(e => e.confidence == null || e.confidence >= min);
  });
  /** Visible edge count (derived; never mutated inside a computed). */
  visibleEdgeCount = computed(() => this.filteredEdges().length);

  /** Routes are computed reactively from the visible card set + filtered
   * edges + column inventory.  Re-runs on each drag tick — the math is
   * cheap (O(edges)) for the typical schema sizes (≤300 edges). */
  routes = computed<EdgeRoute[]>(() => {
    const cards = this.cards();
    const byId = new Map(cards.map(c => [c.id, c]));
    const filtered = this.filteredEdges();
    const out: EdgeRoute[] = [];
    filtered.forEach((e, i) => {
      const a = byId.get(e.from);
      const b = byId.get(e.to);
      if (!a || !b) return;
      const { fromCol, toCol } = this.parseEdgeCols(e.label);
      const route = this.buildColumnAnchoredBezier(a, b, fromCol, toCol);
      const type = this.classifyRelType(e);
      const card = e.cardinality;
      const fromGlyph = (card === 'MANY_TO_ONE' || card === 'MANY_TO_MANY') ? '>' : '|';
      const toGlyph   = (card === 'ONE_TO_MANY'  || card === 'MANY_TO_MANY') ? '<' : '|';
      const cardLabel = this.labelFor(card) ?? card ?? 'unknown';
      const conf = e.confidence == null ? '—' : e.confidence.toFixed(2);
      const tooltip =
        `${e.from}.${fromCol || '?'} → ${e.to}.${toCol || '?'} (${cardLabel}, conf ${conf})`;
      out.push({
        id: `e${i}`,
        type,
        color: this.relTypeColor(type),
        raw: e,
        path: route.path,
        fromX: route.fromX, fromY: route.fromY,
        toX: route.toX, toY: route.toY,
        fromOnRight: route.fromOnRight,
        toOnRight: route.toOnRight,
        fromGlyph,
        toGlyph,
        fromGlyphX: route.fromX + (route.fromOnRight ? 4 : -10),
        fromGlyphY: route.fromY + 4,
        toGlyphX:   route.toX   + (route.toOnRight   ? 4 : -10),
        toGlyphY:   route.toY   + 4,
        tooltip,
        fromTable: e.from,
        toTable: e.to,
      });
    });
    return out;
  });

  contentSize = signal<{ w: number; h: number }>({ w: 800, h: 600 });

  // ------------------------------------------------------------------
  ngAfterViewInit(): void { this.load(); }
  ngOnChanges(): void { if (this.wrap) this.load(); }
  ngOnDestroy(): void { /* nothing to dispose */ }

  // --- Toolbar handlers --------------------------------------------------
  onConfidenceChange(ev: Event): void {
    const v = +(ev.target as HTMLInputElement).value;
    this.minConfidence.set(v);
    // Re-run layout so orphaned tables drop out of the canvas + space
    // recompresses around the survivors.
    this.relayout();
  }

  toggleDirection(): void {
    this.layoutDir.set(this.layoutDir() === 'TB' ? 'LR' : 'TB');
    // Layout-direction toggle resets manual drag offsets — otherwise
    // cards land in absurd positions relative to the new arrangement.
    this.cardOffsets.set({});
    this.relayout();
    setTimeout(() => this.fitToScreen(), 0);
  }

  fitToScreen(): void {
    const sz = this.contentSize();
    const wrap = this.wrap?.nativeElement;
    if (!wrap || sz.w === 0 || sz.h === 0) return;
    const ww = wrap.clientWidth;
    const wh = wrap.clientHeight;
    const margin = 40;
    const sx = (ww - margin * 2) / sz.w;
    const sy = (wh - margin * 2) / sz.h;
    const z = Math.min(1, Math.max(0.15, Math.min(sx, sy)));
    this.zoom.set(z);
    this.panX.set((ww - sz.w * z) / 2);
    this.panY.set((wh - sz.h * z) / 2);
  }

  resetZoom(): void {
    this.zoom.set(1);
    this.panX.set(0);
    this.panY.set(0);
  }

  // --- Pan / zoom interaction --------------------------------------------
  onWheel(ev: WheelEvent): void {
    ev.preventDefault();
    const wrap = this.wrap.nativeElement;
    const rect = wrap.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const oldZoom = this.zoom();
    const factor = ev.deltaY > 0 ? 0.9 : 1.1;
    const z = Math.min(2.5, Math.max(0.15, oldZoom * factor));
    const px = this.panX();
    const py = this.panY();
    const cx = (mx - px) / oldZoom;
    const cy = (my - py) / oldZoom;
    this.panX.set(mx - cx * z);
    this.panY.set(my - cy * z);
    this.zoom.set(z);
  }

  /** Pan-drag on empty canvas.  Cards/headers/edges have their own
   * mousedown handlers and stop propagation so they don't bleed in. */
  onMouseDown(ev: MouseEvent): void {
    const target = ev.target as HTMLElement;
    if (target.closest('.card-node') || target.closest('.edge-group')) return;
    this.dragging = true;
    this.dragStartX = ev.clientX;
    this.dragStartY = ev.clientY;
    this.dragOriginPanX = this.panX();
    this.dragOriginPanY = this.panY();
  }
  onMouseMove(ev: MouseEvent): void {
    // Card-drag in progress takes priority — moves the card in canvas
    // coords (so zoom is respected).
    if (this.cardDrag) {
      const z = this.zoom();
      const dxScreen = ev.clientX - this.cardDrag.startCursorX;
      const dyScreen = ev.clientY - this.cardDrag.startCursorY;
      if (!this.cardDrag.hasMoved) {
        if (Math.abs(dxScreen) + Math.abs(dyScreen)
            >= RelationshipGraphComponent.CARD_DRAG_THRESHOLD) {
          this.cardDrag.hasMoved = true;
        } else {
          return;
        }
      }
      const dx = dxScreen / z;
      const dy = dyScreen / z;
      this.cardOffsets.update(cur => ({
        ...cur,
        [this.cardDrag!.cardId]: {
          dx: this.cardDrag!.startOffsetX + dx,
          dy: this.cardDrag!.startOffsetY + dy,
        },
      }));
      return;
    }
    if (!this.dragging) return;
    this.panX.set(this.dragOriginPanX + (ev.clientX - this.dragStartX));
    this.panY.set(this.dragOriginPanY + (ev.clientY - this.dragStartY));
  }
  onMouseUp(_ev: MouseEvent): void {
    // Drop the card.  If the drag never moved, treat it as a click on
    // the header — always promote to focal + switch parent toggle to
    // "map" (no toggle-deselect, per spec).  Re-emit through null first
    // so signal equality doesn't swallow a repeat click on the same
    // header (returning to overview leaves jobsSvc.selectedTable set).
    if (this.cardDrag) {
      if (!this.cardDrag.hasMoved) {
        const id = this.cardDrag.cardId;
        this.selectedCardId.set(id);
        this.jobsSvc.selectedTable.set(null);
        this.jobsSvc.selectedTable.set(id);
      }
      this.cardDrag = null;
    }
    this.dragging = false;
  }

  /** Mouse-down on the card HEADER (table-name strip).  Records the
   * gesture; whether it ends as a click or a drag is decided in
   * onMouseUp / onMouseMove based on cursor travel. */
  onHeaderMouseDown(ev: MouseEvent, n: CardNode): void {
    ev.stopPropagation();
    const off = this.cardOffsets()[n.id] ?? { dx: 0, dy: 0 };
    this.cardDrag = {
      cardId: n.id,
      startCursorX: ev.clientX,
      startCursorY: ev.clientY,
      startOffsetX: off.dx,
      startOffsetY: off.dy,
      hasMoved: false,
    };
  }

  onEdgeClick(e: EdgeRoute): void {
    this.selectedEdgeId.set(this.selectedEdgeId() === e.id ? null : e.id);
  }

  /** True if the given edge has either endpoint on the currently-hovered
   * card.  Used to keep adjacent edges bright while others fade. */
  isEdgeAdjacentToHover(e: EdgeRoute): boolean {
    const h = this.hoveredCardId();
    if (!h) return true;
    return e.fromTable === h || e.toTable === h;
  }
  /** True if the given card shares an edge with the currently-hovered
   * card.  Used to keep neighbour cards full-opacity on card hover. */
  isCardAdjacentToHover(cardId: string): boolean {
    const h = this.hoveredCardId();
    if (!h || h === cardId) return true;
    for (const e of this.routes()) {
      if ((e.fromTable === h && e.toTable === cardId) ||
          (e.toTable   === h && e.fromTable === cardId)) {
        return true;
      }
    }
    return false;
  }

  // --- Data load + layout pipeline ---------------------------------------
  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    forkJoin({
      graph: this.jobsSvc.relationships(this.jobId(), 1500),
      cols:  this.jobsSvc.columns(this.jobId()),
    }).subscribe({
      next: ({ graph, cols }) => {
        this.schema.set(graph.schema);
        this.totalTables.set(graph.total_tables);
        this.totalEdges.set(graph.total_edges);
        this.allEdges.set(graph.edges ?? []);
        this.allNodes.set(graph.nodes ?? []);
        this.columnsByTable.set(this.indexColumns(cols.columns ?? []));
        this.cardOffsets.set({});
        this.loading.set(false);
        // Defer one tick so the DOM container exists for fit-to-screen.
        setTimeout(() => {
          this.relayout();
          this.fitToScreen();
        }, 0);
      },
      error: err => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load relationship graph.',
        );
      },
    });
  }

  /** Bucket the flat ColumnInfo[] by table, sort each bucket by ordinal,
   * and project to ColumnRow (with the type-glyph abbreviation). */
  private indexColumns(cols: ColumnInfo[]): Map<string, ColumnRow[]> {
    const out = new Map<string, ColumnRow[]>();
    for (const c of cols) {
      const arr = out.get(c.table) ?? [];
      arr.push({
        name: c.column,
        dataType: c.data_type,
        typeGlyph: this.typeGlyph(c.data_type),
        isPk: c.is_pk,
        isFk: c.is_fk,
      });
      out.set(c.table, arr);
    }
    for (const arr of out.values()) {
      arr.sort((a, b) => Number(b.isPk) - Number(a.isPk) || a.name.localeCompare(b.name));
    }
    return out;
  }

  private relayout(): void {
    const filtered = this.filteredEdges();

    // Compute degree per node from the FILTERED edge set.
    const degree = new Map<string, number>();
    for (const e of filtered) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }

    const all = this.allNodes();
    const includedNodeIds = filtered.length === 0
      ? new Set(all.map(n => n.id))
      : new Set<string>([
          ...filtered.map(e => e.from),
          ...filtered.map(e => e.to),
        ]);

    const colsByTable = this.columnsByTable();
    const nodes: CardNode[] = all
      .filter(n => includedNodeIds.has(n.id))
      .map(n => {
        const cols = colsByTable.get(n.id) ?? [];
        const height = Math.max(MIN_CARD_H, HEADER_H + cols.length * ROW_H);
        return {
          id: n.id,
          label: n.label,
          rows: n.value ?? 0,
          fieldCount: degree.get(n.id) ?? 0,
          module: this.moduleBadge(n.id),
          columns: cols,
          width: CARD_W,
          height,
          x: 0, y: 0,
        };
      });

    // Run dagre to position cards.  Cards are MUCH taller now (column
    // list inline) so the rank gap was bumped accordingly.
    const g = new dagre.graphlib.Graph<{}>().setGraph({
      rankdir: this.layoutDir(),
      nodesep: NODE_SEP,
      ranksep: RANK_SEP,
      marginx: 24,
      marginy: 24,
    } as any).setDefaultEdgeLabel(() => ({}));

    for (const n of nodes) {
      g.setNode(n.id, { width: n.width, height: n.height });
    }
    for (const e of filtered) {
      if (g.node(e.from) && g.node(e.to)) {
        g.setEdge(e.from, e.to);
      }
    }
    dagre.layout(g);

    const positioned: CardNode[] = nodes.map(n => {
      const p = g.node(n.id) as any;
      return p
        ? { ...n, x: p.x - n.width / 2, y: p.y - n.height / 2 }
        : n;
    });
    this.resolveOverlaps(positioned);

    let maxX = 0, maxY = 0;
    for (const n of positioned) {
      maxX = Math.max(maxX, n.x + n.width);
      maxY = Math.max(maxY, n.y + n.height);
    }
    this.contentSize.set({ w: maxX + 24, h: maxY + 24 });
    this.layoutCards.set(positioned);
  }

  /** Single nudge-down sweep to remove residual bbox overlap between
   * dagre-positioned cards.  Cards may have very different heights now
   * (column-list-driven), so gaps are computed from actual height. */
  private resolveOverlaps(nodes: CardNode[]): void {
    const PAD = 14;
    const list = [...nodes].sort((a, b) => a.y - b.y);
    for (let i = 1; i < list.length; i++) {
      const cur = list[i];
      for (let j = 0; j < i; j++) {
        const prev = list[j];
        const horizontalOverlap =
          cur.x < prev.x + prev.width + PAD &&
          cur.x + cur.width + PAD > prev.x;
        if (!horizontalOverlap) continue;
        const verticalOverlap =
          cur.y < prev.y + prev.height + PAD &&
          cur.y + cur.height + PAD > prev.y;
        if (!verticalOverlap) continue;
        const newY = prev.y + prev.height + PAD;
        cur.y = Math.max(cur.y, newY);
      }
    }
  }

  // --- Column-anchored edge routing -------------------------------------
  /** Parse "child_col → parent_col" out of an edge label.  Both sides
   * may be empty/missing if the API hasn't populated the label; callers
   * fall back to vertical-card-centre anchoring in that case. */
  private parseEdgeCols(label: string | null | undefined): { fromCol: string; toCol: string } {
    if (!label) return { fromCol: '', toCol: '' };
    const arrow = ' → ';
    if (label.includes(arrow)) {
      const [a, b] = label.split(arrow);
      return { fromCol: (a ?? '').trim(), toCol: (b ?? '').trim() };
    }
    return { fromCol: label.trim(), toCol: label.trim() };
  }

  /** Find the vertical Y offset (relative to card top) of a column row.
   * Falls back to card vertical-centre when the column can't be located
   * (label missing or column not present in the inventory). */
  private columnAnchorY(card: CardNode, colName: string): number {
    if (colName) {
      const idx = card.columns.findIndex(c => c.name === colName);
      if (idx >= 0) return HEADER_H + idx * ROW_H + ROW_H / 2;
    }
    return card.height / 2;
  }

  /** Build a cubic-bezier path from a specific column row on card A to
   * a specific column row on card B.  Picks the side of each card that
   * faces the other endpoint so the curve never crosses the card body.
   *
   * Endpoints land on the LEFT or RIGHT edge of the card at the column
   * row's vertical centre.  Control points extend horizontally from the
   * endpoints — gives a smooth S-curve regardless of relative position. */
  private buildColumnAnchoredBezier(
    a: CardNode, b: CardNode, fromCol: string, toCol: string,
  ): {
    path: string;
    fromX: number; fromY: number; toX: number; toY: number;
    fromOnRight: boolean; toOnRight: boolean;
  } {
    const aY = a.y + this.columnAnchorY(a, fromCol);
    const bY = b.y + this.columnAnchorY(b, toCol);
    const aCx = a.x + a.width / 2;
    const bCx = b.x + b.width / 2;

    // Pick attachment sides: source attaches on the side facing the
    // target; target on the side facing the source.  When the two
    // cards overlap horizontally, default to the left/right rule based
    // on centre comparison (still readable, no crossing).
    const fromOnRight = bCx >= aCx;
    const toOnRight   = !fromOnRight;

    const p0x = fromOnRight ? a.x + a.width : a.x;
    const p0y = aY;
    const p1x = toOnRight   ? b.x + b.width : b.x;
    const p1y = bY;

    // Horizontal control handle proportional to the gap so close cards
    // get a tight curve, distant cards a lazy one.  Floored at 60 to
    // keep the curve graceful even when endpoints are stacked.
    const handle = Math.max(60, Math.abs(p1x - p0x) * 0.5);
    const c0x = p0x + (fromOnRight ? handle : -handle);
    const c0y = p0y;
    const c1x = p1x + (toOnRight   ? handle : -handle);
    const c1y = p1y;

    const path = `M ${p0x} ${p0y} C ${c0x} ${c0y}, ${c1x} ${c1y}, ${p1x} ${p1y}`;
    return {
      path,
      fromX: p0x, fromY: p0y, toX: p1x, toY: p1y,
      fromOnRight, toOnRight,
    };
  }

  // --- Heuristics (relation type, module badge, type glyph) ------------

  private classifyRelType(e: RelationshipEdge): RelType {
    const join = (e.label || '').toLowerCase();
    const fromT = e.from.toLowerCase();
    const toT = e.to.toLowerCase();
    if (/_audit|_log|_history|_event|change_log/.test(fromT) ||
        /_audit|_log|_history|_event|change_log/.test(toT)) return 'history';
    if (/config|setting|policy|rule|param/.test(toT)) return 'config';
    if (/_text|_desc|_note|_message|_comment|_summary|_body/.test(join)) return 'text';
    if (/status|category|country|language|currency|type|kind|locale|state|region|department|priority|level|code$/.test(toT)) return 'master_lookup';
    return 'header_item';
  }

  private relTypeColor(t: RelType): string {
    switch (t) {
      case 'header_item':   return '#58a6ff';
      case 'master_lookup': return '#3fb950';
      case 'config':        return '#bc8cff';
      case 'text':          return '#d29922';
      case 'history':       return '#8b949e';
    }
  }

  private labelFor(cardinality: string | null | undefined): string | null {
    switch (cardinality) {
      case 'ONE_TO_ONE':   return '1:1';
      case 'ONE_TO_MANY':  return '1:N';
      case 'MANY_TO_ONE':  return 'N:1';
      case 'MANY_TO_MANY': return 'N:M';
      default: return null;
    }
  }

  private moduleBadge(tableName: string): string | null {
    const t = tableName.toLowerCase();
    const map: [RegExp, string][] = [
      [/^ads_app/, 'APP'],
      [/^ads_st_/, 'STG'],
      [/^ads_user/, 'USR'],
      [/^ads_open_metadata/, 'META'],
      [/^ads_ingestion/, 'INGEST'],
      [/^ads_master_job|^ads_job/, 'JOB'],
      [/_audit$|_log$|_history$|_event/, 'AUDIT'],
      [/^ads_/, 'ADS'],
      [/_lookup$|^lkp_|^ref_/, 'REF'],
      [/^v_|^vw_/, 'VIEW'],
    ];
    for (const [re, badge] of map) {
      if (re.test(t)) return badge;
    }
    return null;
  }

  /** Single-character abbreviation for a SQL data type — keeps the
   * column row tight while still hinting at the type at a glance. */
  private typeGlyph(t: string): string {
    const lc = (t || '').toLowerCase();
    if (/int|serial|bigint|smallint/.test(lc))                          return 'i';
    if (/numeric|decimal|float|double|real|money/.test(lc))             return '#';
    if (/timestamp|date|time/.test(lc))                                 return 't';
    if (/char|text|string|uuid|json|xml|enum/.test(lc))                 return 'c';
    if (/bool/.test(lc))                                                return 'b';
    if (/bytea|blob|binary/.test(lc))                                   return 'x';
    return '·';
  }
}
