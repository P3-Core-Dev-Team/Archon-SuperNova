/**
 * Cluster-graph view (columnar).
 *
 * Renders ALL tables in the schema as a vis-network graph laid out in
 * columns — one column per cluster, tables stacked vertically within each
 * column.  Cross-cluster FK edges become visible "super-point" connectors
 * between columns.  Physics is disabled and every node is fixed in place
 * so the layout doesn't drift.
 *
 * This replaces the earlier macro-bubble view (which showed each cluster
 * as a single dot).  The user wanted to see every table.
 */
import {
  Component, AfterViewInit, OnDestroy, ElementRef, ViewChild,
  inject, input, output, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { DataSet } from 'vis-data/peer';
import {
  Edge as VisEdge, Network, Node as VisNode, Options,
} from 'vis-network/peer';
import { JobService } from '../../services/job.service';
import { Cluster, ClusterDetail, RelationshipEdge, RelationshipGraph } from '../../models/job.model';

const CLUSTER_PALETTE = [
  '#0969da', '#1a7f37', '#9a6700', '#8250df', '#cf222e',
  '#0969da', '#1a7f37', '#9a6700', '#8250df', '#cf222e',
  '#0969da', '#1a7f37', '#bc4c00', '#8250df', '#cf222e',
];
export function clusterColor(clusterId: number): string {
  return CLUSTER_PALETTE[((clusterId % CLUSTER_PALETTE.length) + CLUSTER_PALETTE.length) % CLUSTER_PALETTE.length];
}

const COLUMN_WIDTH    = 240;
const ROW_HEIGHT      = 56;
const HEADER_Y        = -42;
const FIRST_ROW_Y     = 12;
const SINGLETON_LABEL = '(unclustered / singletons)';

@Component({
  selector: 'app-cluster-graph',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="cg-toolbar">
      <span class="muted">Schema: <strong>{{ schema() }}</strong></span>
      <span class="muted">Tables: <strong>{{ tableCount() }}</strong></span>
      <span class="muted">Clusters: <strong>{{ realClusterCount() }}</strong></span>
      <span class="muted">Edges: <strong>{{ edgeCount() }}</strong></span>
      <span class="spacer"></span>
      <button class="fit-btn" (click)="fit()" [disabled]="!network">Fit to screen</button>
    </div>

    @if (loading()) {
      <div class="muted center pad">Loading cluster graph…</div>
    }
    @if (!loading() && tableCount() === 0) {
      <div class="muted center pad">No tables to render.</div>
    }

    <div #container class="cg-graph"
         [class.hidden]="loading() || tableCount() === 0"></div>

    <p class="muted small footer">
      Each column is one cluster.  Cross-column edges are FK paths between
      clusters — your "super-points".
    </p>
  `,
  styles: [`
    .cg-toolbar {
      display: flex;
      align-items: center;
      gap: 22px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    .cg-toolbar .spacer { flex: 1; }
    .cg-toolbar .fit-btn {
      background: #ffffff;
      border: 1px solid #d0d7de;
      color: #1f2328;
      padding: 5px 12px;
      border-radius: 4px;
      cursor: pointer;
    }
    .cg-toolbar .fit-btn:hover:not(:disabled) { border-color: #0969da; }
    .cg-graph {
      width: 100%;
      height: 640px;
      background: #f6f8fa;
      border: 1px solid #d0d7de;
      border-radius: 8px;
    }
    .cg-graph.hidden { display: none; }
    .center { text-align: center; padding: 30px 0; }
    .muted { color: #656d76; }
    .small { font-size: 12px; }
    .footer { margin-top: 8px; }
    .pad { padding: 24px 0; }
  `],
})
export class ClusterGraphComponent implements AfterViewInit, OnDestroy {
  jobId = input.required<string>();
  /** Emitted when the user clicks a column header (cluster). */
  clusterPick = output<number>();
  /** Emitted when the user clicks a table node. */
  tablePick = output<string>();

  @ViewChild('container') container?: ElementRef<HTMLDivElement>;

  private jobsSvc = inject(JobService);

  schema = signal<string>('');
  loading = signal(true);
  tableCount = signal(0);
  realClusterCount = signal(0);
  edgeCount = signal(0);

  network: Network | null = null;
  private nodesData = new DataSet<VisNode>();
  private edgesData = new DataSet<VisEdge>();

  ngAfterViewInit(): void {
    this.load();
  }
  ngOnDestroy(): void {
    this.network?.destroy();
  }

  fit(): void {
    this.network?.fit({ animation: { duration: 250, easingFunction: 'easeInOutQuad' } });
  }

  private load(): void {
    this.loading.set(true);
    this.jobsSvc.relationships(this.jobId(), 1000).subscribe({
      next: g => {
        this.schema.set(g.schema);
        this.jobsSvc.clusters(this.jobId()).subscribe({
          next: cl => {
            const real = cl.clusters.filter(c => c.table_count > 1);
            this.realClusterCount.set(real.length);
            if (real.length === 0) {
              // No clusters yet — fall back to a single-column dump.
              this.layoutSingleColumn(g);
              this.loading.set(false);
              return;
            }
            // Need member-table names per cluster.
            const detailsByCluster = new Map<number, ClusterDetail>();
            let pending = real.length;
            for (const c of real) {
              this.jobsSvc.clusterDetail(this.jobId(), c.cluster_id).subscribe({
                next: cd => detailsByCluster.set(c.cluster_id, cd),
                error: () => { /* tolerate partial */ },
                complete: () => {
                  if (--pending === 0) {
                    this.layoutByCluster(g, real, detailsByCluster);
                    this.loading.set(false);
                  }
                },
              });
            }
          },
          error: () => {
            this.layoutSingleColumn(g);
            this.loading.set(false);
          },
        });
      },
      error: () => this.loading.set(false),
    });
  }

  /** Lay out tables in columns, one per cluster.  Tables not in any
   *  cluster (singletons / unassigned) go in a final "(unclustered)" column. */
  private layoutByCluster(
    g: RelationshipGraph,
    real: Cluster[],
    detailsByCluster: Map<number, ClusterDetail>,
  ): void {
    if (!this.container) return;

    // Map every known table → its cluster_id (-1 for unclustered).
    const tableToCluster = new Map<string, number>();
    for (const c of real) {
      const cd = detailsByCluster.get(c.cluster_id);
      if (!cd) continue;
      for (const t of cd.tables) tableToCluster.set(t.table_name, c.cluster_id);
    }

    // Bucket every node into a column.  Sorted columns: real clusters in
    // descending table_count, then "(unclustered)".
    const columnsOrdered = [...real].sort((a, b) => b.table_count - a.table_count);
    const colIndex = new Map<number, number>();
    columnsOrdered.forEach((c, i) => colIndex.set(c.cluster_id, i));
    const UNCLUSTERED = -1;
    const unclusteredColIdx = columnsOrdered.length;

    const tablesByColumn = new Map<number, string[]>();
    for (const c of columnsOrdered) tablesByColumn.set(c.cluster_id, []);
    tablesByColumn.set(UNCLUSTERED, []);

    for (const n of g.nodes) {
      const cid = tableToCluster.get(n.id);
      const key = (cid !== undefined && colIndex.has(cid)) ? cid : UNCLUSTERED;
      tablesByColumn.get(key)!.push(n.id);
    }
    // Sort within each column alphabetically.
    for (const list of tablesByColumn.values()) list.sort();

    // ---- Build vis-network DataSets ----
    this.nodesData.clear();
    this.edgesData.clear();

    // Header nodes (one per column).
    for (const c of columnsOrdered) {
      const idx = colIndex.get(c.cluster_id)!;
      this.nodesData.add({
        id: `__hdr_${c.cluster_id}`,
        label: c.name,
        x: idx * COLUMN_WIDTH,
        y: HEADER_Y,
        fixed: { x: true, y: true } as any,
        physics: false as any,
        shape: 'text',
        font: {
          color: '#ffffff',
          face: 'ui-monospace, SFMono-Regular, monospace',
          size: 15,
          strokeWidth: 4,
          strokeColor: clusterColor(c.cluster_id),
        } as any,
      });
    }
    if (tablesByColumn.get(UNCLUSTERED)!.length > 0) {
      this.nodesData.add({
        id: '__hdr_uncl',
        label: SINGLETON_LABEL,
        x: unclusteredColIdx * COLUMN_WIDTH,
        y: HEADER_Y,
        fixed: { x: true, y: true } as any,
        physics: false as any,
        shape: 'text',
        font: {
          color: '#ffffff',
          face: 'ui-monospace, SFMono-Regular, monospace',
          size: 14,
          strokeWidth: 4,
          strokeColor: '#d0d7de',
        } as any,
      });
    }

    // Table nodes (fixed position).
    for (const c of columnsOrdered) {
      const idx = colIndex.get(c.cluster_id)!;
      const fill = clusterColor(c.cluster_id);
      const list = tablesByColumn.get(c.cluster_id)!;
      list.forEach((tname, row) => {
        this.nodesData.add({
          id: tname,
          label: tname,
          x: idx * COLUMN_WIDTH,
          y: FIRST_ROW_Y + row * ROW_HEIGHT,
          fixed: { x: true, y: true } as any,
          physics: false as any,
          shape: 'box',
          widthConstraint: { minimum: COLUMN_WIDTH - 40, maximum: COLUMN_WIDTH - 40 } as any,
          color: { background: fill, border: fill } as any,
          font: {
            color: '#ffffff',
            face: 'ui-monospace, SFMono-Regular, monospace',
            size: 12,
            strokeWidth: 2,
            strokeColor: '#f6f8fa',
          } as any,
        });
      });
    }
    const unclList = tablesByColumn.get(UNCLUSTERED)!;
    unclList.forEach((tname, row) => {
      this.nodesData.add({
        id: tname,
        label: tname,
        x: unclusteredColIdx * COLUMN_WIDTH,
        y: FIRST_ROW_Y + row * ROW_HEIGHT,
        fixed: { x: true, y: true } as any,
        physics: false as any,
        shape: 'box',
        widthConstraint: { minimum: COLUMN_WIDTH - 40, maximum: COLUMN_WIDTH - 40 } as any,
        color: { background: '#f6f8fa', border: '#d0d7de' } as any,
        font: {
          color: '#1f2328',
          face: 'ui-monospace, SFMono-Regular, monospace',
          size: 12,
        } as any,
      });
    });
    this.tableCount.set(g.nodes.length);

    // Edges — confidence-tier color, no labels (they'd clutter columns).
    g.edges.forEach((e, i) => {
      this.edgesData.add({
        id: `e${i}`,
        from: e.from,
        to: e.to,
        title: `${e.label}\nconfidence ${e.confidence == null ? '?' : e.confidence.toFixed(3)}`,
        color: { color: edgeColor(e.confidence), highlight: '#0969da', opacity: 0.85 } as any,
        smooth: { enabled: true, type: 'curvedCW', roundness: 0.18 } as any,
        arrows: { to: { enabled: true, scaleFactor: 0.6 } } as any,
      });
    });
    this.edgeCount.set(g.edges.length);

    this.initNetwork(/*hierarchical=*/false);
  }

  /** Fallback when no clusters are computed yet — single column dump. */
  private layoutSingleColumn(g: RelationshipGraph): void {
    if (!this.container) return;
    this.nodesData.clear();
    this.edgesData.clear();
    g.nodes.slice().sort((a, b) => a.id.localeCompare(b.id)).forEach((n, row) => {
      this.nodesData.add({
        id: n.id,
        label: n.label,
        x: 0,
        y: FIRST_ROW_Y + row * ROW_HEIGHT,
        fixed: { x: true, y: true } as any,
        physics: false as any,
        shape: 'box',
        color: { background: '#0969da', border: '#0969da' } as any,
        font: { color: '#ffffff', face: 'ui-monospace', size: 12 } as any,
      });
    });
    g.edges.forEach((e, i) => {
      this.edgesData.add({
        id: `e${i}`,
        from: e.from, to: e.to,
        color: { color: edgeColor(e.confidence) } as any,
        smooth: { enabled: true, type: 'curvedCW', roundness: 0.18 } as any,
      });
    });
    this.tableCount.set(g.nodes.length);
    this.edgeCount.set(g.edges.length);
    this.initNetwork(false);
  }

  private initNetwork(_hier: boolean): void {
    if (!this.container) return;
    if (this.network) { this.network.destroy(); this.network = null; }

    const options: Options = {
      physics: { enabled: false },
      interaction: { hover: true, tooltipDelay: 120, zoomView: true, dragView: true },
      nodes: { borderWidth: 2, shape: 'box' },
      edges: {
        smooth: { enabled: true, type: 'curvedCW', roundness: 0.18 } as any,
        arrows: { to: { enabled: true, scaleFactor: 0.6 } } as any,
      },
      layout: { hierarchical: { enabled: false }, randomSeed: 42 },
    };
    this.network = new Network(
      this.container.nativeElement,
      { nodes: this.nodesData, edges: this.edgesData },
      options,
    );
    this.network.on('selectNode', params => {
      const id = params.nodes[0];
      if (id == null) return;
      const sid = String(id);
      if (sid.startsWith('__hdr_')) {
        const rest = sid.slice('__hdr_'.length);
        if (rest === 'uncl') return;
        this.clusterPick.emit(Number(rest));
      } else {
        this.tablePick.emit(sid);
      }
    });
    // After layout settles to fit-to-screen.
    setTimeout(() => this.fit(), 0);
  }
}

// Shared helper — confidence-tier edge color (matches relationship-graph).
function edgeColor(c: number | null | undefined): string {
  if (c == null) return '#656d76';
  if (c >= 0.95) return '#1a7f37';
  if (c >= 0.85) return '#9a6700';
  return '#656d76';
}
