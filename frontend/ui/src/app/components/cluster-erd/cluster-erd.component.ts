// Cluster-scoped DbSchema-style ERD diagram.  Built specifically for the
// cluster-detail view per the V3 spec — separate from
// `<app-erd-card>` (which still serves the standalone /jobs/:id/erd
// page) so feature flags don't accumulate across two unrelated callers.
//
// Layout: dagre top-down, generous gaps because cards are tall (column
// list inline).  Edges use orthogonal "stair-step" routing anchored at
// the specific column row endpoints.  Cards drag by header; canvas
// pans on background drag and zooms on wheel.
//
// Interaction summary:
//   * Header click without drag → no-op (cluster ERD already focuses
//     the user on this cluster; bouncing them out would be jarring).
//   * Card-row hover → highlight only the edges touching that column.
//   * Edge hover → thicken + fade others, SVG <title> tooltip with
//     "src.col → tgt.col (cardinality, conf N)".
import {
  AfterViewInit, Component, ElementRef, OnChanges, OnDestroy,
  ViewChild, computed, inject, input, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { forkJoin } from 'rxjs';
import * as dagre from '@dagrejs/dagre';
import { JobService } from '../../services/job.service';
import {
  ColumnInfo,
  RelationshipEdge,
  RelationshipNode,
} from '../../models/job.model';

type RelType = 'header_item' | 'master_lookup' | 'config' | 'text' | 'history';
type KeyKind = 'pk' | 'fk' | 'pkfk' | null;

interface ColumnRow {
  name: string;
  dataType: string;
  isPk: boolean;
  isFk: boolean;
  keyKind: KeyKind;
  /** Null fraction in [0, 1] from the fingerprint phase.  Drives a
   * red bar at the bottom of each column row.  Null when not
   * profiled. */
  nullPct: number | null;
}

interface CardNode {
  id: string;
  label: string;
  isBridge: boolean;
  bridgeColor: string | null;
  /** Row count from tbl_inventory (via the relationships API
   * ``nodes.value`` field).  ~0 for empty tables. */
  rows: number;
  columns: ColumnRow[];
  width: number;
  height: number;
  x: number;
  y: number;
}

interface EdgeRoute {
  id: string;
  type: RelType;
  color: string;
  raw: RelationshipEdge;
  fromTable: string;
  toTable: string;
  fromCol: string;
  toCol: string;
  // Orthogonal stair-step path "M x0 y0 H midX V y1 H x1".
  path: string;
  fromX: number; fromY: number; toX: number; toY: number;
  fromOnRight: boolean; toOnRight: boolean;
  fromGlyph: string;
  toGlyph: string;
  fromGlyphX: number; fromGlyphY: number;
  toGlyphX: number; toGlyphY: number;
  tooltip: string;
}

const CARD_W = 280;
const HEADER_H = 32;
const ROW_H = 22;
const MIN_CARD_H = HEADER_H + ROW_H;
const RANK_SEP = 110;
const NODE_SEP = 48;

@Component({
  selector: 'app-cluster-erd',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="erd-toolbar">
      <span class="muted">Tables: <strong>{{ cards().length }}</strong></span>
      <span class="muted">Edges: <strong>{{ routes().length }}</strong></span>
      @if (loading()) { <span class="muted">Loading…</span> }
      @if (error()) { <span class="warn">{{ error() }}</span> }
    </div>

    <div class="erd-wrap"
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

        <!-- SVG edges layer (behind the cards) -->
        <svg class="edges"
             [attr.width]="contentSize().w"
             [attr.height]="contentSize().h"
             xmlns="http://www.w3.org/2000/svg">
          @for (e of routes(); track e.id) {
            <g [attr.data-edge]="e.id"
               class="edge-group"
               [class.edge-hover]="hoveredEdgeId() === e.id"
               [class.edge-dim]="(hoveredEdgeId() && hoveredEdgeId() !== e.id) ||
                                 (hoveredCol() && !edgeTouchesCol(e, hoveredCol()!))"
               (mouseenter)="hoveredEdgeId.set(e.id)"
               (mouseleave)="hoveredEdgeId.set(null)">
              <path class="edge-hit" [attr.d]="e.path" />
              <path class="edge"
                    [attr.d]="e.path"
                    [attr.stroke]="e.color">
                <title>{{ e.tooltip }}</title>
              </path>
              <text class="card-glyph"
                    [attr.x]="e.fromGlyphX"
                    [attr.y]="e.fromGlyphY"
                    [attr.fill]="e.color">{{ e.fromGlyph }}</text>
              <text class="card-glyph"
                    [attr.x]="e.toGlyphX"
                    [attr.y]="e.toGlyphY"
                    [attr.fill]="e.color">{{ e.toGlyph }}</text>
            </g>
          }
        </svg>

        <!-- HTML cards layer -->
        @for (n of cards(); track n.id) {
          <div class="card-node"
               [class.bridge]="n.isBridge"
               [class.dragging]="cardDrag?.cardId === n.id && cardDrag?.hasMoved"
               [style.left.px]="n.x"
               [style.top.px]="n.y"
               [style.width.px]="n.width"
               [style.height.px]="n.height"
               [style.borderColor]="n.isBridge ? '#d29922' : null">
            <!-- Header strip — subtle 15%-opacity tint of cluster colour
                 (or amber for bridge cards), high-contrast text on top.
                 Drag handle = the whole header. -->
            <div class="card-head"
                 [style.background]="headerBackground(n)"
                 (mousedown)="onHeaderMouseDown($event, n)">
              <div class="card-head-main">
                <span class="card-dot" [style.background]="n.isBridge ? '#d29922' : '#58a6ff'"></span>
                <span class="card-table mono">{{ n.label }}</span>
                @if (n.isBridge) {
                  <span class="bridge-pill" title="Outside this cluster">external</span>
                }
              </div>
              <div class="card-rows" [title]="n.rows + ' rows'">
                {{ formatRowCount(n.rows) }} rows
              </div>
            </div>
            <!-- Column rows.  stopPropagation on mousedown so the wrap
                 doesn't start a pan when a row is clicked.  Null
                 fraction surfaces in the row tooltip only — the
                 in-row red bar was visually noisy. -->
            <div class="card-cols">
              @for (c of n.columns; track c.name) {
                <div class="col-row"
                     [class.col-pk]="c.isPk"
                     [class.col-fk]="c.isFk"
                     [title]="c.nullPct != null ? c.name + ' — ' + (c.nullPct * 100 | number:'1.1-1') + '% null' : c.name"
                     (mouseenter)="onColEnter(n.id, c.name)"
                     (mouseleave)="onColLeave()"
                     (mousedown)="$event.stopPropagation()">
                  <span class="col-key">
                    @if (c.keyKind === 'pkfk') {
                      <span class="key-pkfk" title="Primary &amp; foreign key">PK·FK</span>
                    } @else if (c.keyKind === 'pk') {
                      <span class="key-pk" title="Primary key">PK</span>
                    } @else if (c.keyKind === 'fk') {
                      <span class="key-fk" title="Foreign key">FK</span>
                    }
                  </span>
                  <span class="col-name mono" [title]="c.name">{{ c.name }}</span>
                  <span class="col-type mono">{{ c.dataType }}</span>
                </div>
              }
              @if (n.columns.length === 0) {
                <div class="col-row col-empty">— no columns inventoried —</div>
              }
            </div>
          </div>
        }
      </div>

      <!-- Floating chrome -->
      <div class="legend">
        <div class="legend-title">Relationship</div>
        <div class="legend-row"><span class="swatch" style="background:#58a6ff"></span> header / item</div>
        <div class="legend-row"><span class="swatch" style="background:#3fb950"></span> master lookup</div>
        <div class="legend-row"><span class="swatch" style="background:#bc8cff"></span> config</div>
        <div class="legend-row"><span class="swatch" style="background:#d29922"></span> text</div>
        <div class="legend-row"><span class="swatch" style="background:#8b949e"></span> history</div>
      </div>
      <div class="canvas-actions">
        <button type="button" (click)="fitToScreen()">Fit to screen</button>
        <button type="button" (click)="resetZoom()">Reset zoom</button>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .erd-toolbar {
      display: flex;
      gap: 14px;
      align-items: center;
      margin-bottom: 8px;
      font-size: 12px;
    }
    .muted { color: #8b949e; }
    .muted strong { color: #e6edf3; }
    .warn {
      color: #ffabab;
      background: rgba(248, 81, 73, 0.08);
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
    }
    .erd-wrap {
      position: relative;
      width: 100%;
      height: 720px;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 8px;
      overflow: hidden;
      cursor: grab;
    }
    .erd-wrap:active { cursor: grabbing; }
    .canvas {
      position: absolute;
      top: 0; left: 0;
      transform-origin: 0 0;
    }
    svg.edges {
      position: absolute;
      top: 0; left: 0;
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
      stroke-width: 12;
      pointer-events: stroke;
    }
    .edge {
      fill: none;
      stroke-width: 1.5;
      stroke-linejoin: round;
      pointer-events: none;
    }
    .edge-group.edge-hover .edge { stroke-width: 2.8; }
    .edge-group.edge-dim { opacity: 0.2; }
    .edge-group.edge-hover { opacity: 1 !important; }
    .card-glyph {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 12px;
      font-weight: 700;
      pointer-events: none;
    }

    /* In-cluster cards — dark fill, subtle 1px border. */
    .card-node {
      position: absolute;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 4px 14px rgba(0, 0, 0, 0.18);
      overflow: hidden;
      user-select: none;
      transition: border-color 0.12s, box-shadow 0.12s;
    }
    .card-node:hover { border-color: #4a5159; }
    .card-node.dragging {
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 8px 26px rgba(0, 0, 0, 0.55);
      transition: none;
    }
    /* External (bridge) cards: dashed amber border + slightly darker
       body background so they recede from in-cluster members. */
    .card-node.bridge {
      background: #0e1117;
      border-style: dashed;
      border-color: #d29922 !important;
    }

    /* Subtle tinted header band — opacity blend of the accent colour so
       the card reads as a single object, not a high-saturation banner. */
    .card-head {
      display: flex;
      align-items: center;
      gap: 8px;
      height: ${HEADER_H}px;
      padding: 0 10px;
      background: rgba(88, 166, 255, 0.13);
      border-bottom: 1px solid #30363d;
      cursor: grab;
    }
    .card-node.dragging .card-head { cursor: grabbing; }
    .card-dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      flex: 0 0 auto;
    }
    .card-table {
      font-weight: 600;
      font-size: 13px;
      color: #e6edf3;
      letter-spacing: -0.2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1 1 auto;
    }
    .bridge-pill {
      flex: 0 0 auto;
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #e3b341;
      background: rgba(210, 153, 34, 0.18);
      border: 1px solid rgba(210, 153, 34, 0.35);
    }
    /* Header layout — name + dot + bridge pill on the left, row count
       subtitle on the right.  Mirror of the relationship-graph card
       header so analysts get the same visual vocabulary in both. */
    .card-head-main {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .card-rows {
      flex: 0 0 auto;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 10px;
      color: #8b949e;
      letter-spacing: 0.3px;
      cursor: help;
    }

    .card-cols { display: flex; flex-direction: column; }
    /* Column row layout — single key column (PK / FK / PK·FK / blank)
       on the left, name in the middle, type right-aligned in muted
       gray.  Tabular monospace keeps the column names aligned. */
    .col-row {
      display: grid;
      grid-template-columns: 44px 1fr auto;
      align-items: center;
      gap: 8px;
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
    .col-key { display: flex; align-items: center; gap: 2px; }
    .col-key .key-pk,
    .col-key .key-fk,
    .col-key .key-pkfk {
      display: inline-block;
      padding: 0 5px;
      border-radius: 3px;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      line-height: 14px;
    }
    .col-key .key-pk    { color: #3fb950; background: rgba(63, 185, 80, 0.14); }
    .col-key .key-fk    { color: #58a6ff; background: rgba(88, 166, 255, 0.14); }
    .col-key .key-pkfk  { color: #d29922; background: rgba(210, 153, 34, 0.14); font-size: 8.5px; }
    .col-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      cursor: help;
    }
    .col-type {
      color: #6e7681;
      font-size: 10.5px;
      text-align: right;
      max-width: 78px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .col-empty {
      grid-template-columns: 1fr;
      justify-content: center;
      color: #6e7681;
      font-style: italic;
      font-size: 11px;
    }
    .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }

    /* Floating chrome */
    .legend {
      position: absolute;
      top: 12px;
      right: 12px;
      background: rgba(13, 17, 23, 0.92);
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 11px;
      pointer-events: none;
      z-index: 5;
    }
    .legend-title {
      font-size: 9.5px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
      margin-bottom: 4px;
    }
    .legend-row { display: flex; align-items: center; gap: 6px; line-height: 1.6; color: #c9d1d9; }
    .swatch {
      display: inline-block;
      width: 14px; height: 3px;
      border-radius: 2px;
    }
    .canvas-actions {
      position: absolute;
      bottom: 12px;
      right: 12px;
      display: flex;
      gap: 6px;
      z-index: 5;
    }
    .canvas-actions button {
      background: rgba(13, 17, 23, 0.92);
      border: 1px solid #30363d;
      color: #c9d1d9;
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
    }
    .canvas-actions button:hover { border-color: #58a6ff; color: #58a6ff; }
  `],
})
export class ClusterErdComponent implements AfterViewInit, OnChanges, OnDestroy {
  jobId = input.required<string>();
  filterTableNames = input<string[]>([]);
  bridgeTableNames = input<string[]>([]);
  bridgeColors = input<Record<string, string>>({});

  @ViewChild('wrap') wrap!: ElementRef<HTMLDivElement>;
  private jobsSvc = inject(JobService);

  loading = signal(true);
  error = signal<string | null>(null);

  hoveredEdgeId = signal<string | null>(null);
  /** Hovered (table, column) — used to dim every edge that doesn't
   * touch the hovered column row.  Cleared on row mouseleave. */
  hoveredCol = signal<{ table: string; col: string } | null>(null);

  zoom = signal(1);
  panX = signal(0);
  panY = signal(0);
  private dragging = false;
  private dragStartX = 0;
  private dragStartY = 0;
  private dragOriginPanX = 0;
  private dragOriginPanY = 0;

  cardOffsets = signal<Record<string, { dx: number; dy: number }>>({});
  cardDrag: {
    cardId: string;
    startCursorX: number;
    startCursorY: number;
    startOffsetX: number;
    startOffsetY: number;
    hasMoved: boolean;
  } | null = null;
  private static readonly CARD_DRAG_THRESHOLD = 5;

  canvasTransform = computed(
    () => `translate(${this.panX()}px, ${this.panY()}px) scale(${this.zoom()})`,
  );

  private allEdges = signal<RelationshipEdge[]>([]);
  private allNodes = signal<RelationshipNode[]>([]);
  private columnsByTable = signal<Map<string, ColumnRow[]>>(new Map());
  private layoutCards = signal<CardNode[]>([]);
  contentSize = signal<{ w: number; h: number }>({ w: 800, h: 600 });

  cards = computed<CardNode[]>(() => {
    const offsets = this.cardOffsets();
    return this.layoutCards().map(n => {
      const off = offsets[n.id];
      return off ? { ...n, x: n.x + off.dx, y: n.y + off.dy } : n;
    });
  });

  routes = computed<EdgeRoute[]>(() => {
    const cards = this.cards();
    const byId = new Map(cards.map(c => [c.id, c]));
    const edges = this.allEdges();
    const filt = this.filterTableNames();
    const member = new Set(filt.length ? filt : cards.map(c => c.id));
    const visible = edges.filter(e =>
      byId.has(e.from) && byId.has(e.to) &&
      (member.has(e.from) || member.has(e.to)),
    );
    const out: EdgeRoute[] = [];
    visible.forEach((e, i) => {
      const a = byId.get(e.from)!;
      const b = byId.get(e.to)!;
      const { fromCol, toCol } = this.parseEdgeCols(e.label);
      const route = this.buildOrthogonal(a, b, fromCol, toCol, i);
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
        fromTable: e.from, toTable: e.to,
        fromCol, toCol,
        path: route.path,
        fromX: route.fromX, fromY: route.fromY,
        toX: route.toX, toY: route.toY,
        fromOnRight: route.fromOnRight,
        toOnRight: route.toOnRight,
        fromGlyph, toGlyph,
        fromGlyphX: route.fromX + (route.fromOnRight ? 4 : -10),
        fromGlyphY: route.fromY + 4,
        toGlyphX:   route.toX   + (route.toOnRight   ? 4 : -10),
        toGlyphY:   route.toY   + 4,
        tooltip,
      });
    });
    return out;
  });

  ngAfterViewInit(): void { this.load(); }
  ngOnChanges(): void { if (this.wrap) this.load(); }
  ngOnDestroy(): void { /* nothing to dispose */ }

  // --- Pan / zoom ----------------------------------------------------
  onWheel(ev: WheelEvent): void {
    ev.preventDefault();
    const wrap = this.wrap.nativeElement;
    const rect = wrap.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const oldZoom = this.zoom();
    const factor = ev.deltaY > 0 ? 0.9 : 1.1;
    const z = Math.min(2.5, Math.max(0.15, oldZoom * factor));
    const px = this.panX(); const py = this.panY();
    const cx = (mx - px) / oldZoom;
    const cy = (my - py) / oldZoom;
    this.panX.set(mx - cx * z);
    this.panY.set(my - cy * z);
    this.zoom.set(z);
  }
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
    if (this.cardDrag) {
      const z = this.zoom();
      const dxScreen = ev.clientX - this.cardDrag.startCursorX;
      const dyScreen = ev.clientY - this.cardDrag.startCursorY;
      if (!this.cardDrag.hasMoved) {
        if (Math.abs(dxScreen) + Math.abs(dyScreen)
            >= ClusterErdComponent.CARD_DRAG_THRESHOLD) {
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
    this.cardDrag = null;
    this.dragging = false;
  }
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

  // --- Column-row hover ---------------------------------------------
  onColEnter(table: string, col: string): void {
    this.hoveredCol.set({ table, col });
  }
  onColLeave(): void { this.hoveredCol.set(null); }
  edgeTouchesCol(e: EdgeRoute, hc: { table: string; col: string }): boolean {
    return (e.fromTable === hc.table && e.fromCol === hc.col) ||
           (e.toTable   === hc.table && e.toCol   === hc.col);
  }

  /** Inline header background — 15% opacity tint of the cluster colour
   * (or amber for bridge cards) so the band reads as part of the card,
   * not a full-saturation banner. */
  headerBackground(n: CardNode): string {
    if (n.isBridge) return 'rgba(210, 153, 34, 0.16)';
    const hex = n.bridgeColor;
    if (!hex) return 'rgba(88, 166, 255, 0.13)';
    return this.hexToRgba(hex, 0.16);
  }
  private hexToRgba(hex: string, alpha: number): string {
    const m = hex.replace('#', '').match(/^[0-9a-f]{6}$/i);
    if (!m) return `rgba(88, 166, 255, ${alpha})`;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  // --- Data load + layout -------------------------------------------
  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    forkJoin({
      graph: this.jobsSvc.relationships(this.jobId(), 1500),
      cols:  this.jobsSvc.columns(this.jobId()),
    }).subscribe({
      next: ({ graph, cols }) => {
        this.allEdges.set(graph.edges ?? []);
        this.allNodes.set(graph.nodes ?? []);
        this.columnsByTable.set(this.indexColumns(cols.columns ?? []));
        this.cardOffsets.set({});
        this.loading.set(false);
        setTimeout(() => {
          this.relayout();
          this.fitToScreen();
        }, 0);
      },
      error: err => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load cluster ERD.',
        );
      },
    });
  }

  private indexColumns(cols: ColumnInfo[]): Map<string, ColumnRow[]> {
    const out = new Map<string, ColumnRow[]>();
    for (const c of cols) {
      const arr = out.get(c.table) ?? [];
      const keyKind: KeyKind =
        c.is_pk && c.is_fk ? 'pkfk' :
        c.is_pk            ? 'pk'   :
        c.is_fk            ? 'fk'   : null;
      arr.push({
        name: c.column,
        dataType: c.data_type,
        isPk: c.is_pk,
        isFk: c.is_fk,
        keyKind,
        nullPct: c.null_pct == null ? null : Number(c.null_pct),
      });
      out.set(c.table, arr);
    }
    for (const arr of out.values()) {
      // PK first, then FK, then alpha.
      arr.sort((a, b) =>
        Number(b.isPk) - Number(a.isPk) ||
        Number(b.isFk) - Number(a.isFk) ||
        a.name.localeCompare(b.name),
      );
    }
    return out;
  }

  private relayout(): void {
    const filt = this.filterTableNames();
    const bridges = new Set(this.bridgeTableNames() ?? []);
    const memberOnly = new Set(filt);
    const include = new Set<string>([...filt, ...bridges]);

    const cby = this.columnsByTable();
    const colors = this.bridgeColors();
    const allNodes = this.allNodes();
    const rowsByTable = new Map<string, number>(
      // Prefer the explicit row_count field; fall back to the legacy
      // ``value`` (edge degree) for older API responses.
      allNodes.map(n => [n.id, n.row_count ?? n.value ?? 0]),
    );
    const allTables = new Set<string>([
      ...allNodes.map(n => n.id),
      ...this.allEdges().flatMap(e => [e.from, e.to]),
    ]);

    // Use either the explicit filter set, or every known table when no
    // filter is provided (i.e. the standalone use case).
    const finalTables = include.size > 0
      ? [...include].filter(t => allTables.has(t))
      : [...allTables];

    const nodes: CardNode[] = finalTables.map(t => {
      const cols = cby.get(t) ?? [];
      const isBridge = bridges.has(t) && !memberOnly.has(t);
      const height = Math.max(MIN_CARD_H, HEADER_H + cols.length * ROW_H);
      return {
        id: t,
        label: t,
        isBridge,
        bridgeColor: isBridge ? (colors[t] ?? null) : null,
        rows: rowsByTable.get(t) ?? 0,
        columns: cols,
        width: CARD_W,
        height,
        x: 0, y: 0,
      };
    });

    const edges = this.allEdges().filter(e =>
      include.has(e.from) && include.has(e.to) &&
      (memberOnly.has(e.from) || memberOnly.has(e.to)),
    );

    const g = new dagre.graphlib.Graph<{}>().setGraph({
      rankdir: 'TB',
      nodesep: NODE_SEP,
      ranksep: RANK_SEP,
      marginx: 24,
      marginy: 24,
    } as any).setDefaultEdgeLabel(() => ({}));
    for (const n of nodes) g.setNode(n.id, { width: n.width, height: n.height });
    for (const e of edges) {
      if (g.node(e.from) && g.node(e.to)) g.setEdge(e.from, e.to);
    }
    dagre.layout(g);

    const positioned: CardNode[] = nodes.map(n => {
      const p = g.node(n.id) as any;
      return p ? { ...n, x: p.x - n.width / 2, y: p.y - n.height / 2 } : n;
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

  private resolveOverlaps(nodes: CardNode[]): void {
    const PAD = 16;
    const list = [...nodes].sort((a, b) => a.y - b.y);
    for (let i = 1; i < list.length; i++) {
      const cur = list[i];
      for (let j = 0; j < i; j++) {
        const prev = list[j];
        const ho = cur.x < prev.x + prev.width + PAD &&
                   cur.x + cur.width + PAD > prev.x;
        if (!ho) continue;
        const vo = cur.y < prev.y + prev.height + PAD &&
                   cur.y + cur.height + PAD > prev.y;
        if (!vo) continue;
        cur.y = Math.max(cur.y, prev.y + prev.height + PAD);
      }
    }
  }

  private parseEdgeCols(label: string | null | undefined): { fromCol: string; toCol: string } {
    if (!label) return { fromCol: '', toCol: '' };
    const arrow = ' → ';
    if (label.includes(arrow)) {
      const [a, b] = label.split(arrow);
      return { fromCol: (a ?? '').trim(), toCol: (b ?? '').trim() };
    }
    const ascii = label.indexOf('->');
    if (ascii >= 0) {
      return {
        fromCol: label.slice(0, ascii).trim(),
        toCol:   label.slice(ascii + 2).trim(),
      };
    }
    return { fromCol: label.trim(), toCol: label.trim() };
  }

  private columnAnchorY(card: CardNode, colName: string): number {
    if (colName) {
      const idx = card.columns.findIndex(c => c.name === colName);
      if (idx >= 0) return HEADER_H + idx * ROW_H + ROW_H / 2;
    }
    return card.height / 2;
  }

  /**
   * Orthogonal stair-step path from a column row on card A to a column
   * row on card B.  Right-angle bends only — no diagonals — to match
   * DbSchema's visual style.  Per-edge ``midX`` jitter (modulo 7) so
   * parallel edges don't stack on the same vertical seam.
   */
  private buildOrthogonal(
    a: CardNode, b: CardNode,
    fromCol: string, toCol: string,
    edgeIdx: number,
  ): {
    path: string;
    fromX: number; fromY: number; toX: number; toY: number;
    fromOnRight: boolean; toOnRight: boolean;
  } {
    const aY = a.y + this.columnAnchorY(a, fromCol);
    const bY = b.y + this.columnAnchorY(b, toCol);
    const aCx = a.x + a.width / 2;
    const bCx = b.x + b.width / 2;
    const fromOnRight = bCx >= aCx;
    const toOnRight   = !fromOnRight;
    const x0 = fromOnRight ? a.x + a.width : a.x;
    const x1 = toOnRight   ? b.x + b.width : b.x;
    // Stub out from each side so the stair-step doesn't graze the card.
    const stub = 16;
    const sx0 = fromOnRight ? x0 + stub : x0 - stub;
    const sx1 = toOnRight   ? x1 + stub : x1 - stub;
    // Vertical seam at the midpoint between the two stubs, jittered so
    // parallel edges don't collapse onto the same line.
    const jitter = (edgeIdx % 7) * 8 - 24;
    const midX = (sx0 + sx1) / 2 + jitter;
    const path =
      `M ${x0} ${aY} ` +
      `L ${sx0} ${aY} ` +
      `L ${midX} ${aY} ` +
      `L ${midX} ${bY} ` +
      `L ${sx1} ${bY} ` +
      `L ${x1} ${bY}`;
    return { path, fromX: x0, fromY: aY, toX: x1, toY: bY, fromOnRight, toOnRight };
  }

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

  /** Compact row-count label — matches the relationship-graph card
   * header so analysts see the same vocabulary across both views. */
  formatRowCount(n: number): string {
    if (n == null || !isFinite(n) || n < 0) return '?';
    if (n < 1000) return String(n);
    if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`;
    return `${(n / 1_000_000).toFixed(1)}M`;
  }

  private labelFor(c: string | null | undefined): string | null {
    switch (c) {
      case 'ONE_TO_ONE':   return '1:1';
      case 'ONE_TO_MANY':  return '1:N';
      case 'MANY_TO_ONE':  return 'N:1';
      case 'MANY_TO_MANY': return 'N:M';
      default: return null;
    }
  }
}
