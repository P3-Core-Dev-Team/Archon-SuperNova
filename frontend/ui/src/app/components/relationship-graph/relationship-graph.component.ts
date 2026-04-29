import {
  AfterViewInit, Component, ElementRef, OnChanges, OnDestroy,
  ViewChild, inject, input, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { DataSet } from 'vis-data/peer';
import { Edge as VisEdge, Network, Node as VisNode, Options } from 'vis-network/peer';
import { JobService } from '../../services/job.service';
import { RelationshipEdge } from '../../models/job.model';
import { clusterColor } from '../cluster-graph/cluster-graph.component';

type RelType = 'header_item' | 'master_lookup' | 'config' | 'text' | 'history';

@Component({
  selector: 'app-relationship-graph',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="toolbar">
      <span class="muted">Schema: <strong>{{ schema() }}</strong></span>
      <span class="muted">Tables: <strong>{{ totalTables() }}</strong></span>
      <span class="counter" [title]="'Showing ' + visibleEdgeCount() + ' of ' + totalEdges() + ' relationships at confidence ≥ ' + (minConfidence() | number:'1.2-2')">
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
      <span class="tier-pills" title="Live counts per confidence band — updates as the slider moves">
        <span class="pill tier-high">≥0.95: {{ tierCounts().high }}</span>
        <span class="pill tier-med">0.85–0.95: {{ tierCounts().med }}</span>
        <span class="pill tier-low">&lt;0.85: {{ tierCounts().low }}</span>
        @if (tierCounts().unknown > 0) {
          <span class="pill tier-unknown">unknown: {{ tierCounts().unknown }}</span>
        }
      </span>
      <button type="button"
              (click)="toggleHierarchical()"
              [disabled]="!network"
              [class.active]="hierarchical()"
              [attr.aria-pressed]="hierarchical()"
              title="Switch between force-directed and hierarchical (top-down) layout">
        Hierarchical layout: <strong>{{ hierarchical() ? 'on' : 'off' }}</strong>
      </button>
      <button type="button" (click)="fitToScreen()" [disabled]="!network">Fit to screen</button>
    </div>

    <div class="graph-wrap">
      <div #container class="graph"></div>
      <div class="legend">
        <div class="legend-title">Relationship</div>
        <div class="legend-row"><span class="swatch" style="background:#58a6ff"></span> header / item</div>
        <div class="legend-row"><span class="swatch" style="background:#3fb950"></span> master lookup</div>
        <div class="legend-row"><span class="swatch" style="background:#bc8cff"></span> config</div>
        <div class="legend-row"><span class="swatch" style="background:#d29922"></span> text</div>
        <div class="legend-row"><span class="swatch" style="background:#8b949e"></span> history</div>
        <div class="legend-divider"></div>
        <div class="legend-title">Cardinality</div>
        <div class="legend-row"><span class="card-glyph">|—|</span> 1:1</div>
        <div class="legend-row"><span class="card-glyph">|—&lt;</span> 1:N</div>
        <div class="legend-row"><span class="card-glyph">&gt;—|</span> N:1</div>
        <div class="legend-row"><span class="card-glyph">&gt;—&lt;</span> N:M</div>
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
    /* Prominent live count of currently-visible relationships. */
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
    /* Per-tier live counters next to the slider. */
    .tier-pills {
      display: inline-flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      border: 1px solid;
      background: #0d1117;
    }
    .pill.tier-high    { color: #3fb950; border-color: rgba(63, 185, 80, 0.5); }
    .pill.tier-med     { color: #d29922; border-color: rgba(210, 153, 34, 0.5); }
    .pill.tier-low     { color: #8b949e; border-color: rgba(139, 148, 158, 0.5); }
    .pill.tier-unknown { color: #999;    border-color: #444; }
    .graph-wrap { position: relative; }
    .graph {
      width: 100%;
      height: 620px;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 8px;
      position: relative;
    }
    .legend {
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: rgba(13, 17, 23, 0.85);
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
    .card-glyph {
      display: inline-block;
      min-width: 30px;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 11px;
      color: #c9d1d9;
      text-align: center;
      letter-spacing: -1px;
    }
    .toolbar button.active {
      background: #1f6feb;
      border-color: #58a6ff;
      color: #fff;
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

  @ViewChild('container') container!: ElementRef<HTMLDivElement>;

  private jobsSvc = inject(JobService);

  schema = signal('');
  totalTables = signal(0);
  totalEdges = signal(0);
  visibleEdgeCount = signal(0);
  /** Live per-tier counts (after the confidence slider filter is applied). */
  tierCounts = signal<{ high: number; med: number; low: number; unknown: number }>(
    { high: 0, med: 0, low: 0, unknown: 0 }
  );
  loading = signal(true);
  error = signal<string | null>(null);
  selectedEdge = signal<RelationshipEdge | null>(null);
  minConfidence = signal(0);
  hierarchical = signal(false);

  network: Network | null = null;
  private allEdges: RelationshipEdge[] = [];
  private nodesData = new DataSet<VisNode>();
  private edgesData = new DataSet<VisEdge>();
  // Stable edge id → original RelationshipEdge so selection survives filter changes.
  private edgeById = new Map<string, RelationshipEdge>();

  ngAfterViewInit(): void {
    this.load();
  }

  ngOnChanges(): void {
    // jobId can theoretically change; reload
    if (this.container) this.load();
  }

  ngOnDestroy(): void {
    this.network?.destroy();
  }

  onConfidenceChange(ev: Event): void {
    const v = +(ev.target as HTMLInputElement).value;
    this.minConfidence.set(v);
    this.applyFilter();
  }

  fitToScreen(): void {
    this.network?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
  }

  toggleHierarchical(): void {
    if (!this.network) return;
    const next = !this.hierarchical();
    this.hierarchical.set(next);
    if (next) {
      // Hierarchical mode owns its own positioning — physics off avoids
      // drift after the layout solver places nodes on levels.
      this.network.setOptions({
        layout: {
          hierarchical: {
            enabled: true,
            direction: 'UD',
            sortMethod: 'directed',
            nodeSpacing: 200,
            levelSeparation: 200,
            edgeMinimization: true,
          },
        },
        physics: { enabled: false },
      });
      // Hierarchical layout is synchronous; fit on the next tick.
      setTimeout(() => this.network?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }), 0);
    } else {
      // Switch back to force-directed: re-enable physics and re-arm the
      // freeze-on-stabilize listener so panning won't restart the simulation
      // after the new layout settles.
      this.network.setOptions({
        layout: { hierarchical: { enabled: false } },
        physics: {
          enabled: true,
          barnesHut: {
            gravitationalConstant: -12000,
            centralGravity: 0.2,
            springLength: 110,
            springConstant: 0.04,
            damping: 0.4,
          },
          stabilization: { iterations: 250 },
        },
      });
      this.armFreezeOnStabilize();
      this.network.once('stabilized', () => this.network?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }));
    }
  }

  /**
   * Re-arm the "freeze physics once stabilised" behaviour. Called from
   * initNetwork() and again whenever we switch back to force-directed so the
   * simulation doesn't keep running indefinitely after layout toggles.
   */
  private armFreezeOnStabilize(): void {
    this.network?.once('stabilizationIterationsDone', () => {
      // Only freeze if we're still in force-directed mode — user may have
      // toggled back to hierarchical before stabilisation finished.
      if (!this.hierarchical()) {
        this.network?.setOptions({ physics: { enabled: false } });
      }
    });
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.jobsSvc.relationships(this.jobId(), 1000).subscribe({
      next: g => {
        this.schema.set(g.schema);
        this.totalTables.set(g.total_tables);
        this.totalEdges.set(g.total_edges);
        this.allEdges = g.edges;

        // Fetch cluster assignments so each table NODE gets coloured by
        // the cluster it belongs to.  When the clusters table doesn't
        // exist yet the call yields an empty map and we fall back to a
        // single accent color.
        this.jobsSvc.clusters(this.jobId()).subscribe({
          next: cl => {
            const tableToCluster = new Map<string, number>();
            for (const c of cl.clusters) {
              for (const tname of (cl as any).clusters && (c as any).table_names || []) {
                tableToCluster.set(tname, c.cluster_id);
              }
            }
            // /clusters returns table_count per cluster but not table names.
            // Resolve via cluster-detail in parallel for the larger ones,
            // skipping clusters with table_count <= 1 (singletons).
            const real = cl.clusters.filter(c => c.table_count > 1);
            if (real.length === 0) {
              this.paintNodes(g.nodes, tableToCluster);
              return;
            }
            let pending = real.length;
            for (const c of real) {
              this.jobsSvc.clusterDetail(this.jobId(), c.cluster_id).subscribe({
                next: cd => {
                  for (const t of cd.tables) {
                    tableToCluster.set(t.table_name, c.cluster_id);
                  }
                },
                error: () => { /* continue with partial */ },
                complete: () => {
                  if (--pending === 0) {
                    this.paintNodes(g.nodes, tableToCluster);
                  }
                },
              });
            }
          },
          error: () => this.paintNodes(g.nodes, new Map<string, number>()),
        });
      },
      error: err => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load relationship graph.',
        );
      },
    });
  }

  /**
   * Paint nodes coloured by their cluster_id.  Tables that did not get
   * assigned to a cluster (singletons or pre-clustering) fall back to the
   * legacy accent blue.  Lightens the color slightly for the border so the
   * node has a subtle highlight.
   */
  private paintNodes(
    nodes: { id: string; label: string; value: number }[],
    tableToCluster: Map<string, number>,
  ): void {
    // Pre-compute degree (FK in/out count) per table from the loaded edges
    // so the card footer can show "{N} fields" — actual column counts aren't
    // in the relationships payload, so we use the degree as a proxy of
    // structural significance.
    const degree = new Map<string, number>();
    for (const e of this.allEdges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }

    this.nodesData.clear();
    this.nodesData.add(nodes.map(n => {
      const cid = tableToCluster.get(n.id);
      const accent = cid != null ? clusterColor(cid) : '#58a6ff';
      const deg = degree.get(n.id) ?? 0;
      const moduleHint = this.moduleBadge(n.id);
      // vis-network's font.multi='html' supports <b>/<i>/<code>; build a
      // 3-line "card" label: bold mono table name + module badge, separator,
      // small muted footer with edge-degree.
      const label =
        `<b>${this.escape(n.label)}</b>${moduleHint ? `  <code>${moduleHint}</code>` : ''}\n` +
        `${n.value.toLocaleString()} row${n.value === 1 ? '' : 's'}\n` +
        `${deg} relationship${deg === 1 ? '' : 's'}`;
      return {
        id: n.id,
        label,
        value: n.value,
        title: cid != null
          ? `${n.label}\n${n.value} row${n.value === 1 ? '' : 's'}\ncluster #${cid}`
          : `${n.label}\n${n.value} row${n.value === 1 ? '' : 's'}`,
        // shape:'box' = rounded-rect background; chromeOnly is set false so
        // the border + fill render even when the node is unselected.
        shape: 'box',
        // shapeProperties.borderRadius gives the rounded corners.
        shapeProperties: { borderRadius: 8 },
        color: {
          background: '#161b22',
          border: '#30363d',
          highlight: { background: '#1c222b', border: accent },
          hover: { background: '#1c222b', border: accent },
        },
        borderWidth: 1,
        borderWidthSelected: 2,
        margin: { top: 12, right: 14, bottom: 12, left: 14 } as any,
        font: {
          multi: 'html',
          face: 'ui-monospace, SFMono-Regular, monospace',
          color: '#e6edf3',
          size: 12,
          align: 'left',
          // Let the multi-line label flow; vis-network respects \n breaks.
          bold: { color: '#e6edf3', size: 14, face: 'ui-monospace, SFMono-Regular, monospace' } as any,
        } as any,
        widthConstraint: { minimum: 140, maximum: 220 },
      };
    }));
    this.applyFilter();
    if (!this.network) {
      this.initNetwork();
    }
    this.loading.set(false);
  }

  /** Best-effort module classifier from table-name prefix.  Not perfect —
   * users can override later via a config map.  Lower-case table name is
   * scanned for the most-specific known prefix first.  Returns ``null``
   * when no recognised module shows up so the badge is hidden. */
  private moduleBadge(tableName: string): string | null {
    const t = tableName.toLowerCase();
    const map: [RegExp, string][] = [
      [/^ads_app/,         'APP'],
      [/^ads_st_/,         'STG'],
      [/^ads_user/,        'USR'],
      [/^ads_open_metadata/, 'META'],
      [/^ads_ingestion/,   'INGEST'],
      [/^ads_master_job|^ads_job/, 'JOB'],
      [/_audit$|_log$|_history$|_event/, 'AUDIT'],
      [/^ads_/,            'ADS'],
      [/_lookup$|^lkp_|^ref_/, 'REF'],
      [/^v_|^vw_/,         'VIEW'],
    ];
    for (const [re, badge] of map) {
      if (re.test(t)) return badge;
    }
    return null;
  }

  /** Minimal HTML-escape so a stray ``<`` in a table name doesn't break the
   * font.multi='html' rendering.  Three angle-brackets are enough. */
  private escape(s: string): string {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  private applyFilter(): void {
    const min = this.minConfidence();
    this.edgesData.clear();
    this.edgeById.clear();
    let visibleCount = 0;
    let high = 0, med = 0, low = 0, unk = 0;
    const visEdges: VisEdge[] = [];
    this.allEdges.forEach((e, i) => {
      if (e.confidence != null && e.confidence < min) return;
      const id = `e${i}`;
      this.edgeById.set(id, e);
      visibleCount++;
      if (e.confidence == null) unk++;
      else if (e.confidence >= 0.95) high++;
      else if (e.confidence >= 0.85) med++;
      else low++;
      const arrows = this.arrowsFor(e.cardinality);
      const cardLabel = this.labelFor(e.cardinality);
      const relType = this.classifyRelType(e);
      const relColor = this.relTypeColor(relType);
      const tooltipCard = e.cardinality ? e.cardinality : 'unknown';
      // Edge label = the joining column name(s), in lowercase mono with a
      // dark pill background.  ``e.label`` from the API is "child_col → parent_col";
      // we use the child_col side as the join key.
      const joinCol = this.joinColumn(e.label);
      const edge: VisEdge = {
        id,
        from: e.from,
        to: e.to,
        title: `${e.label}\ntype ${relType}\nconfidence ${this.fmt(e.confidence)} • containment ${this.fmt(e.containment)}\ncardinality ${tooltipCard}`,
        color: { color: relColor, highlight: '#58a6ff', hover: relColor },
        arrows,
        // Smooth bezier — the spec calls for "smooth cubic bezier curves
        // connecting card borders, not straight lines".  vis-network's
        // 'cubicBezier' produces the queryviz-style sweep.
        smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.5 } as any,
        width: e.confidence == null ? 1.2 : Math.max(1.2, e.confidence * 2.5),
      };
      // Combined label: join column + cardinality glyph — both render in a
      // pill thanks to the font.background.  Avoids a double-label that
      // would compete with the bezier path.
      const labelText = joinCol
        ? (cardLabel ? `${joinCol}  ${cardLabel}` : joinCol)
        : (cardLabel ?? '');
      if (labelText) {
        edge.label = labelText;
        edge.font = {
          size: 11,
          color: '#c9d1d9',
          face: 'ui-monospace, SFMono-Regular, monospace',
          background: '#0d1117',
          strokeWidth: 0,
          align: 'middle',
        } as VisEdge['font'];
      }
      visEdges.push(edge);
    });
    this.edgesData.add(visEdges);
    this.visibleEdgeCount.set(visibleCount);
    this.tierCounts.set({ high, med, low, unknown: unk });
  }

  /** Classify a relationship into one of the 5 visual buckets per the spec.
   * Heuristic — tuned for SAP/AdventureWorks-style schemas where the column
   * names are conventionally meaningful.  Falls back to ``header_item`` for
   * any "regular FK" that doesn't fit a more specific bucket.
   *
   * Buckets:
   *   header_item    — child has ``_id`` ending → blue (most common shape)
   *   master_lookup  — parent table is a tiny dictionary (status / category /
   *                    country / language) → green
   *   config         — parent table contains "config" / "policy" / "setting" → purple
   *   text           — column involves "_text" / "_desc" / "_note" → amber
   *   history        — child or parent contains "history" / "audit" / "_log" / "event" → gray
   */
  private classifyRelType(e: RelationshipEdge): RelType {
    const join = (e.label || '').toLowerCase();
    const fromT = e.from.toLowerCase();
    const toT = e.to.toLowerCase();
    if (/_audit|_log|_history|_event|change_log/.test(fromT) ||
        /_audit|_log|_history|_event|change_log/.test(toT)) {
      return 'history';
    }
    if (/config|setting|policy|rule|param/.test(toT)) {
      return 'config';
    }
    if (/_text|_desc|_note|_message|_comment|_summary|_body/.test(join)) {
      return 'text';
    }
    if (/status|category|country|language|currency|type|kind|locale|state|region|department|priority|level|code$/.test(toT)) {
      return 'master_lookup';
    }
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

  /** Extract the "child_col" half of the API-provided edge label
   * ``child_col → parent_col`` so we render only one column-name on the
   * edge.  Non-matching shapes return null → the edge keeps its
   * cardinality glyph alone. */
  private joinColumn(rawLabel: string | null | undefined): string | null {
    if (!rawLabel) return null;
    const arrow = ' → ';
    if (rawLabel.includes(arrow)) {
      const [child, parent] = rawLabel.split(arrow);
      const c = (child ?? '').trim();
      const p = (parent ?? '').trim();
      if (c && p && c === p) return c;
      // Different column names on each side: show "child→parent"
      return `${c}→${p}`;
    }
    return rawLabel;
  }

  /**
   * Convert a backend cardinality string into vis-network endpoint config using
   * crow's-foot (Information Engineering) conventions. Endpoint shapes:
   *   ONE_TO_ONE   → bar at both ends
   *   ONE_TO_MANY  → bar at parent (from), crow at child (to)
   *   MANY_TO_ONE  → crow at parent (from), bar at child (to)  (mirror of 1:N)
   *   MANY_TO_MANY → crow at both ends
   *   null/empty/other → keep the existing single-arrow style
   * vis-network 9.1.13 supports a native "crow" endpoint, so no fallback is needed.
   */
  private arrowsFor(cardinality: string | null | undefined): VisEdge['arrows'] {
    switch (cardinality) {
      case 'ONE_TO_ONE':
        return {
          to: { enabled: true, type: 'bar', scaleFactor: 0.9 },
          from: { enabled: true, type: 'bar', scaleFactor: 0.9 },
        };
      case 'ONE_TO_MANY':
        return {
          to: { enabled: true, type: 'crow', scaleFactor: 1.1 },
          from: { enabled: true, type: 'bar', scaleFactor: 0.9 },
        };
      case 'MANY_TO_ONE':
        return {
          to: { enabled: true, type: 'bar', scaleFactor: 0.9 },
          from: { enabled: true, type: 'crow', scaleFactor: 1.1 },
        };
      case 'MANY_TO_MANY':
        return {
          to: { enabled: true, type: 'crow', scaleFactor: 1.1 },
          from: { enabled: true, type: 'crow', scaleFactor: 1.1 },
        };
      default:
        return { to: { enabled: true, type: 'arrow' } };
    }
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

  private fmt(v: number | null): string {
    return v == null ? '—' : v.toFixed(3);
  }


  private initNetwork(): void {
    const options: Options = {
      autoResize: true,
      physics: {
        enabled: true,
        barnesHut: {
          gravitationalConstant: -12000,
          centralGravity: 0.2,
          springLength: 110,
          springConstant: 0.04,
          damping: 0.4,
        },
        stabilization: { iterations: 250 },
      },
      interaction: {
        hover: true,
        multiselect: false,
        tooltipDelay: 250,
      },
      nodes: {
        // shape:'box' with rounded corners is the queryviz card look.  Per-node
        // overrides in paintNodes() set background / border / multi-line label.
        shape: 'box',
        shapeProperties: { borderRadius: 8 },
        borderWidth: 1,
        borderWidthSelected: 2,
      },
      edges: {
        // Per-edge `arrows` and `color` objects override these defaults.  Spec
        // calls for cubic-bezier curves connecting card borders.
        arrows: { to: { enabled: true, type: 'arrow' } },
        smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.5 } as any,
      },
    };
    this.network = new Network(
      this.container.nativeElement,
      { nodes: this.nodesData, edges: this.edgesData },
      options,
    );
    this.network.on('selectEdge', params => {
      const id = params.edges[0];
      if (id == null) return;
      this.selectedEdge.set(this.edgeById.get(String(id)) ?? null);
    });
    this.network.on('deselectEdge', () => this.selectedEdge.set(null));
    // Selecting a node publishes the table name on the JobService bus so the
    // detail panel below the graph (table-detail.component) can render it.
    this.network.on('selectNode', params => {
      const id = params.nodes[0];
      if (id == null) return;
      this.jobsSvc.selectedTable.set(String(id));
    });
    this.network.on('deselectNode', () => this.jobsSvc.selectedTable.set(null));
    // Freeze layout once stabilised so panning/zooming doesn't restart the simulation.
    this.armFreezeOnStabilize();
  }
}
