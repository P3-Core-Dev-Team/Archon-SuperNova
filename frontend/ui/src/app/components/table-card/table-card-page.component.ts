import {
  AfterViewInit, Component, ElementRef, HostListener, OnInit,
  ViewChild, computed, inject, signal,
} from '@angular/core';
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
        <!-- MAP mode: focal-table at the centre, 1-hop neighbours arranged
             radially around it.  Floating chrome (back pill, jump-to
             search, legend, export) sits on top of the canvas. -->
        <div class="map-wrap"
             #mapWrap
             (wheel)="onMapWheel($event)"
             (mousedown)="onMapMouseDown($event)"
             (mousemove)="onMapMouseMove($event)"
             (mouseup)="onMapMouseUp($event)"
             (mouseleave)="onMapMouseUp($event)">

          <!-- Top-left floating pill: back arrow + module | table -->
          <div class="map-pill top-left">
            <a class="back-arrow" [routerLink]="['/jobs', jobId]">←</a>
            @if (mapModuleBadge()) {
              <span class="pill-module">{{ mapModuleBadge() }}</span>
              <span class="pill-sep">|</span>
            }
            <span class="mono pill-name">{{ tableName }}</span>
          </div>
          <div class="map-chip top-left chip-below muted small">
            {{ mapEdges().length }} connection{{ mapEdges().length === 1 ? '' : 's' }}
          </div>

          <!-- Top-center floating jump-to-table search -->
          <div class="map-search">
            <span class="search-icon">⌕</span>
            <input class="search-input"
                   [value]="searchQuery()"
                   (input)="onSearchInput($event)"
                   placeholder="jump to table…" />
            @if (searchHits().length > 0 && searchQuery().length > 0) {
              <div class="search-hits">
                @for (h of searchHits(); track h) {
                  <a class="search-hit mono"
                     [routerLink]="['/jobs', jobId, 'tables', h]"
                     [queryParams]="{ view: 'map' }"
                     (click)="searchQuery.set('')">{{ h }}</a>
                }
              </div>
            }
          </div>

          <!-- Top-right floating legend -->
          <div class="map-legend">
            <div class="legend-title">relationship types</div>
            <div class="legend-row"><span class="swatch" style="background:#58a6ff"></span> header / item</div>
            <div class="legend-row"><span class="swatch" style="background:#3fb950"></span> master lookup</div>
            <div class="legend-row"><span class="swatch" style="background:#bc8cff"></span> config</div>
            <div class="legend-row"><span class="swatch" style="background:#d29922"></span> text</div>
            <div class="legend-row"><span class="swatch" style="background:#8b949e"></span> history</div>
          </div>

          <!-- Bottom-right export button -->
          <button type="button" class="map-export"
                  (click)="exportMap()"
                  title="Copy a DBML / Mermaid snippet for the focal table + neighbours">
            export
          </button>

          <!-- Canvas: cards positioned absolutely, edges in SVG behind -->
          <div class="map-canvas"
               [style.transform]="mapCanvasTransform()"
               [style.width.px]="mapContentSize().w"
               [style.height.px]="mapContentSize().h">

            <svg class="map-edges"
                 [attr.width]="mapContentSize().w"
                 [attr.height]="mapContentSize().h"
                 xmlns="http://www.w3.org/2000/svg">
              @for (e of mapEdges(); track e.id) {
                <g class="map-edge-group" [class.dimmed]="hoveredCardId() && !isEdgeAdjacentToHover(e)">
                  <path class="map-edge"
                        [attr.d]="e.path"
                        [attr.stroke]="e.color" />
                  <!-- Cardinality glyphs at endpoints -->
                  <text class="map-glyph"
                        [attr.x]="e.fromGlyphX"
                        [attr.y]="e.fromGlyphY"
                        [attr.fill]="e.color">{{ e.fromGlyph }}</text>
                  <text class="map-glyph"
                        [attr.x]="e.toGlyphX"
                        [attr.y]="e.toGlyphY"
                        [attr.fill]="e.color">{{ e.toGlyph }}</text>
                  <!-- Joining column label, plain text on canvas (no pill) -->
                  @if (e.joinLabel) {
                    <text class="map-edge-label"
                          [attr.x]="e.midX"
                          [attr.y]="e.midY"
                          text-anchor="middle"
                          [attr.fill]="'#c9d1d9'">{{ e.joinLabel }}</text>
                  }
                </g>
              }
            </svg>

            @for (n of mapCards(); track n.id) {
              <div class="map-card"
                   [class.focal]="n.id === tableName"
                   [class.dim]="hoveredCardId() && hoveredCardId() !== n.id && !isCardAdjacentToHover(n.id)"
                   [style.left.px]="n.x"
                   [style.top.px]="n.y"
                   [style.width.px]="n.width"
                   (mouseenter)="hoveredCardId.set(n.id)"
                   (mouseleave)="hoveredCardId.set(null)"
                   (click)="onMapCardClick(n)">
                <div class="card-head">
                  <span class="card-table mono">{{ n.label }}</span>
                  @if (n.module) { <span class="card-module">{{ n.module }}</span> }
                </div>
                <div class="card-desc">
                  {{ n.rows | number }} row{{ n.rows === 1 ? '' : 's' }}
                  @if (n.id !== tableName) {
                    @if (n.fieldCount > 0) { · {{ n.fieldCount }} field{{ n.fieldCount === 1 ? '' : 's' }} }
                  }
                </div>
                <div class="card-foot">
                  {{ n.relCount }} relationship{{ n.relCount === 1 ? '' : 's' }}
                </div>
              </div>
            }
          </div>

          @if (mapCards().length === 1) {
            <div class="overlay muted">
              No FK relationships from this table — nothing to draw.
            </div>
          }
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

    /* === MAP mode: focal-table 1-hop graph ============================ */

    /* Viewport-bleed: the host is constrained to max-width:1400px with auto
       margins, but in map mode the canvas spans the full viewport below the
       page header.  margin-left/right calc(50% - 50vw) negates the host's
       centring without affecting surrounding TABLE-mode content.
       Border-radius + side-borders are dropped to feel edge-to-edge. */
    .map-wrap {
      position: relative;
      width: 100vw;
      margin-left: calc(50% - 50vw);
      margin-right: calc(50% - 50vw);
      /* Fill remaining vertical space below the page header.  The header
         (back link + page-header row + badges row) is roughly 180 px tall;
         the --map-top-offset CSS custom property lets a parent override the
         constant if its layout changes.  min-height keeps the canvas usable
         when the viewport is short (e.g. landscape phones). */
      height: calc(100vh - var(--map-top-offset, 196px));
      min-height: 480px;
      background: #0d1117;
      border-top: 1px solid #30363d;
      border-bottom: 1px solid #30363d;
      border-left: none;
      border-right: none;
      border-radius: 0;
      overflow: hidden;
      cursor: grab;
      margin-top: 6px;
    }
    .map-wrap:active { cursor: grabbing; }

    .map-canvas {
      position: absolute;
      top: 0;
      left: 0;
      transform-origin: 0 0;
    }

    /* Edge SVG sits behind cards. */
    svg.map-edges {
      position: absolute;
      top: 0;
      left: 0;
      pointer-events: none;
      overflow: visible;
    }
    .map-edge-group { transition: opacity 0.15s; }
    .map-edge-group.dimmed { opacity: 0.18; }
    .map-edge {
      fill: none;
      stroke-width: 1.6;
    }
    .map-glyph {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 13px;
      font-weight: 700;
      pointer-events: none;
    }
    .map-edge-label {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 11px;
      pointer-events: none;
      paint-order: stroke;
      stroke: #0d1117;
      stroke-width: 4px;
      stroke-linejoin: round;
    }

    /* Cards (focal + neighbour) — IDENTICAL geometry per the spec.  Only
       the focal gets a 2px accent border; no size change, no glow. */
    .map-card {
      position: absolute;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 4px 14px rgba(0, 0, 0, 0.18);
      padding: 10px 12px;
      cursor: pointer;
      transition: border-color 0.12s, transform 0.12s, opacity 0.15s;
      user-select: none;
    }
    .map-card:hover { border-color: #58a6ff; }
    .map-card.focal {
      border: 2px solid #58a6ff;
      padding: 9px 11px;  /* compensate for thicker border */
      cursor: default;
    }
    .map-card.dim { opacity: 0.35; }

    /* Floating chrome.  Each control sits absolutely-positioned over the
       canvas; the canvas takes the wheel/drag handlers, chrome elements
       capture their own clicks. */
    .map-pill {
      position: absolute;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: rgba(13, 17, 23, 0.92);
      border: 1px solid #30363d;
      border-radius: 999px;
      padding: 5px 12px;
      font-size: 12px;
      z-index: 5;
    }
    .map-pill.top-left { top: 12px; left: 12px; }
    .map-pill .back-arrow { color: #c9d1d9; font-size: 14px; line-height: 1; }
    .map-pill .pill-module {
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      color: #a371f7;
      background: rgba(163, 113, 247, 0.12);
      padding: 1px 7px;
      border-radius: 8px;
    }
    .map-pill .pill-sep { color: #30363d; }
    .map-pill .pill-name { color: #e6edf3; font-weight: 600; }

    .map-chip {
      position: absolute;
      z-index: 5;
      background: rgba(13, 17, 23, 0.92);
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 3px 8px;
    }
    .map-chip.top-left.chip-below { top: 50px; left: 16px; }

    .map-search {
      position: absolute;
      top: 12px;
      left: 50%;
      transform: translateX(-50%);
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 999px;
      padding: 5px 14px;
      width: 320px;
      max-width: 50vw;
      z-index: 5;
    }
    .map-search:focus-within { border-color: #58a6ff; }
    .map-search .search-icon {
      color: #8b949e;
      font-size: 13px;
    }
    .map-search .search-input {
      flex: 1;
      background: transparent;
      border: none;
      outline: none;
      color: #e6edf3;
      font-size: 13px;
      padding: 0;
      font-family: ui-monospace, SFMono-Regular, monospace;
    }
    .map-search .search-input::placeholder { color: #6e7681; font-family: inherit; }
    .search-hits {
      position: absolute;
      top: 36px;
      left: 0;
      right: 0;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      box-shadow: 0 4px 14px rgba(0,0,0,0.4);
      padding: 4px;
      max-height: 240px;
      overflow-y: auto;
    }
    .search-hit {
      display: block;
      padding: 5px 10px;
      color: #c9d1d9;
      font-size: 12px;
      border-radius: 4px;
      text-decoration: none;
    }
    .search-hit:hover { background: #1c222b; color: #e6edf3; }

    .map-legend {
      position: absolute;
      top: 12px;
      right: 12px;
      background: rgba(13, 17, 23, 0.92);
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 12px;
      z-index: 5;
      pointer-events: none;
    }
    .map-legend .legend-title {
      font-size: 10px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: #8b949e;
      margin-bottom: 4px;
    }
    .map-legend .legend-row { display: flex; align-items: center; gap: 6px; line-height: 1.6; color: #c9d1d9; }
    .map-legend .swatch {
      display: inline-block;
      width: 14px;
      height: 3px;
      border-radius: 2px;
    }

    .map-export {
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: #161b22;
      color: #c9d1d9;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 5px 14px;
      font-size: 12px;
      cursor: pointer;
      z-index: 5;
    }
    .map-export:hover { border-color: #58a6ff; color: #fff; }

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
export class TableCardPageComponent implements OnInit, AfterViewInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private jobsSvc = inject(JobService);

  // Reference to the map's bordered canvas wrapper so fit-to-screen can
  // measure its real size after the viewport-bleed CSS settles.
  @ViewChild('mapWrap') private mapWrapEl?: ElementRef<HTMLDivElement>;

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
    if (v === 'map') {
      // Wait for Angular to render the map block + the next paint so the
      // viewport-bleed CSS has measured.  rAF is enough on first toggle;
      // fall through to a setTimeout(0) cushion when rAF fires too early
      // (the @if branch hasn't projected its DOM yet).
      requestAnimationFrame(() => this.fitMapToScreen());
      setTimeout(() => this.fitMapToScreen(), 50);
    }
  }

  /** Compute a transform that fits the radial card layout into the
   * available viewport-sized canvas, with a small margin.  Called on
   * initial MAP render, on toggle into MAP mode, and on window resize. */
  fitMapToScreen(): void {
    if (this.view() !== 'map') return;
    const wrap = this.mapWrapEl?.nativeElement;
    const sz = this.mapContentSize();
    if (!wrap || sz.w === 0 || sz.h === 0) return;
    const ww = wrap.clientWidth;
    const wh = wrap.clientHeight;
    if (ww === 0 || wh === 0) return;
    const margin = 60;
    const sx = (ww - margin * 2) / sz.w;
    const sy = (wh - margin * 2) / sz.h;
    const z = Math.min(1, Math.max(0.2, Math.min(sx, sy)));
    this.mapZoom.set(z);
    this.mapPanX.set((ww - sz.w * z) / 2);
    this.mapPanY.set((wh - sz.h * z) / 2);
  }

  ngAfterViewInit(): void {
    // If the user landed directly on ?view=map (deep link), the data
    // load below will eventually populate mapCards(); kick off a fit on
    // the next frame after that completes.  ngOnInit's forkJoin schedules
    // the actual call; this hook just ensures mapWrapEl is bound first.
  }

  /** Window-level resize keeps the focal map fit-to-screen as users
   * resize the browser.  Throttled by rAF so the layout doesn't thrash. */
  @HostListener('window:resize')
  onWindowResize(): void {
    if (this.view() !== 'map') return;
    requestAnimationFrame(() => this.fitMapToScreen());
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

  // === MAP-mode state + computed routes ================================
  // The map shows the focal table at the centre with its 1-hop neighbours
  // arranged in a circle around it.  Cards are real <div>s, edges are SVG
  // cubic beziers connecting card borders, joining-column labels are
  // plain SVG text on the canvas (no pill background per the spec).

  hoveredCardId = signal<string | null>(null);
  searchQuery = signal('');
  mapZoom = signal(1);
  mapPanX = signal(0);
  mapPanY = signal(0);
  private mapDragging = false;
  private mapDragStartX = 0;
  private mapDragStartY = 0;
  private mapDragOriginX = 0;
  private mapDragOriginY = 0;

  mapCanvasTransform = computed(
    () => `translate(${this.mapPanX()}px, ${this.mapPanY()}px) scale(${this.mapZoom()})`,
  );

  /** Tables to render: focal + every distinct 1-hop neighbour. */
  mapCards = computed<{ id: string; label: string; rows: number; fieldCount: number; relCount: number; module: string | null; width: number; height: number; x: number; y: number; }[]>(() => {
    const focal = this.tableName;
    const out = this.outFks();
    const inb = this.inFks();
    const neighbours = new Set<string>();
    for (const f of out) neighbours.add(f.parentTable);
    for (const f of inb) neighbours.add(f.childTable);
    neighbours.delete(focal);

    // Per-table relationship count (using all edges, not just edges
    // adjacent to the focal — gives a sense of how connected each
    // neighbour is in the wider graph).
    const relCount = new Map<string, number>();
    for (const e of this.allEdges()) {
      relCount.set(e.from, (relCount.get(e.from) ?? 0) + 1);
      relCount.set(e.to, (relCount.get(e.to) ?? 0) + 1);
    }

    // Per-table column count from the inventory we already loaded.
    const colCount = new Map<string, number>();
    for (const c of this.allColumns()) {
      colCount.set(c.table, (colCount.get(c.table) ?? 0) + 1);
    }

    // Row count from the relationships graph nodes payload.
    // Loaded into allEdges only — we don't currently load the nodes for
    // map-mode; fall back to 0 when unknown.
    const rowCount = (id: string): number => {
      // The TABLE-mode header doesn't load the relationships nodes
      // payload either; rows are unknown for non-focal tables in this
      // pass.  We could lift them in a follow-up by fetching the
      // graph payload alongside columns/pii.
      return 0;
    };

    const CARD_W = 240;
    const CARD_H = 100;

    const list: { id: string; label: string; rows: number; fieldCount: number; relCount: number; module: string | null; width: number; height: number; x: number; y: number; }[] = [];

    // Focal at origin, neighbours around it.
    list.push({
      id: focal,
      label: focal,
      rows: rowCount(focal),
      fieldCount: colCount.get(focal) ?? 0,
      relCount: out.length + inb.length,
      module: this.moduleBadge(focal),
      width: CARD_W,
      height: CARD_H,
      x: 0, y: 0,
    });

    const N = neighbours.size;
    if (N > 0) {
      const cardDiag = Math.hypot(CARD_W, CARD_H);
      // Radius scales with neighbour count so cards never overlap on the
      // circumference: circumference ≥ N * cardDiag * 1.05.
      const radius = Math.max(280, (N * cardDiag * 1.05) / (2 * Math.PI) + 60);
      const startAngle = -Math.PI / 2; // top
      let i = 0;
      for (const nb of neighbours) {
        const angle = startAngle + (2 * Math.PI * i) / N;
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;
        list.push({
          id: nb,
          label: nb,
          rows: rowCount(nb),
          fieldCount: colCount.get(nb) ?? 0,
          relCount: relCount.get(nb) ?? 0,
          module: this.moduleBadge(nb),
          width: CARD_W,
          height: CARD_H,
          x, y,
        });
        i++;
      }
    }

    // Translate so the leftmost / topmost card sits at margin (80, 80).
    let minX = Infinity, minY = Infinity;
    for (const c of list) {
      minX = Math.min(minX, c.x);
      minY = Math.min(minY, c.y);
    }
    const margin = 80;
    for (const c of list) {
      c.x = c.x - minX + margin;
      c.y = c.y - minY + margin;
    }
    return list;
  });

  /** SVG bezier paths between focal and each neighbour. */
  mapEdges = computed(() => {
    const cards = this.mapCards();
    const byId = new Map(cards.map(c => [c.id, c]));
    const focal = this.tableName;
    const focalCard = byId.get(focal);
    if (!focalCard) return [];

    interface MapEdge {
      id: string;
      color: string;
      joinLabel: string;
      path: string;
      midX: number;
      midY: number;
      fromGlyph: string;
      toGlyph: string;
      fromGlyphX: number;
      fromGlyphY: number;
      toGlyphX: number;
      toGlyphY: number;
      fromTable: string;
      toTable: string;
    }
    const edges: MapEdge[] = [];

    let i = 0;
    for (const f of this.outFks()) {
      const nb = byId.get(f.parentTable);
      if (!nb) continue;
      const r = this.bezierBetween(focalCard, nb);
      const t = this.classifyRelType(f);
      const join = f.childCol === f.parentCol ? f.childCol : `${f.childCol} → ${f.parentCol}`;
      const fromCard = f.cardinality;
      const fromGlyph = (fromCard === 'MANY_TO_ONE' || fromCard === 'MANY_TO_MANY') ? '>' : '|';
      const toGlyph = (fromCard === 'ONE_TO_MANY' || fromCard === 'MANY_TO_MANY') ? '<' : '|';
      edges.push({
        id: `mo${i++}`,
        color: this.relTypeColor(t),
        joinLabel: join,
        path: r.path,
        midX: r.midX,
        midY: r.midY,
        fromGlyph,
        toGlyph,
        fromGlyphX: r.fromX + (r.fromOnRight ? 4 : -10),
        fromGlyphY: r.fromY + 4,
        toGlyphX: r.toX + (r.toOnRight ? 4 : -10),
        toGlyphY: r.toY + 4,
        fromTable: focal,
        toTable: f.parentTable,
      });
    }
    for (const f of this.inFks()) {
      const nb = byId.get(f.childTable);
      if (!nb) continue;
      const r = this.bezierBetween(nb, focalCard);
      const t = this.classifyRelType(f);
      const join = f.childCol === f.parentCol ? f.childCol : `${f.childCol} → ${f.parentCol}`;
      const fromCard = f.cardinality;
      const fromGlyph = (fromCard === 'MANY_TO_ONE' || fromCard === 'MANY_TO_MANY') ? '>' : '|';
      const toGlyph = (fromCard === 'ONE_TO_MANY' || fromCard === 'MANY_TO_MANY') ? '<' : '|';
      edges.push({
        id: `mi${i++}`,
        color: this.relTypeColor(t),
        joinLabel: join,
        path: r.path,
        midX: r.midX,
        midY: r.midY,
        fromGlyph,
        toGlyph,
        fromGlyphX: r.fromX + (r.fromOnRight ? 4 : -10),
        fromGlyphY: r.fromY + 4,
        toGlyphX: r.toX + (r.toOnRight ? 4 : -10),
        toGlyphY: r.toY + 4,
        fromTable: f.childTable,
        toTable: focal,
      });
    }
    return edges;
  });

  mapContentSize = computed(() => {
    let maxX = 0, maxY = 0;
    for (const c of this.mapCards()) {
      maxX = Math.max(maxX, c.x + c.width);
      maxY = Math.max(maxY, c.y + c.height);
    }
    return { w: maxX + 80, h: maxY + 80 };
  });

  mapModuleBadge = computed(() => this.moduleBadge(this.tableName));

  searchHits = computed<string[]>(() => {
    const q = this.searchQuery().trim().toLowerCase();
    if (!q || q.length < 2) return [];
    const all = new Set<string>();
    for (const e of this.allEdges()) {
      all.add(e.from);
      all.add(e.to);
    }
    return [...all]
      .filter(t => t.toLowerCase().includes(q))
      .sort()
      .slice(0, 8);
  });

  // --- MAP interactions ---------------------------------------------------

  onMapWheel(ev: WheelEvent): void {
    ev.preventDefault();
    const wrap = (ev.currentTarget as HTMLElement);
    const rect = wrap.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const oldZoom = this.mapZoom();
    const factor = ev.deltaY > 0 ? 0.9 : 1.1;
    const z = Math.min(2.5, Math.max(0.2, oldZoom * factor));
    const px = this.mapPanX();
    const py = this.mapPanY();
    const cx = (mx - px) / oldZoom;
    const cy = (my - py) / oldZoom;
    this.mapPanX.set(mx - cx * z);
    this.mapPanY.set(my - cy * z);
    this.mapZoom.set(z);
  }

  onMapMouseDown(ev: MouseEvent): void {
    const target = ev.target as HTMLElement;
    if (target.closest('.map-card') || target.closest('.map-pill') ||
        target.closest('.map-search') || target.closest('.map-legend') ||
        target.closest('.map-export') || target.closest('.map-chip')) return;
    this.mapDragging = true;
    this.mapDragStartX = ev.clientX;
    this.mapDragStartY = ev.clientY;
    this.mapDragOriginX = this.mapPanX();
    this.mapDragOriginY = this.mapPanY();
  }
  onMapMouseMove(ev: MouseEvent): void {
    if (!this.mapDragging) return;
    this.mapPanX.set(this.mapDragOriginX + (ev.clientX - this.mapDragStartX));
    this.mapPanY.set(this.mapDragOriginY + (ev.clientY - this.mapDragStartY));
  }
  onMapMouseUp(_ev: MouseEvent): void { this.mapDragging = false; }

  onMapCardClick(n: { id: string }): void {
    if (n.id === this.tableName) return;
    // Promote neighbour to focal — navigate to its map view.
    this.router.navigate(['/jobs', this.jobId, 'tables', n.id], {
      queryParams: { view: 'map' },
    });
  }

  onSearchInput(ev: Event): void {
    this.searchQuery.set((ev.target as HTMLInputElement).value);
  }

  exportMap(): void {
    // Copy a Mermaid snippet covering the focal + neighbours to clipboard.
    const lines: string[] = [`%% Archon-SuperNova focal map: ${this.tableName}`, 'erDiagram'];
    for (const e of this.mapEdges()) {
      lines.push(`  ${e.fromTable} ||--o{ ${e.toTable} : "${e.joinLabel}"`);
    }
    const snippet = lines.join('\n');
    if (navigator.clipboard) {
      navigator.clipboard.writeText(snippet).catch(() => { /* clipboard denied */ });
    }
  }

  isCardAdjacentToHover(cardId: string): boolean {
    const h = this.hoveredCardId();
    if (!h) return false;
    if (cardId === this.tableName) return true;
    if (cardId === h) return true;
    // Edge between the hovered card and this card?
    return this.mapEdges().some(e =>
      (e.fromTable === h && e.toTable === cardId) ||
      (e.toTable === h && e.fromTable === cardId),
    );
  }

  isEdgeAdjacentToHover(e: { fromTable: string; toTable: string }): boolean {
    const h = this.hoveredCardId();
    if (!h) return true;
    return e.fromTable === h || e.toTable === h;
  }

  /** Cubic-bezier path between the BORDER of card a and card b.  Picks the
   * side of each card facing the other so the curve never crosses a card. */
  private bezierBetween(
    a: { x: number; y: number; width: number; height: number },
    b: { x: number; y: number; width: number; height: number },
  ): {
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

  private moduleBadge(tableName: string): string | null {
    if (!tableName) return null;
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
        // Deep-link case: ?view=map on first load — once the data lands
        // and the map block has been projected, fit the radial layout to
        // the new full-viewport canvas.  rAF + setTimeout(50) covers
        // first-paint + a relayout cushion.
        if (this.view() === 'map') {
          requestAnimationFrame(() => this.fitMapToScreen());
          setTimeout(() => this.fitMapToScreen(), 50);
        }
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
