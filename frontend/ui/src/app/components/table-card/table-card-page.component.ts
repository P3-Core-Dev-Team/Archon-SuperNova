import {
  AfterViewInit, Component, ElementRef, EventEmitter, HostListener,
  Input, OnChanges, OnInit, Output, ViewChild, computed, inject, signal,
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
  /** IIN/BIN-derived brand list for CC_NUMBER columns — sorted by
   * descending count so the dominant scheme renders first.  Empty for
   * non-card columns. */
  brands: string[];
  /** Regulation tags from the backend (PCI / GDPR / HIPAA / …),
   * deduplicated across this column's PII findings.  Lets the UI
   * group e.g. card_holder_name + card_number + cvv under one PCI
   * badge without forcing the user to interpret raw type names. */
  regulations: string[];
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
  host: {
    '[class.embedded]': 'embedded',
  },
  template: `
    @if (!embedded) {
      <a [routerLink]="['/jobs', jobId]" class="back">← Back to job</a>
    }

    @if (loading()) {
      <p class="muted">Loading…</p>
    }

    @if (error()) {
      <div class="error card">{{ error() }}</div>
    }

    @if (!loading() && !error()) {
      <!-- Page header: table name + description on the left, table | map
           toggle on the right.  Shared between both modes per the spec.
           Hidden when embedded — the parent (Relationships tab) provides
           the header + a 3-state overview/map/table toggle. -->
      @if (!embedded) {
      <div class="page-header">
        <div class="title-row">
          <h1 class="mono">{{ tableName() }}</h1>
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
      }

      @if (!embedded) {
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
      }

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
            <span class="mono pill-name">{{ tableName() }}</span>
          </div>
          <div class="map-chip top-left chip-below muted small">
            <span class="conn-count">
              {{ mapEdges().length }}@if (mapEdges().length !== totalMapEdgeCount()) {<span class="of-total">/{{ totalMapEdgeCount() }}</span>}
              connection{{ mapEdges().length === 1 ? '' : 's' }}
            </span>
            <span class="chip-divider">·</span>
            <label class="conf-slider-label"
                   title="Hide edges with confidence below this threshold">
              <span class="conf-label">min&nbsp;conf</span>
              <input type="range" min="0" max="1" step="0.05"
                     [value]="minMapConfidence()"
                     (input)="onMapConfidenceChange($event)" />
              <span class="mono conf-val">{{ minMapConfidence() | number:'1.2-2' }}</span>
            </label>
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
                     href="javascript:void(0)"
                     (click)="onSearchHitClick(h)">{{ h }}</a>
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
                <g class="map-edge-group"
                   [class.dimmed]="hoveredCardId() && !isEdgeAdjacentToHover(e)"
                   [class.trail]="e.isTrail">
                  <path class="map-edge"
                        [class.trail]="e.isTrail"
                        [attr.d]="e.path"
                        [attr.stroke]="e.color"
                        [attr.stroke-dasharray]="e.dashArray || null"
                        [attr.stroke-opacity]="e.strokeOpacity" />
                  <!-- Cardinality glyphs at endpoints -->
                  <text class="map-glyph"
                        [attr.x]="e.fromGlyphX"
                        [attr.y]="e.fromGlyphY"
                        [attr.fill]="e.color">{{ e.fromGlyph }}</text>
                  <text class="map-glyph"
                        [attr.x]="e.toGlyphX"
                        [attr.y]="e.toGlyphY"
                        [attr.fill]="e.color">{{ e.toGlyph }}</text>
                  <!-- Joining column label, plain text on canvas (no pill).
                       Confidence renders as a smaller dimmed line beneath
                       it so the strength of each edge is visible at a
                       glance — matches the relationship-graph slider. -->
                  @if (e.joinLabel) {
                    <text class="map-edge-label"
                          [attr.x]="e.midX"
                          [attr.y]="e.midY"
                          text-anchor="middle"
                          [attr.fill]="e.isTrail ? '#f0b429' : '#c9d1d9'">{{ e.joinLabel }}</text>
                  }
                  @if (e.confLabel && !e.isTrail) {
                    <text class="map-edge-conf"
                          [attr.x]="e.midX"
                          [attr.y]="e.midY + 12"
                          text-anchor="middle"
                          [attr.fill]="confLabelColor(e.confidence)">{{ e.confLabel }}</text>
                  }
                  @if (e.isTrail && e.trailLabel) {
                    <text class="map-trail-label"
                          [attr.x]="e.midX"
                          [attr.y]="e.midY + 14"
                          text-anchor="middle"
                          [attr.fill]="'#f0b429'">{{ e.trailLabel }}</text>
                  }
                </g>
              }
            </svg>

            @for (n of mapCards(); track n.id) {
              <div class="map-card"
                   [class.focal]="n.id === tableName()"
                   [class.trail]="n.isTrail"
                   [class.dragging]="cardDrag?.cardId === n.id && cardDrag?.hasMoved"
                   [class.dim]="hoveredCardId() && hoveredCardId() !== n.id && !isCardAdjacentToHover(n.id)"
                   [style.left.px]="n.x"
                   [style.top.px]="n.y"
                   [style.width.px]="n.width"
                   (mouseenter)="hoveredCardId.set(n.id)"
                   (mouseleave)="hoveredCardId.set(null)"
                   (mousedown)="onCardMouseDown($event, n)">
                @if (n.isTrail) {
                  <span class="trail-step-badge"
                        [title]="'Click to jump back to this point in your trail'">
                    {{ n.trailIndex + 1 }}
                  </span>
                }
                <div class="card-head">
                  <span class="card-table mono">{{ n.label }}</span>
                  @if (n.module) { <span class="card-module">{{ n.module }}</span> }
                </div>
                <div class="card-desc">
                  {{ n.rows | number }} row{{ n.rows === 1 ? '' : 's' }}
                  @if (n.id !== tableName()) {
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
                        @if (c.regulations.includes('PCI')) {
                          <!-- PCI cardholder data: collapse the multiple
                               supplementary chips (Card Number / Card
                               Name / CVV + brand chips) into a single
                               flat [PCI] tag.  Detail goes to the
                               tooltip so the row stays scannable. -->
                          <span class="kbadge reg-tag reg-pci"
                                [title]="pciColumnTooltip(c)">PCI</span>
                        } @else {
                          @for (reg of c.regulations; track reg) {
                            <span class="kbadge reg-tag" [class]="'reg-' + reg.toLowerCase()"
                                  [title]="reg + ' regulated data'">{{ reg }}</span>
                          }
                          @for (p of c.pii_types; track p) {
                            <span class="kbadge pii" [title]="p">{{ piiLabel(p) }}</span>
                          }
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
    /* Standalone (route) mode keeps a comfortable max-width so the
     * fields/relationships split doesn't stretch absurdly wide.  When
     * embedded inside the job-detail Relationships tab, the parent
     * decides the width — the :host-context override below clears
     * this cap so the embedded view follows the page width. */
    :host { display: block; max-width: 1500px; margin: 0 auto; padding: 0 4px; }
    :host-context(.embedded), :host(.embedded) { max-width: none; }
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
    }
    /* Embedded mode: the parent (Relationships tab) constrains size.  Keep
       width 100% inside its own column; use a fixed-but-comfortable height
       since we no longer own the viewport. */
    :host-context(.embedded) .map-wrap, .embedded .map-wrap {
      width: 100%;
      margin-left: 0;
      margin-right: 0;
      height: 720px;
      min-height: 480px;
      border-radius: 8px;
      border: 1px solid #30363d;
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
    /* Trail edges: thicker gold stroke + subtle drop shadow so the
       drill-down path stands out as a single connected thread on top of
       the regular FK-relationship edges. */
    .map-edge.trail {
      stroke-width: 3.2;
      filter: drop-shadow(0 0 3px rgba(240, 180, 41, 0.55));
    }
    .map-edge-group.trail { opacity: 1 !important; }
    .map-trail-label {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.4px;
      pointer-events: none;
      paint-order: stroke;
      stroke: #0d1117;
      stroke-width: 4px;
      stroke-linejoin: round;
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
    /* Numeric confidence rendered as a smaller, dimmer line under the
       join-column label.  Same paint-order trick gives readability on
       top of cards/edges without a backing pill. */
    .map-edge-conf {
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 10px;
      font-weight: 600;
      pointer-events: none;
      paint-order: stroke;
      stroke: #0d1117;
      stroke-width: 4px;
      stroke-linejoin: round;
    }
    /* Confidence slider chip — sits next to the connection count chip
       so the user can dial the visible-edge threshold without leaving
       the canvas. */
    .map-chip .conn-count { white-space: nowrap; }
    .map-chip .conn-count .of-total { color: #6e7681; margin-right: 1px; }
    .map-chip .chip-divider { color: #30363d; margin: 0 6px; }
    .map-chip .conf-slider-label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .map-chip .conf-slider-label input[type=range] {
      width: 90px;
      vertical-align: middle;
    }
    .map-chip .conf-label { color: #8b949e; }
    .map-chip .conf-val {
      min-width: 32px;
      text-align: right;
      color: #58a6ff;
      font-weight: 600;
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
    }
    /* Trail predecessor cards — gold border so the chain reads as one
       visual unit alongside the matching gold path edges.  The numbered
       badge in the corner shows the user's hop order (1, 2, 3 …). */
    .map-card.trail {
      border: 2px solid #f0b429;
      padding: 9px 11px;
      background: linear-gradient(180deg, rgba(240, 180, 41, 0.08) 0%, #161b22 60%);
    }
    .map-card.trail:hover { border-color: #ffd166; }
    .trail-step-badge {
      position: absolute;
      top: -10px;
      left: -10px;
      width: 22px;
      height: 22px;
      background: #f0b429;
      color: #0d1117;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: 700;
      font-family: ui-monospace, SFMono-Regular, monospace;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
      z-index: 2;
    }
    .map-card.dim { opacity: 0.35; }
    /* Active-drag visuals: lift the card slightly and disable the smooth
       transitions so the cursor follows position 1:1 without lag. */
    .map-card.dragging {
      cursor: grabbing;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4),
                  0 8px 24px rgba(0, 0, 0, 0.42);
      transition: none;
      z-index: 4;
    }

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
      /* ~55% fields / ~45% relationships per the wide-viewport spec.
       * Both columns have min-widths so neither collapses on
       * mid-width screens (≈1280-1500px). */
      grid-template-columns: minmax(540px, 55fr) minmax(440px, 45fr);
      gap: 24px;
      align-items: start;
    }
    @media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }

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
    /* Regulation tags — one chip per regulatory framework that gates
       the column's findings (PCI / GDPR / HIPAA / …).  Drawn before
       the pii_type chips so a row reads "[PCI] Card Number" l-to-r. */
    .kbadge.reg-tag { font-size: 9.5px; border: 1px solid currentColor; }
    .kbadge.reg-pci   { color: #ff7b8b; background: rgba(235, 0, 27, 0.16); }
    .kbadge.reg-gdpr  { color: #79c0ff; background: rgba(121, 192, 255, 0.16); }
    .kbadge.reg-hipaa { color: #56d364; background: rgba(86, 211, 100, 0.16); }
    .kbadge.reg-ccpa  { color: #d2a8ff; background: rgba(210, 168, 255, 0.16); }
    .kbadge.reg-sox   { color: #d29922; background: rgba(210, 153, 34, 0.16); }
    .kbadge.reg-dpdpa { color: #ffa05a; background: rgba(255, 96, 0, 0.16); }
    /* IIN/BIN brand chips, rendered next to the CC_NUMBER pii kbadge.
       Subtle outlined fill so the brand name is unmistakable but the
       chip doesn't compete with the primary PII tag for attention. */
    .kbadge.brand-tag { font-size: 9.5px; border: 1px solid currentColor; }
    .kbadge.brand-visa       { color: #6f8aff; background: rgba(26, 31, 113, 0.18); }
    .kbadge.brand-mastercard { color: #ff7b8b; background: rgba(235, 0, 27, 0.16); }
    .kbadge.brand-amex       { color: #7cb3e8; background: rgba(46, 119, 187, 0.18); }
    .kbadge.brand-discover   { color: #ffa05a; background: rgba(255, 96, 0, 0.16); }
    .kbadge.brand-diners     { color: #66b2e0; background: rgba(0, 121, 190, 0.18); }
    .kbadge.brand-jcb        { color: #739dd0; background: rgba(14, 76, 150, 0.18); }
    .kbadge.brand-unionpay   { color: #ff8090; background: rgba(209, 4, 41, 0.16); }
    .kbadge.brand-maestro    { color: #a8a7ee; background: rgba(108, 107, 189, 0.18); }
    .kbadge.brand-rupay      { color: #4cb6a6; background: rgba(9, 121, 105, 0.18); }
    .kbadge.brand-mir        { color: #88e09a; background: rgba(77, 180, 94, 0.18); }

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
export class TableCardPageComponent implements OnInit, OnChanges, AfterViewInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private jobsSvc = inject(JobService);

  // Reference to the map's bordered canvas wrapper so fit-to-screen can
  // measure its real size after the viewport-bleed CSS settles.
  @ViewChild('mapWrap') private mapWrapEl?: ElementRef<HTMLDivElement>;

  /**
   * Embedded mode — set by a parent component (the Relationships tab on
   * the job-detail page) when the page renders this component inline
   * instead of as a top-level route.  In embedded mode we:
   *   - hide our own "← Back to job" link and the top page-header
   *     (the parent owns the page chrome)
   *   - hide the "table | map" pill toggle (the parent provides a
   *     three-state overview/map/table toggle)
   *   - emit ``tableSelected`` instead of router-navigating when a
   *     neighbour-card click or search-hit click would normally promote
   *     a different table.
   */
  @Input({ alias: 'embedded' }) embedded = false;
  @Input({ alias: 'jobId' })    inputJobId?: string;
  @Input({ alias: 'tableName' }) inputTableName?: string;
  @Input({ alias: 'view' })      inputView?: 'table' | 'map';

  /** Fired when a neighbour-card click (map mode) or a search-hit click
   * would have navigated to a different focal table.  The parent uses
   * this to update its own ``?table=`` query param without us touching
   * the router.  Only fires while ``embedded`` is true. */
  @Output() tableSelected = new EventEmitter<{ table: string; view: 'table' | 'map' }>();

  jobId = '';
  /** Reactive focal-table name driven by the route's :table_name param.
   * Must be a signal (not a plain property) so the computed cards/edges/
   * outFks/inFks/groupedRelationships recompute when the user navigates
   * between tables (search hit click, neighbour-card click, browser
   * forward / back) — the same component instance is reused on a sibling
   * route change, so ngOnInit doesn't re-fire. */
  tableName = signal('');

  // 'table' (queryviz field-detail page) | 'map' (focal-table 1-hop graph).
  // Synced with the URL ?view= param so the toggle is shareable + bookmarkable.
  view = signal<'table' | 'map'>('table');

  loading = signal(true);
  error = signal<string | null>(null);

  job = signal<Job | null>(null);
  private allColumns = signal<ColumnInfo[]>([]);
  private allEdges = signal<RelationshipEdge[]>([]);
  private allPii = signal<PiiFinding[]>([]);
  /** Per-table row count from the relationships graph nodes payload.
   * Used by the map view's neighbour-card subtitle.  Null when the
   * relationships request hasn't completed yet. */
  private rowCountByTable = signal<Map<string, number>>(new Map());

  setView(v: 'table' | 'map'): void {
    if (this.view() === v) return;
    this.view.set(v);
    if (this.embedded) {
      // Parent (Relationships tab) owns the URL.  Tell it the user
      // toggled view; the parent updates its query params.
      this.tableSelected.emit({ table: this.tableName(), view: v });
      if (v === 'map') {
        requestAnimationFrame(() => this.fitMapToScreen());
        setTimeout(() => this.fitMapToScreen(), 50);
      }
      return;
    }
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

  /**
   * Called from the parent (when embedded) whenever the @Input table-name
   * changes, OR from the paramMap subscriber in route-driven mode.  Resets
   * transient state and refits the radial layout when MAP mode is active.
   */
  private onFocalChange(newTbl: string): void {
    if (newTbl === this.tableName()) return;
    this.tableName.set(newTbl);
    this.searchQuery.set('');
    this.hoveredCardId.set(null);
    this.cardOffsets.set({});
    if (this.view() === 'map') {
      requestAnimationFrame(() => this.fitMapToScreen());
      setTimeout(() => this.fitMapToScreen(), 50);
    }
  }

  /** ngOnChanges fires whenever the parent rebinds an @Input.  In
   * embedded mode this is how we pick up table-switch / view-switch
   * commands from the Relationships tab. */
  ngOnChanges(): void {
    if (!this.embedded) return;
    if (this.inputTableName && this.inputTableName !== this.tableName()) {
      this.onFocalChange(this.inputTableName);
    }
    if (this.inputView && this.inputView !== this.view()) {
      this.view.set(this.inputView);
      if (this.inputView === 'map') {
        requestAnimationFrame(() => this.fitMapToScreen());
        setTimeout(() => this.fitMapToScreen(), 50);
      }
    }
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
    const tbl = this.tableName();
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
    set.delete(this.tableName());
    return set.size;
  });

  // === MAP-mode state + computed routes ================================
  // The map shows the focal table at the centre with its 1-hop neighbours
  // arranged in a circle around it.  Cards are real <div>s, edges are SVG
  // cubic beziers connecting card borders, joining-column labels are
  // plain SVG text on the canvas (no pill background per the spec).

  hoveredCardId = signal<string | null>(null);
  searchQuery = signal('');
  /** Minimum FK confidence threshold for the map view.  Edges (and any
   * neighbour cards that no longer have a surviving edge) are hidden
   * when their confidence is below this value.  Mirrors the slider on
   * the all-tables relationship-graph component so users can dial
   * down noise on either view consistently.  Default 0 = show all. */
  minMapConfidence = signal(0);
  mapZoom = signal(1);
  mapPanX = signal(0);
  mapPanY = signal(0);
  private mapDragging = false;
  private mapDragStartX = 0;
  private mapDragStartY = 0;
  private mapDragOriginX = 0;
  private mapDragOriginY = 0;

  /**
   * User-applied position offsets per card.  Layered ON TOP of the radial
   * layout in mapCards() so dragging a card shifts only its position; the
   * underlying layout output is unchanged so re-fits still work.  Cleared
   * when the focal table changes (a new layout starts fresh).
   */
  cardOffsets = signal<Record<string, { dx: number; dy: number }>>({});

  /**
   * In-progress drag state.  ``hasMoved`` flips true when the cursor has
   * travelled the threshold (5 px in screen-space) since the mousedown —
   * below threshold the gesture is treated as a click (promote-to-focal),
   * above it the gesture is a drag (move the card).  Captured on
   * mousedown of any .map-card, finalised on the wrap's mouseup.
   */
  // Public so the template can read it for the [class.dragging] binding.
  // Read-only from the template's perspective; only handlers below mutate it.
  cardDrag: {
    cardId: string;
    startCursorX: number;
    startCursorY: number;
    startOffsetX: number;
    startOffsetY: number;
    hasMoved: boolean;
  } | null = null;
  private static readonly CARD_DRAG_THRESHOLD = 5;

  mapCanvasTransform = computed(
    () => `translate(${this.mapPanX()}px, ${this.mapPanY()}px) scale(${this.mapZoom()})`,
  );

  /** Trail predecessors — every focal table the user visited BEFORE
   * the current one.  Rendered as a left-to-right chain of cards on
   * the canvas with highlighted "path" edges between consecutive
   * trail entries, so the user sees their drill-down history baked
   * into the graph itself (no breadcrumb strip required). */
  private trailPredecessors = computed<string[]>(() => {
    const trail = this.jobsSvc.focalTrail();
    const focal = this.tableName();
    // Drop the LAST entry if it equals the focal — that's the focal
    // itself and is already laid out at the canvas centre.
    const idx = trail.lastIndexOf(focal);
    return idx >= 0 ? trail.slice(0, idx) : trail;
  });

  /** Pure auto-layout output (radial).  Doesn't include user drag-offsets;
   * those are layered on in ``mapCards``.  Splitting the two so a re-fit
   * after window resize keeps using the radial coordinates while the
   * dragged-card overrides survive. */
  private mapLayoutCards = computed<{ id: string; label: string; rows: number; fieldCount: number; relCount: number; module: string | null; width: number; height: number; x: number; y: number; isTrail: boolean; trailIndex: number; }[]>(() => {
    const focal = this.tableName();
    const trailPred = this.trailPredecessors();
    const trailSet = new Set(trailPred);
    // Use the confidence-filtered FK set so neighbour cards that no
    // longer have any surviving edge fall out of the layout — keeps
    // orphans off the canvas when the slider is dragged high.
    const out = this.visibleOutFks();
    const inb = this.visibleInFks();
    const neighbours = new Set<string>();
    for (const f of out) neighbours.add(f.parentTable);
    for (const f of inb) neighbours.add(f.childTable);
    neighbours.delete(focal);
    // Predecessors get their own slot on the left — exclude them from
    // the radial neighbour ring so they don't get drawn twice.
    for (const t of trailSet) neighbours.delete(t);

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

    // Row count looked up in the rowCountByTable signal that ngOnInit
    // populates from the relationships nodes payload.  Falls back to
    // 0 when the lookup hasn't loaded yet (which only happens during
    // the initial render before forkJoin resolves).
    const rcLookup = this.rowCountByTable();
    const rowCount = (id: string): number => rcLookup.get(id) ?? 0;

    const CARD_W = 240;
    const CARD_H = 100;

    const list: { id: string; label: string; rows: number; fieldCount: number; relCount: number; module: string | null; width: number; height: number; x: number; y: number; isTrail: boolean; trailIndex: number; }[] = [];

    // Focal at origin, predecessors stacked horizontally to the LEFT,
    // remaining neighbours fanning out radially on the right half.
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
      isTrail: false,
      trailIndex: -1,
    });

    // === Trail predecessors: chain to the LEFT of focal at y=0 ======
    // a → b → focal renders as [a card] → [b card] → [focal card] all
    // on the same horizontal line, with path-edges drawn distinctly in
    // mapEdges().  The chain is laid out in trail-order so the oldest
    // hop is leftmost.
    const TRAIL_STEP = CARD_W + 120;       // gap between predecessor cards
    const trailLen = trailPred.length;
    for (let ti = 0; ti < trailLen; ti++) {
      const tName = trailPred[ti];
      const distFromFocal = trailLen - ti; // 1, 2, 3 …
      list.push({
        id: tName,
        label: tName,
        rows: rowCount(tName),
        fieldCount: colCount.get(tName) ?? 0,
        relCount: relCount.get(tName) ?? 0,
        module: this.moduleBadge(tName),
        width: CARD_W,
        height: CARD_H,
        x: -distFromFocal * TRAIL_STEP,
        y: 0,
        isTrail: true,
        trailIndex: ti,
      });
    }

    const N = neighbours.size;
    if (N > 0) {
      const cardDiag = Math.hypot(CARD_W, CARD_H);
      // Radius scales with neighbour count so cards never overlap on the
      // circumference: circumference ≥ N * cardDiag * 1.05.
      const radius = Math.max(280, (N * cardDiag * 1.05) / (2 * Math.PI) + 60);
      // When a trail exists, lay neighbours on the right HALF (-π/2 to π/2)
      // so they never collide with the predecessor chain coming in from
      // the left.  Without a trail, fall back to the full circle.
      const halfArc = trailLen > 0;
      const arcStart = halfArc ? -Math.PI / 2 : -Math.PI / 2;
      const arcSpan  = halfArc ?  Math.PI    :  2 * Math.PI;
      let i = 0;
      for (const nb of neighbours) {
        const t = N === 1 ? 0.5 : (i / (N - 1));
        const angle = halfArc
          ? arcStart + arcSpan * t       // even spread across the half-arc
          : arcStart + (arcSpan * i) / N;
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
          isTrail: false,
          trailIndex: -1,
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

  /** Render-time card list = auto-layout positions + per-card user
   * drag-offsets.  Edges depend on this so they re-route the moment a
   * drag updates ``cardOffsets``. */
  mapCards = computed<{ id: string; label: string; rows: number; fieldCount: number; relCount: number; module: string | null; width: number; height: number; x: number; y: number; isTrail: boolean; trailIndex: number; }[]>(() => {
    const layout = this.mapLayoutCards();
    const offsets = this.cardOffsets();
    return layout.map(c => {
      const off = offsets[c.id];
      return off ? { ...c, x: c.x + off.dx, y: c.y + off.dy } : c;
    });
  });

  /** SVG bezier paths between focal and each neighbour with anchor
   * distribution (multiple edges leaving the same side spread along that
   * side instead of stacking) + label-vs-card collision avoidance (a
   * label whose midpoint falls on a card slides along the curve to clear
   * space).  Recomputes any time the cards change — including drag. */
  mapEdges = computed(() => {
    const cards = this.mapCards();
    const cardById = new Map(cards.map(c => [c.id, c]));
    const focal = this.tableName();
    const focalCard = cardById.get(focal);
    if (!focalCard) return [];

    type Side = 'top' | 'right' | 'bottom' | 'left';
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
      // Confidence integration: numeric value + a pre-formatted display
      // label, plus stroke styling derived from the bucket so weak FKs
      // visually fade.  null confidence renders as a solid full-opacity
      // edge with no numeric label.
      confidence: number | null;
      confLabel: string;
      dashArray: string;
      strokeOpacity: number;
      /** True when the edge is part of the user's drill-down trail
       * (e.g. a→b→focal).  Trail edges render with a thicker gold
       * stroke and a "step N" label so the path is unmistakable in
       * the canvas. */
      isTrail: boolean;
      trailLabel: string;
    }
    interface Proto {
      id: string;
      from: { id: string; x: number; y: number; width: number; height: number };
      to:   { id: string; x: number; y: number; width: number; height: number };
      fk: FkRow;
      relType: RelType;
      isTrail?: boolean;
      trailStep?: number;
    }

    const protos: Proto[] = [];
    let pid = 0;
    // Iterate the *visible* FK set so the slider drives both the
    // card-set (via mapLayoutCards) and the edge-set together —
    // dropping a low-conf edge naturally drops its neighbour card too
    // unless that card is held by another edge.
    for (const f of this.visibleOutFks()) {
      const nb = cardById.get(f.parentTable);
      if (!nb) continue;
      protos.push({
        id: `mo${pid++}`,
        from: focalCard,
        to: nb,
        fk: f,
        relType: this.classifyRelType(f),
      });
    }
    for (const f of this.visibleInFks()) {
      const nb = cardById.get(f.childTable);
      if (!nb) continue;
      protos.push({
        id: `mi${pid++}`,
        from: nb,
        to: focalCard,
        fk: f,
        relType: this.classifyRelType(f),
      });
    }

    // === Trail path edges ===========================================
    // For trail [a, b, focal] add edges a→b and b→focal so the user's
    // drill-down chain is drawn as a connected gold path on the canvas.
    // When a real FK between consecutive trail tables exists in the
    // discovery graph we use it (so the joining-column label is real);
    // otherwise we synthesise a phantom edge with a UNKNOWN cardinality
    // so the user still sees the chain.
    const trailPred = this.trailPredecessors();
    const trailChain = [...trailPred, focal];
    for (let i = 0; i + 1 < trailChain.length; i++) {
      const aId = trailChain[i];
      const bId = trailChain[i + 1];
      const aCard = cardById.get(aId);
      const bCard = cardById.get(bId);
      if (!aCard || !bCard) continue;
      // Look up an actual FK row from allEdges() between these tables
      // so the join label is meaningful when one exists.
      const fk = this.allEdges().find(
        e => (e.from === aId && e.to === bId) || (e.from === bId && e.to === aId),
      );
      const parsedFk: FkRow = fk
        ? this.parseEdge(fk)
        : {
            childTable: aId,
            childCol: '',
            parentTable: bId,
            parentCol: '',
            cardinality: null,
            confidence: null,
          };
      protos.push({
        id: `mt${pid++}`,
        from: aCard,
        to: bCard,
        fk: parsedFk,
        relType: this.classifyRelType(parsedFk),
        isTrail: true,
        trailStep: i + 1,
      });
    }

    if (protos.length === 0) return [];

    // === Anchor distribution =========================================
    // For each proto-edge we pick the side of each card that faces the
    // other endpoint, then group all endpoints by (cardId, side) so we
    // can spread them along the side instead of all attaching at the
    // centre.
    interface Endpoint {
      protoId: string;
      cardId: string;
      side: Side;
      otherCx: number; // centre of the OTHER card — used to sort within a side
      otherCy: number;
    }
    const endpoints: Endpoint[] = [];
    for (const p of protos) {
      const aCx = p.from.x + p.from.width / 2;
      const aCy = p.from.y + p.from.height / 2;
      const bCx = p.to.x + p.to.width / 2;
      const bCy = p.to.y + p.to.height / 2;
      endpoints.push({
        protoId: p.id, cardId: p.from.id,
        side: this.sideFacing(p.from, bCx, bCy),
        otherCx: bCx, otherCy: bCy,
      });
      endpoints.push({
        protoId: p.id, cardId: p.to.id,
        side: this.sideFacing(p.to, aCx, aCy),
        otherCx: aCx, otherCy: aCy,
      });
    }

    // Group by (cardId, side) and assign attachment points along the
    // side at fractions (i + 1) / (N + 1) — 0.5 for one edge, 0.33 / 0.67
    // for two, 0.25 / 0.5 / 0.75 for three, etc.
    const groups = new Map<string, Endpoint[]>();
    for (const e of endpoints) {
      const k = `${e.cardId}|${e.side}`;
      const arr = groups.get(k);
      if (arr) arr.push(e); else groups.set(k, [e]);
    }
    const attach = new Map<string, { x: number; y: number; side: Side }>();
    for (const [k, group] of groups) {
      const card = cardById.get(group[0].cardId)!;
      const side = group[0].side;
      const horiz = side === 'top' || side === 'bottom';
      // Sort by the position along the side that points toward the other
      // card — gives stable, untangled ordering.
      group.sort((a, b) =>
        horiz ? a.otherCx - b.otherCx : a.otherCy - b.otherCy,
      );
      const N = group.length;
      for (let i = 0; i < N; i++) {
        const tt = (i + 1) / (N + 1);
        const pt = this.pointOnSide(card, side, tt);
        attach.set(`${group[i].protoId}:${group[i].cardId}`, { x: pt.x, y: pt.y, side });
      }
    }

    // === Bezier construction =========================================
    const edges: MapEdge[] = [];
    for (const p of protos) {
      const a = attach.get(`${p.id}:${p.from.id}`);
      const b = attach.get(`${p.id}:${p.to.id}`);
      if (!a || !b) continue;
      const route = this.bezierFromAnchors(a, b);
      // Label-vs-card collision: if the natural midpoint falls on any
      // OTHER card (not the two endpoints), slide along the curve until
      // it's clear.
      let midX = route.midX;
      let midY = route.midY;
      const exclude = new Set([p.from.id, p.to.id]);
      if (this.pointInAnyCard(midX, midY, cards, exclude)) {
        for (const t of [0.4, 0.6, 0.3, 0.7, 0.25, 0.75, 0.2, 0.8]) {
          const pt = this.bezierAt(route, t);
          if (!this.pointInAnyCard(pt.x, pt.y, cards, exclude)) {
            midX = pt.x; midY = pt.y; break;
          }
        }
      }

      const card = p.fk.cardinality;
      const fromGlyph = (card === 'MANY_TO_ONE' || card === 'MANY_TO_MANY') ? '>' : '|';
      const toGlyph   = (card === 'ONE_TO_MANY' || card === 'MANY_TO_MANY') ? '<' : '|';
      const join = p.fk.childCol === p.fk.parentCol
        ? p.fk.childCol
        : `${p.fk.childCol} → ${p.fk.parentCol}`;

      // Confidence-driven stroke styling: high (>=0.85) is solid /
      // full-opacity, mid (>=0.65) is solid / slightly faded, low is
      // dashed + heavily faded.  null confidence renders as full-strength
      // (legacy edges from the discovery pipeline that didn't emit a
      // score should still look canonical).
      const conf = p.fk.confidence;
      const dashArray = (conf != null && conf < 0.65) ? '6,4' : '';
      const strokeOpacity = conf == null
        ? 1.0
        : conf >= 0.85 ? 1.0
        : conf >= 0.65 ? 0.85
        :                0.55;
      const confLabel = conf == null ? '' : conf.toFixed(2);

      // Trail edges override colour/opacity/dash so the drill-down
      // path stands out as a single connected gold thread regardless
      // of underlying FK confidence.
      const isTrail = !!p.isTrail;
      const trailLabel = isTrail ? `step ${p.trailStep}` : '';
      const edgeColor = isTrail ? '#f0b429' : this.relTypeColor(p.relType);
      const edgeDash = isTrail ? '' : dashArray;
      const edgeOpacity = isTrail ? 1.0 : strokeOpacity;
      edges.push({
        id: p.id,
        color: edgeColor,
        joinLabel: join,
        path: route.path,
        midX, midY,
        fromGlyph,
        toGlyph,
        fromGlyphX: route.fromX + (a.side === 'right' ? 4 : a.side === 'left' ? -10 : -3),
        fromGlyphY: route.fromY + (a.side === 'top' ? -4 : a.side === 'bottom' ? 12 : 4),
        toGlyphX:   route.toX   + (b.side === 'right' ? 4 : b.side === 'left' ? -10 : -3),
        toGlyphY:   route.toY   + (b.side === 'top' ? -4 : b.side === 'bottom' ? 12 : 4),
        fromTable: p.from.id,
        toTable:   p.to.id,
        confidence: conf,
        confLabel,
        dashArray: edgeDash,
        strokeOpacity: edgeOpacity,
        isTrail,
        trailLabel,
      });
    }
    return edges;
  });

  /** Pick which side of ``card`` faces the point at (cx, cy).  Used for
   * anchor distribution — the chosen side is whichever brings the
   * connection point closest to the OTHER card. */
  private sideFacing(
    card: { x: number; y: number; width: number; height: number },
    cx: number, cy: number,
  ): 'top' | 'right' | 'bottom' | 'left' {
    const ccx = card.x + card.width / 2;
    const ccy = card.y + card.height / 2;
    const dx = cx - ccx;
    const dy = cy - ccy;
    if (Math.abs(dx) > Math.abs(dy)) {
      return dx >= 0 ? 'right' : 'left';
    } else {
      return dy >= 0 ? 'bottom' : 'top';
    }
  }

  /** Point on ``card``'s ``side`` border at fraction ``t`` (0..1) along
   * the side.  ``t`` = 0.5 is centre-of-side; smaller spreads pull
   * attachments toward the corners. */
  private pointOnSide(
    card: { x: number; y: number; width: number; height: number },
    side: 'top' | 'right' | 'bottom' | 'left',
    t: number,
  ): { x: number; y: number } {
    // Clamp t into [0.15, 0.85] so anchors don't sit right on the corners.
    const ct = Math.max(0.15, Math.min(0.85, t));
    switch (side) {
      case 'top':    return { x: card.x + card.width * ct, y: card.y };
      case 'bottom': return { x: card.x + card.width * ct, y: card.y + card.height };
      case 'left':   return { x: card.x, y: card.y + card.height * ct };
      case 'right':  return { x: card.x + card.width, y: card.y + card.height * ct };
    }
  }

  /** Build a cubic bezier between two anchor points whose ``side`` tells
   * us which way the curve should leave the card.  Returns the path
   * string + the midpoint at t=0.5 + the endpoint coordinates so the
   * caller can place cardinality glyphs. */
  private bezierFromAnchors(
    a: { x: number; y: number; side: 'top' | 'right' | 'bottom' | 'left' },
    b: { x: number; y: number; side: 'top' | 'right' | 'bottom' | 'left' },
  ): { path: string; midX: number; midY: number; fromX: number; fromY: number; toX: number; toY: number } {
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    const handle = Math.max(60, dist * 0.35);
    const off = (side: 'top' | 'right' | 'bottom' | 'left') => {
      switch (side) {
        case 'top':    return { dx: 0, dy: -handle };
        case 'bottom': return { dx: 0, dy: +handle };
        case 'left':   return { dx: -handle, dy: 0 };
        case 'right':  return { dx: +handle, dy: 0 };
      }
    };
    const oa = off(a.side);
    const ob = off(b.side);
    const c0x = a.x + oa.dx, c0y = a.y + oa.dy;
    const c1x = b.x + ob.dx, c1y = b.y + ob.dy;

    const mid = this.bezierAt(
      { fromX: a.x, fromY: a.y, c0x, c0y, c1x, c1y, toX: b.x, toY: b.y } as any,
      0.5,
    );
    return {
      path: `M ${a.x} ${a.y} C ${c0x} ${c0y}, ${c1x} ${c1y}, ${b.x} ${b.y}`,
      midX: mid.x, midY: mid.y,
      fromX: a.x, fromY: a.y, toX: b.x, toY: b.y,
    };
  }

  /** Evaluate a cubic bezier at parameter ``t``.  ``route`` may carry
   * either the bezierFromAnchors output OR the legacy bezierBetween
   * output — both have the same control-point fields. */
  private bezierAt(
    route: { fromX?: number; fromY?: number; toX?: number; toY?: number;
             c0x?: number; c0y?: number; c1x?: number; c1y?: number;
             path?: string; },
    t: number,
  ): { x: number; y: number } {
    // Parse from path when explicit fields aren't there (defensive).
    let p0x = route.fromX ?? 0, p0y = route.fromY ?? 0;
    let p1x = route.toX ?? 0,   p1y = route.toY ?? 0;
    let c0x = route.c0x ?? p0x, c0y = route.c0y ?? p0y;
    let c1x = route.c1x ?? p1x, c1y = route.c1y ?? p1y;
    if ((route.c0x === undefined || route.c1x === undefined) && route.path) {
      const m = /M ([\d.+-]+) ([\d.+-]+) C ([\d.+-]+) ([\d.+-]+), ([\d.+-]+) ([\d.+-]+), ([\d.+-]+) ([\d.+-]+)/.exec(route.path);
      if (m) {
        p0x = +m[1]; p0y = +m[2];
        c0x = +m[3]; c0y = +m[4];
        c1x = +m[5]; c1y = +m[6];
        p1x = +m[7]; p1y = +m[8];
      }
    }
    const omt = 1 - t;
    return {
      x: omt ** 3 * p0x + 3 * omt ** 2 * t * c0x + 3 * omt * t ** 2 * c1x + t ** 3 * p1x,
      y: omt ** 3 * p0y + 3 * omt ** 2 * t * c0y + 3 * omt * t ** 2 * c1y + t ** 3 * p1y,
    };
  }

  /** Returns true if (x, y) lies inside the AABB of any card except the
   * ids in ``exclude``.  Used to slide labels off cards. */
  private pointInAnyCard(
    x: number, y: number,
    cards: { id: string; x: number; y: number; width: number; height: number }[],
    exclude: Set<string>,
  ): boolean {
    const PAD = 6;
    for (const c of cards) {
      if (exclude.has(c.id)) continue;
      if (x >= c.x - PAD && x <= c.x + c.width + PAD &&
          y >= c.y - PAD && y <= c.y + c.height + PAD) {
        return true;
      }
    }
    return false;
  }

  mapContentSize = computed(() => {
    let maxX = 0, maxY = 0;
    for (const c of this.mapCards()) {
      maxX = Math.max(maxX, c.x + c.width);
      maxY = Math.max(maxY, c.y + c.height);
    }
    return { w: maxX + 80, h: maxY + 80 };
  });

  mapModuleBadge = computed(() => this.moduleBadge(this.tableName()));

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

  /** Mousedown on a map-card.  Records the gesture origin; doesn't yet
   * promote the card to focal — that decision is deferred to mouseup so
   * we can distinguish click (no movement) from drag (>= 5 px). */
  onCardMouseDown(ev: MouseEvent, n: { id: string }): void {
    // Stop the wrap's pan handler from also firing on this event.
    ev.stopPropagation();
    // Suppress text selection drag-start while dragging the card.
    ev.preventDefault();
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

  onMapMouseMove(ev: MouseEvent): void {
    // Card drag takes precedence over canvas pan.
    if (this.cardDrag) {
      const dxScreen = ev.clientX - this.cardDrag.startCursorX;
      const dyScreen = ev.clientY - this.cardDrag.startCursorY;
      if (!this.cardDrag.hasMoved) {
        if (Math.hypot(dxScreen, dyScreen) < TableCardPageComponent.CARD_DRAG_THRESHOLD) {
          return;
        }
        this.cardDrag.hasMoved = true;
      }
      // Convert screen-space delta to canvas-space (account for zoom).
      const z = this.mapZoom() || 1;
      const dx = dxScreen / z;
      const dy = dyScreen / z;
      const next = { ...this.cardOffsets() };
      next[this.cardDrag.cardId] = {
        dx: this.cardDrag.startOffsetX + dx,
        dy: this.cardDrag.startOffsetY + dy,
      };
      this.cardOffsets.set(next);
      return;
    }
    if (!this.mapDragging) return;
    this.mapPanX.set(this.mapDragOriginX + (ev.clientX - this.mapDragStartX));
    this.mapPanY.set(this.mapDragOriginY + (ev.clientY - this.mapDragStartY));
  }

  onMapMouseUp(_ev: MouseEvent): void {
    if (this.cardDrag) {
      // Click vs drag: if the gesture stayed inside the threshold, treat
      // as a click → promote the card to focal (or no-op if it IS the
      // focal).  Otherwise the drag commits the new position; nothing
      // further to do — cardOffsets is already up to date.
      if (!this.cardDrag.hasMoved) {
        this.onMapCardClick({ id: this.cardDrag.cardId });
      }
      this.cardDrag = null;
      return;
    }
    this.mapDragging = false;
  }

  onMapCardClick(n: { id: string }): void {
    if (n.id === this.tableName()) return;
    if (this.embedded) {
      // Parent owns navigation in embedded mode — emit and let the
      // Relationships tab update its ?table= query param.
      this.tableSelected.emit({ table: n.id, view: 'map' });
      return;
    }
    // Standalone-route mode: promote neighbour to focal via URL change.
    this.router.navigate(['/jobs', this.jobId, 'tables', n.id], {
      queryParams: { view: 'map' },
    });
  }

  onSearchInput(ev: Event): void {
    this.searchQuery.set((ev.target as HTMLInputElement).value);
  }

  /** Range-input handler for the map's min-confidence slider. */
  onMapConfidenceChange(ev: Event): void {
    const v = parseFloat((ev.target as HTMLInputElement).value);
    this.minMapConfidence.set(Number.isNaN(v) ? 0 : v);
  }

  /** Pick a hue for the conf number based on its bucket — green for
   * strong, amber for medium, dim grey for weak.  Matches the score
   * colour vocabulary used in the table-view sidebar. */
  confLabelColor(c: number | null): string {
    if (c == null) return '#8b949e';
    if (c >= 0.85) return '#3fb950';
    if (c >= 0.65) return '#d29922';
    return '#8b949e';
  }

  /** Click handler for the jump-to-table search dropdown.  Navigates
   * (route-driven) or emits (embedded) — same branching as
   * onMapCardClick. */
  onSearchHitClick(table: string): void {
    this.searchQuery.set('');
    if (this.embedded) {
      this.tableSelected.emit({ table, view: 'map' });
      return;
    }
    this.router.navigate(['/jobs', this.jobId, 'tables', table], {
      queryParams: { view: 'map' },
    });
  }

  exportMap(): void {
    // Copy a Mermaid snippet covering the focal + neighbours to clipboard.
    const lines: string[] = [`%% Archon-SuperNova focal map: ${this.tableName()}`, 'erDiagram'];
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
    if (cardId === this.tableName()) return true;
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
    // Two configuration paths: route-driven (top-level page) or input-
    // driven (embedded inside the Relationships tab).  Inputs take
    // precedence when present so the parent owns the source of truth.
    const id = this.inputJobId
      ?? this.route.snapshot.paramMap.get('id');
    const tbl = this.inputTableName
      ?? this.route.snapshot.paramMap.get('table_name');
    if (!id || !tbl) {
      this.error.set('Missing job id or table name in URL.');
      this.loading.set(false);
      return;
    }
    this.jobId = id;
    this.tableName.set(tbl);
    const v0 = this.inputView
      ?? this.route.snapshot.queryParamMap.get('view');
    this.view.set(v0 === 'map' ? 'map' : 'table');

    if (!this.embedded) {
      // Route-driven mode: subscribe to URL param changes so browser
      // back/forward + sibling-route navigation update focal state.
      this.route.queryParamMap.subscribe(qp => {
        const v = qp.get('view');
        const next = v === 'map' ? 'map' : 'table';
        if (next !== this.view()) this.view.set(next);
      });
      this.route.paramMap.subscribe(pm => {
        const newTbl = pm.get('table_name');
        if (!newTbl || newTbl === this.tableName()) return;
        this.onFocalChange(newTbl);
      });
    }

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
        // Build the per-table row-count lookup from the relationships
        // nodes payload.  Each node carries ``row_count`` (lifted from
        // ``tbl_inventory.row_count_estimate`` by the API).  Falls
        // back to the legacy ``value`` (edge degree) only on older
        // API responses that don't yet ship the field.
        const rc = new Map<string, number>();
        for (const n of (r.rels.nodes ?? [])) {
          rc.set(n.id, (n as any).row_count ?? n.value ?? 0);
        }
        this.rowCountByTable.set(rc);
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
    // Brand chips (IIN/BIN) per column — only populated when the
    // column has a CC_NUMBER finding with a non-empty
    // ``provider_breakdown``.  Sorted server-side by descending count.
    const brandsByCol = new Map<string, string[]>();
    // Regulation tags per column — deduplicated union of every
    // finding's ``regulated`` array.  PCI shows once even when both
    // CC_NUMBER and CARD_CVV fire on the same column.
    const regsByCol = new Map<string, string[]>();
    for (const f of this.allPii()) {
      if (f.table_name === this.tableName()) {
        const arr = piiByCol.get(f.column_name) ?? [];
        if (!arr.includes(f.pii_type)) arr.push(f.pii_type);
        piiByCol.set(f.column_name, arr);
        if (f.pii_type === 'CC_NUMBER' && f.provider_breakdown && f.provider_breakdown.length > 0) {
          brandsByCol.set(f.column_name, f.provider_breakdown.map(p => p.brand));
        }
        if (Array.isArray(f.regulated) && f.regulated.length > 0) {
          const cur = regsByCol.get(f.column_name) ?? [];
          for (const r of f.regulated) if (!cur.includes(r)) cur.push(r);
          regsByCol.set(f.column_name, cur);
        }
      }
    }
    return this.allColumns()
      .filter(c => c.table === this.tableName())
      .sort((a, b) => a.ordinal - b.ordinal)
      .map(c => ({
        ordinal: c.ordinal,
        name: c.column,
        type: c.data_type,
        length: this.lengthFromType(c.data_type),
        is_pk: c.is_pk,
        is_fk: c.is_fk,
        pii_types: piiByCol.get(c.column) ?? [],
        brands: brandsByCol.get(c.column) ?? [],
        regulations: regsByCol.get(c.column) ?? [],
      }));
  });

  /** Friendly display label for a PII type symbol — keeps tables
   * scannable without dropping the canonical name (the raw symbol
   * stays in the chip's title attribute for power users).  Mirrors
   * the mapping in pii-table.component.ts so the two surfaces show
   * the same labels. */
  private static readonly _PII_LABEL_MAP: Record<string, string> = {
    CC_NUMBER: 'Card Number',
    CARD_HOLDER_NAME: 'Card Name',
    CARD_CVV: 'CVV',
    CREDENTIAL_HASH: 'Credential Hash',
    SSN_US: 'SSN (US)',
    PHONE_US: 'Phone (US)',
    PASSPORT_US: 'Passport (US)',
    PASSPORT_GB: 'Passport (UK)',
    PASSPORT_IN: 'Passport (IN)',
    AADHAAR_IN: 'Aadhaar',
    PAN_IN: 'PAN (IN)',
    NHS_GB: 'NHS Number',
    NIR_FR: 'NIR',
    PERSON_NAME: 'Person Name',
    EMAIL: 'Email',
    POSTAL_CODE: 'Postal Code',
    COUNTRY_CODE: 'Country Code',
    ADDRESS: 'Address',
    DOB: 'Date of Birth',
    IBAN: 'IBAN',
    SWIFT_BIC: 'SWIFT / BIC',
    BANK_ACCOUNT: 'Bank Account',
    ABA_ROUTING_US: 'ABA Routing',
  };

  piiLabel(piiType: string): string {
    return TableCardPageComponent._PII_LABEL_MAP[piiType] ?? piiType;
  }

  /** Tooltip for the collapsed [PCI] chip on a column row.  Lists the
   * underlying PII types (Card Number / Card Name / CVV) and the
   * IIN/BIN brand breakdown so the user can see the detail without
   * the row carrying five separate chips. */
  pciColumnTooltip(c: ColumnRow): string {
    const types = c.pii_types
      .map(t => TableCardPageComponent._PII_LABEL_MAP[t] ?? t);
    const lines: string[] = [`PCI: ${types.join(', ')}`];
    if (c.brands.length > 0) {
      lines.push(c.brands.join(', '));
    }
    return lines.join('\n');
  }

  outFks = computed<FkRow[]>(() =>
    this.allEdges()
      .filter(e => e.from === this.tableName())
      .map(e => this.parseEdge(e))
  );

  inFks = computed<FkRow[]>(() =>
    this.allEdges()
      .filter(e => e.to === this.tableName())
      .map(e => this.parseEdge(e))
  );

  /** Map-view-only FK projections, post min-confidence filter.  The
   * sidebar/table view always shows the full set; the map filters so
   * the canvas stays uncluttered when the user dials up the threshold.
   * FKs without a confidence score are kept (treated as "trusted"). */
  visibleOutFks = computed<FkRow[]>(() => {
    const min = this.minMapConfidence();
    return this.outFks().filter(f => f.confidence == null || f.confidence >= min);
  });
  visibleInFks = computed<FkRow[]>(() => {
    const min = this.minMapConfidence();
    return this.inFks().filter(f => f.confidence == null || f.confidence >= min);
  });
  /** Unfiltered map-edge count, used by the chip's "X/N connections"
   * readout when the slider hides some of them. */
  totalMapEdgeCount = computed(() => this.outFks().length + this.inFks().length);

  piiRows = computed<PiiFinding[]>(() =>
    this.allPii()
      .filter(p => p.table_name === this.tableName())
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
