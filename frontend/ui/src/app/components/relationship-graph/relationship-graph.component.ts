import {
  AfterViewInit, Component, ElementRef, OnChanges, OnDestroy,
  ViewChild, computed, inject, input, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import * as dagre from '@dagrejs/dagre';
import { JobService } from '../../services/job.service';
import {
  RelationshipEdge,
  RelationshipNode,
} from '../../models/job.model';

type RelType = 'header_item' | 'master_lookup' | 'config' | 'text' | 'history';

interface CardNode {
  id: string;        // table name (also dagre node id)
  label: string;     // display name
  rows: number;      // row count from the API node payload
  fieldCount: number; // edge degree as a proxy for "how connected"
  module: string | null;
  width: number;
  height: number;
  x: number;         // top-left after layout
  y: number;
}

interface EdgeRoute {
  id: string;
  type: RelType;
  color: string;
  joinLabel: string;
  cardLabel: string | null;
  raw: RelationshipEdge;
  // Cubic bezier path "M x0 y0 C cx0 cy0 cx1 cy1 x1 y1"
  path: string;
  // For label + glyph placement.
  midX: number;
  midY: number;
  fromGlyph: string;   // "|" / ">"  (cardinality at the FROM endpoint)
  toGlyph: string;     // "|" / "<"
  fromGlyphX: number;
  fromGlyphY: number;
  toGlyphX: number;
  toGlyphY: number;
}

const CARD_W = 240;
const CARD_H_BASE = 96;     // nominal card height — overridden after measure
const RANK_SEP = 100;        // vertical gap between dagre ranks
const NODE_SEP = 28;         // horizontal gap within a rank

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
          <defs>
            <!-- Subtle drop-shadow on the cards (CSS handles its own) and
                 the cardinality endcaps live in the SVG layer. -->
          </defs>
          @for (e of routes(); track e.id) {
            <g [attr.data-edge]="e.id"
               [class.edge-selected]="selectedEdgeId() === e.id"
               class="edge-group"
               (click)="onEdgeClick(e)">
              <path class="edge"
                    [attr.d]="e.path"
                    [attr.stroke]="e.color" />
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
              <!-- Edge label pill -->
              @if (e.joinLabel) {
                <g [attr.transform]="'translate(' + e.midX + ',' + e.midY + ')'">
                  <rect class="label-pill"
                        [attr.x]="-labelWidth(e.joinLabel) / 2 - 6"
                        y="-9"
                        [attr.width]="labelWidth(e.joinLabel) + 12"
                        height="18"
                        rx="4" />
                  <text class="label-text" text-anchor="middle" dy="4">{{ e.joinLabel }}</text>
                </g>
              }
            </g>
          }
        </svg>

        <!-- HTML card layer (queryviz-style nodes) -->
        @for (n of cards(); track n.id) {
          <div class="card-node"
               [class.selected]="selectedCardId() === n.id"
               [style.left.px]="n.x"
               [style.top.px]="n.y"
               [style.width.px]="n.width"
               (click)="onCardClick(n)">
            <div class="card-head">
              <span class="card-table mono">{{ n.label }}</span>
              @if (n.module) {
                <span class="card-module">{{ n.module }}</span>
              }
            </div>
            <div class="card-desc">
              {{ n.rows | number }} row{{ n.rows === 1 ? '' : 's' }}
              @if (rowDescription(n)) { · {{ rowDescription(n) }} }
            </div>
            <div class="card-foot">
              {{ n.fieldCount }} field{{ n.fieldCount === 1 ? '' : 's' }}
              <a class="open-link"
                 [routerLink]="['/jobs', jobId(), 'tables', n.id]"
                 (click)="$event.stopPropagation()"
                 title="Open the queryviz-style table page">
                open →
              </a>
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
      height: 720px;
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
    .edge-group { pointer-events: auto; cursor: pointer; }
    .edge {
      fill: none;
      stroke-width: 1.6;
      transition: stroke-width 0.1s;
    }
    .edge-group:hover .edge { stroke-width: 2.6; }
    .edge-selected .edge { stroke-width: 3; }
    .card-glyph {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 13px;
      font-weight: 700;
      pointer-events: none;
    }
    .label-pill {
      fill: #0d1117;
      stroke: #30363d;
      stroke-width: 1;
    }
    .label-text {
      fill: #c9d1d9;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 11px;
      pointer-events: none;
    }

    /* HTML card nodes — queryviz style. */
    .card-node {
      position: absolute;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 4px 14px rgba(0, 0, 0, 0.18);
      padding: 10px 12px;
      cursor: pointer;
      transition: border-color 0.12s, transform 0.12s;
      user-select: none;
    }
    .card-node:hover { border-color: #58a6ff; }
    .card-node.selected {
      border-color: #58a6ff;
      border-width: 2px;
      padding: 9px 11px;  /* compensate for thicker border */
    }
    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
      margin-bottom: 4px;
    }
    .card-table {
      font-weight: 600;
      font-size: 14px;
      color: #e6edf3;
      letter-spacing: -0.2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .card-module {
      flex-shrink: 0;
      padding: 1px 7px;
      border-radius: 8px;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      color: #a371f7;
      background: rgba(163, 113, 247, 0.12);
    }
    .card-desc {
      font-size: 12px;
      color: #8b949e;
      line-height: 1.4;
      max-height: 32px;
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .card-foot {
      margin-top: 6px;
      padding-top: 6px;
      border-top: 1px solid #21262d;
      font-size: 11px;
      color: #6e7681;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .open-link {
      color: #58a6ff;
      font-size: 11px;
    }
    .open-link:hover { text-decoration: underline; }
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
  visibleEdgeCount = signal(0);
  loading = signal(true);
  error = signal<string | null>(null);
  minConfidence = signal(0);
  layoutDir = signal<'TB' | 'LR'>('TB');
  selectedEdgeId = signal<string | null>(null);
  selectedCardId = signal<string | null>(null);

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

  // --- Data + layout output -----------------------------------------
  private allEdges: RelationshipEdge[] = [];
  private allNodes: RelationshipNode[] = [];

  cards = signal<CardNode[]>([]);
  routes = signal<EdgeRoute[]>([]);
  contentSize = signal<{ w: number; h: number }>({ w: 800, h: 600 });

  // ------------------------------------------------------------------
  ngAfterViewInit(): void { this.load(); }
  ngOnChanges(): void { if (this.wrap) this.load(); }
  ngOnDestroy(): void { /* nothing to dispose */ }

  // --- Toolbar handlers --------------------------------------------------
  onConfidenceChange(ev: Event): void {
    const v = +(ev.target as HTMLInputElement).value;
    this.minConfidence.set(v);
    this.relayout();
  }

  toggleDirection(): void {
    this.layoutDir.set(this.layoutDir() === 'TB' ? 'LR' : 'TB');
    this.relayout();
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
    const z = Math.min(1, Math.max(0.2, Math.min(sx, sy)));
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
    const z = Math.min(2.5, Math.max(0.2, oldZoom * factor));
    // Keep the cursor over the same logical canvas point after zooming.
    const px = this.panX();
    const py = this.panY();
    const cx = (mx - px) / oldZoom;
    const cy = (my - py) / oldZoom;
    this.panX.set(mx - cx * z);
    this.panY.set(my - cy * z);
    this.zoom.set(z);
  }

  onMouseDown(ev: MouseEvent): void {
    // Only background drag — clicks on cards/edges keep their handlers.
    const target = ev.target as HTMLElement;
    if (target.closest('.card-node') || target.closest('.edge-group')) return;
    this.dragging = true;
    this.dragStartX = ev.clientX;
    this.dragStartY = ev.clientY;
    this.dragOriginPanX = this.panX();
    this.dragOriginPanY = this.panY();
  }
  onMouseMove(ev: MouseEvent): void {
    if (!this.dragging) return;
    this.panX.set(this.dragOriginPanX + (ev.clientX - this.dragStartX));
    this.panY.set(this.dragOriginPanY + (ev.clientY - this.dragStartY));
  }
  onMouseUp(_ev: MouseEvent): void { this.dragging = false; }

  onCardClick(n: CardNode): void {
    this.selectedCardId.set(this.selectedCardId() === n.id ? null : n.id);
    this.jobsSvc.selectedTable.set(this.selectedCardId());
  }

  onEdgeClick(e: EdgeRoute): void {
    this.selectedEdgeId.set(this.selectedEdgeId() === e.id ? null : e.id);
  }

  // --- Data load + layout pipeline ---------------------------------------
  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.jobsSvc.relationships(this.jobId(), 1500).subscribe({
      next: g => {
        this.schema.set(g.schema);
        this.totalTables.set(g.total_tables);
        this.totalEdges.set(g.total_edges);
        this.allEdges = g.edges ?? [];
        this.allNodes = g.nodes ?? [];
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

  private relayout(): void {
    const min = this.minConfidence();
    const filtered = this.allEdges.filter(e => e.confidence == null || e.confidence >= min);
    this.visibleEdgeCount.set(filtered.length);

    // Compute degree per node from the FILTERED edge set.
    const degree = new Map<string, number>();
    for (const e of filtered) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }

    // Skip nodes that have no visible edges and no rows — keeps the canvas
    // readable when the slider is dragged high.  Always include every API
    // node when there are no edges at all (initial render-pre-extraction).
    const includedNodeIds = filtered.length === 0
      ? new Set(this.allNodes.map(n => n.id))
      : new Set<string>([
          ...filtered.map(e => e.from),
          ...filtered.map(e => e.to),
        ]);

    const nodes: CardNode[] = this.allNodes
      .filter(n => includedNodeIds.has(n.id))
      .map(n => ({
        id: n.id,
        label: n.label,
        rows: n.value ?? 0,
        fieldCount: degree.get(n.id) ?? 0,
        module: this.moduleBadge(n.id),
        width: CARD_W,
        height: CARD_H_BASE,
        x: 0, y: 0,
      }));

    // Run dagre to position cards.
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

    // Translate dagre's centre coords → top-left coords used by absolute CSS.
    const positioned: CardNode[] = nodes.map(n => {
      const p = g.node(n.id) as any;
      return p
        ? { ...n, x: p.x - n.width / 2, y: p.y - n.height / 2 }
        : n;
    });
    // Overlap-resolution sweep: if two cards' bboxes intersect, push the
    // lower one down by the overlap delta.  Single sort + sweep — O(n log n).
    this.resolveOverlaps(positioned);
    // Compute content bounding box for the canvas extent + fit-to-screen.
    let maxX = 0, maxY = 0;
    for (const n of positioned) {
      maxX = Math.max(maxX, n.x + n.width);
      maxY = Math.max(maxY, n.y + n.height);
    }
    this.contentSize.set({ w: maxX + 24, h: maxY + 24 });
    this.cards.set(positioned);

    // Build edge routes between card BORDERS using cubic bezier.
    const byId = new Map(positioned.map(c => [c.id, c]));
    const routes: EdgeRoute[] = [];
    filtered.forEach((e, i) => {
      const a = byId.get(e.from);
      const b = byId.get(e.to);
      if (!a || !b) return;
      const route = this.buildBezier(a, b);
      const type = this.classifyRelType(e);
      const join = this.joinColumn(e.label) ?? '';
      const cardLabel = this.labelFor(e.cardinality);
      const fromCard = e.cardinality;
      const fromGlyph = (fromCard === 'MANY_TO_ONE' || fromCard === 'MANY_TO_MANY') ? '>' : '|';
      const toGlyph = (fromCard === 'ONE_TO_MANY' || fromCard === 'MANY_TO_MANY') ? '<' : '|';
      routes.push({
        id: `e${i}`,
        type,
        color: this.relTypeColor(type),
        joinLabel: cardLabel ? `${join}  ${cardLabel}`.trim() : join,
        cardLabel,
        raw: e,
        path: route.path,
        midX: route.midX,
        midY: route.midY,
        fromGlyph,
        toGlyph,
        fromGlyphX: route.fromX + (route.fromOnRight ? 4 : -10),
        fromGlyphY: route.fromY + 4,
        toGlyphX: route.toX + (route.toOnRight ? 4 : -10),
        toGlyphY: route.toY + 4,
      });
    });
    this.routes.set(routes);
  }

  /**
   * After dagre lays out the graph, two cards in adjacent ranks can still
   * end up with overlapping bounding boxes when the second rank is tight.
   * Single nudge-down sweep: sort by y, push later cards down by any
   * vertical overlap with prior cards.  Cheap and good enough for the
   * typical schema sizes (≤200 cards).
   */
  private resolveOverlaps(nodes: CardNode[]): void {
    const PAD = 12;
    const list = [...nodes].sort((a, b) => a.y - b.y);
    for (let i = 1; i < list.length; i++) {
      const cur = list[i];
      for (let j = 0; j < i; j++) {
        const prev = list[j];
        // Bounding box overlap?
        const horizontalOverlap =
          cur.x < prev.x + prev.width + PAD &&
          cur.x + cur.width + PAD > prev.x;
        if (!horizontalOverlap) continue;
        const verticalOverlap =
          cur.y < prev.y + prev.height + PAD &&
          cur.y + cur.height + PAD > prev.y;
        if (!verticalOverlap) continue;
        // Push cur down so its top equals prev's bottom + PAD.
        const newY = prev.y + prev.height + PAD;
        cur.y = Math.max(cur.y, newY);
      }
    }
  }

  /**
   * Build a cubic-bezier path between the BORDER of card ``a`` and the
   * BORDER of card ``b``.  We pick the side of each card facing the other
   * (top/bottom/left/right) so the curve never crosses the card body.
   * Control points are placed orthogonally to the chosen side so the
   * curve is smooth and approximately U-shaped between top/bottom
   * neighbours.
   */
  private buildBezier(a: CardNode, b: CardNode): {
    path: string; midX: number; midY: number;
    fromX: number; fromY: number; toX: number; toY: number;
    fromOnRight: boolean; toOnRight: boolean;
  } {
    const ac = { x: a.x + a.width / 2, y: a.y + a.height / 2 };
    const bc = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    const dx = bc.x - ac.x;
    const dy = bc.y - ac.y;
    const horizontal = Math.abs(dx) > Math.abs(dy);

    let p0x: number, p0y: number, p1x: number, p1y: number;
    let c0x: number, c0y: number, c1x: number, c1y: number;
    let fromOnRight = false, toOnRight = false;

    if (horizontal) {
      // Connect right side of left card to left side of right card.
      if (dx >= 0) {
        p0x = a.x + a.width; p0y = ac.y; fromOnRight = true;
        p1x = b.x;            p1y = bc.y; toOnRight = false;
      } else {
        p0x = a.x;            p0y = ac.y; fromOnRight = false;
        p1x = b.x + b.width;  p1y = bc.y; toOnRight = true;
      }
      const handle = Math.max(60, Math.abs(p1x - p0x) * 0.5);
      c0x = p0x + (fromOnRight ? handle : -handle);
      c0y = p0y;
      c1x = p1x + (toOnRight ? handle : -handle);
      c1y = p1y;
    } else {
      // Vertical: top/bottom borders.
      if (dy >= 0) {
        p0x = ac.x; p0y = a.y + a.height;
        p1x = bc.x; p1y = b.y;
      } else {
        p0x = ac.x; p0y = a.y;
        p1x = bc.x; p1y = b.y + b.height;
      }
      const handle = Math.max(60, Math.abs(p1y - p0y) * 0.5);
      c0x = p0x;
      c0y = p0y + (dy >= 0 ? handle : -handle);
      c1x = p1x;
      c1y = p1y + (dy >= 0 ? -handle : handle);
    }

    // Midpoint at t=0.5 of the cubic bezier (parametric).
    const t = 0.5;
    const omt = 1 - t;
    const midX = omt ** 3 * p0x + 3 * omt ** 2 * t * c0x + 3 * omt * t ** 2 * c1x + t ** 3 * p1x;
    const midY = omt ** 3 * p0y + 3 * omt ** 2 * t * c0y + 3 * omt * t ** 2 * c1y + t ** 3 * p1y;

    const path = `M ${p0x} ${p0y} C ${c0x} ${c0y}, ${c1x} ${c1y}, ${p1x} ${p1y}`;
    return {
      path, midX, midY,
      fromX: p0x, fromY: p0y, toX: p1x, toY: p1y,
      fromOnRight, toOnRight,
    };
  }

  // --- Heuristics (relation type, module badge, etc.) -------------------

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

  private joinColumn(rawLabel: string | null | undefined): string | null {
    if (!rawLabel) return null;
    const arrow = ' → ';
    if (rawLabel.includes(arrow)) {
      const [child, parent] = rawLabel.split(arrow);
      const c = (child ?? '').trim();
      const p = (parent ?? '').trim();
      if (c && p && c === p) return c;
      return `${c}→${p}`;
    }
    return rawLabel;
  }

  private labelFor(cardinality: string | null | undefined): string | null {
    switch (cardinality) {
      case 'ONE_TO_ONE': return '1:1';
      case 'ONE_TO_MANY': return '1:N';
      case 'MANY_TO_ONE': return 'N:1';
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

  /** Approx pixel width of a label string (mono 11px) for the SVG pill. */
  labelWidth(s: string): number {
    return s.length * 6.6;
  }

  /** A short row-count description e.g. "small", "wide" — one-liner for
   * the second card line.  Keep it gentle so it never dominates. */
  rowDescription(n: CardNode): string | null {
    if (n.rows === 0) return 'empty';
    if (n.rows < 100) return 'small';
    if (n.rows < 10_000) return null;
    if (n.rows < 1_000_000) return 'large';
    return 'massive';
  }
}
