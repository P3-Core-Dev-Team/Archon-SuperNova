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
        <div class="legend-title">Confidence</div>
        <div class="legend-row"><span class="swatch" style="background:#3fb950"></span> ≥ 0.95</div>
        <div class="legend-row"><span class="swatch" style="background:#d29922"></span> 0.85 – 0.95</div>
        <div class="legend-row"><span class="swatch" style="background:#8b949e"></span> &lt; 0.85</div>
        <div class="legend-row"><span class="swatch" style="background:#666"></span> unknown</div>
        <div class="legend-divider"></div>
        <div class="legend-title">Cardinality</div>
        <div class="legend-row"><span class="card-glyph">|—|</span> 1:1 (one-to-one)</div>
        <div class="legend-row"><span class="card-glyph">|—&lt;</span> 1:N (one-to-many)</div>
        <div class="legend-row"><span class="card-glyph">&gt;—|</span> N:1 (many-to-one)</div>
        <div class="legend-row"><span class="card-glyph">&gt;—&lt;</span> N:M (many-to-many)</div>
        <div class="legend-row"><span class="card-glyph">→</span> unknown</div>
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
    this.nodesData.clear();
    this.nodesData.add(nodes.map(n => {
      const cid = tableToCluster.get(n.id);
      const fill = cid != null ? clusterColor(cid) : '#1f6feb';
      const border = cid != null ? clusterColor(cid) : '#58a6ff';
      return {
        id: n.id,
        label: n.label,
        value: n.value,
        title: cid != null
          ? `${n.label}\n${n.value} row${n.value === 1 ? '' : 's'}\ncluster #${cid}`
          : `${n.label}\n${n.value} row${n.value === 1 ? '' : 's'}`,
        font: {
          // White labels with a thin dark stroke so the text remains
          // legible whether the cluster color is light (e.g. amber) or
          // dark (e.g. red).
          color: '#ffffff',
          face: 'ui-monospace, SFMono-Regular, monospace',
          size: 13,
          strokeWidth: 3,
          strokeColor: '#0d1117',
        },
        shape: 'dot',
        color: { background: fill, border },
      };
    }));
    this.applyFilter();
    if (!this.network) {
      this.initNetwork();
    }
    this.loading.set(false);
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
      const label = this.labelFor(e.cardinality);
      const tooltipCard = e.cardinality ? e.cardinality : 'unknown';
      const edge: VisEdge = {
        id,
        from: e.from,
        to: e.to,
        title: `${e.label}\nconfidence ${this.fmt(e.confidence)} • containment ${this.fmt(e.containment)}\ncardinality ${tooltipCard}`,
        color: { color: this.colorFor(e.confidence) },
        arrows,
        smooth: false,
        width: e.confidence == null ? 1 : Math.max(1, e.confidence * 3),
      };
      if (label) {
        edge.label = label;
        // Small font with translucent white plate so the label reads over edge lines
        // regardless of confidence-tier color.
        edge.font = {
          size: 12,
          color: '#0d1117',
          face: 'ui-monospace, SFMono-Regular, monospace',
          background: 'rgba(255,255,255,0.8)',
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

  private colorFor(c: number | null): string {
    if (c == null) return '#666';
    if (c >= 0.95) return '#3fb950';
    if (c >= 0.85) return '#d29922';
    return '#8b949e';
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
        scaling: { min: 8, max: 28, label: { enabled: true, min: 12, max: 18 } },
        borderWidth: 1,
      },
      edges: {
        // Per-edge `arrows` objects override this default (used only when an
        // edge falls back to plain arrow rendering).
        arrows: { to: { enabled: true, type: 'arrow' } },
        smooth: false,
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
