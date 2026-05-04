import {
  AfterViewInit, Component, ElementRef, Input, OnChanges, OnDestroy,
  ViewChild
} from '@angular/core';
import { DataSet } from 'vis-data/peer';
import { Edge as VisEdge, Network, Node as VisNode, Options } from 'vis-network/peer';
import { ApiService } from '../../core/api.service';

export interface GraphNode {
  id: string;
  label: string;
  value?: number;
}

export interface GraphEdge {
  source?: string;
  from?: string;
  target?: string;
  to?: string;
  label?: string;
  score?: number;
  confidence?: number;
  cardinality?: string;
}

export interface GraphData {
  schema?: string;
  nodes?: GraphNode[];
  edges?: GraphEdge[];
}

@Component({
  selector: 'app-relationship-graph',
  template: `
    <div class="toolbar">
      <span class="muted">Schema: <strong>{{ schema }}</strong></span>
      <span class="muted">Tables: <strong>{{ totalTables }}</strong></span>
      <span class="counter">
        <strong class="big">{{ visibleEdgeCount }}</strong>
        <span class="muted">of {{ totalEdges }} relationships</span>
      </span>
      <label class="slider-label">
        Min confidence:
        <input type="range" min="0" max="1" step="0.05"
               [value]="minConfidence"
               (input)="onConfidenceChange($event)" />
        <span class="mono">{{ minConfidence | number:'1.2-2' }}</span>
      </label>
      <button class="btn" type="button"
              (click)="toggleHierarchical()"
              [disabled]="!network"
              [class.active]="hierarchical">
        Hierarchical layout: <strong>{{ hierarchical ? 'on' : 'off' }}</strong>
      </button>
      <button class="btn" type="button" (click)="fitToScreen()" [disabled]="!network">Fit to screen</button>
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
        <div class="legend-row"><span class="card-glyph">|—|</span> 1:1</div>
        <div class="legend-row"><span class="card-glyph">|—&lt;</span> 1:N</div>
        <div class="legend-row"><span class="card-glyph">&gt;—|</span> N:1</div>
        <div class="legend-row"><span class="card-glyph">&gt;—&lt;</span> N:M</div>
      </div>
      <div class="overlay muted" *ngIf="loading">Loading graph…</div>
      <div class="overlay muted" *ngIf="!loading && !error && totalEdges === 0">No relationships discovered yet.</div>
    </div>

    <div class="edge-panel card" *ngIf="selectedEdge">
      <div class="title">Edge detail</div>
      <div><strong>{{ selectedEdge.source || selectedEdge.from }}</strong> → <strong>{{ selectedEdge.target || selectedEdge.to }}</strong></div>
      <div class="muted">{{ selectedEdge.label }}</div>
      <div>Cardinality: <code>{{ selectedEdge.cardinality }}</code></div>
      <div>Confidence: {{ (selectedEdge.score || selectedEdge.confidence || 0) | number:'1.3-3' }}</div>
    </div>

    <div class="error" *ngIf="error">{{ error }}</div>
  `,
  styles: [`
    .toolbar { display: flex; gap: 22px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
    .toolbar label { display: inline-flex; gap: 8px; align-items: center; font-size: 13px; color: #e6edf3; }
    .counter { display: inline-flex; align-items: baseline; gap: 6px; padding: 4px 10px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; }
    .counter .big { font-size: 18px; color: #58a6ff; font-variant-numeric: tabular-nums; }
    .slider-label input[type=range] { width: 160px; }
    .slider-label .mono { min-width: 36px; text-align: right; color: #58a6ff; font-weight: 600; }
    .graph-wrap { position: relative; }
    .graph { width: 100%; height: 620px; background: rgba(13,17,23,0.5); border: 1px solid var(--border); border-radius: 8px; position: relative; }
    .legend { position: absolute; bottom: 12px; right: 12px; background: rgba(13, 17, 23, 0.85); border: 1px solid #30363d; border-radius: 6px; padding: 8px 10px; font-size: 12px; pointer-events: none; color: #e6edf3; }
    .legend-title { font-size: 10px; letter-spacing: 0.6px; text-transform: uppercase; color: #8b949e; margin-bottom: 4px; }
    .legend-row { display: flex; align-items: center; gap: 6px; line-height: 1.6; }
    .swatch { display: inline-block; width: 14px; height: 3px; border-radius: 2px; }
    .legend-divider { height: 1px; background: #30363d; margin: 6px -2px 4px; }
    .card-glyph { display: inline-block; min-width: 30px; font-family: monospace; font-size: 11px; color: #c9d1d9; text-align: center; }
    .overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
    .edge-panel { margin-top: 12px; max-width: 600px; background: rgba(13,17,23,0.8); border: 1px solid var(--border); padding: 12px; border-radius: 8px; color: #e6edf3; }
    .edge-panel .title { font-size: 11px; letter-spacing: 0.6px; text-transform: uppercase; color: #8b949e; margin-bottom: 6px; }
    .error { color: #ffabab; padding: 12px; background: #3a0d0d; border: 1px solid #f85149; border-radius: 6px; margin-top: 8px; }
    .btn.active { background: #1f6feb; color: #fff; }
  `]
})
export class RelationshipGraphComponent implements AfterViewInit, OnChanges, OnDestroy {
  @Input() jobId!: string;
  @ViewChild('container') container!: ElementRef<HTMLDivElement>;

  schema = '';
  totalTables = 0;
  totalEdges = 0;
  visibleEdgeCount = 0;
  loading = true;
  error: string | null = null;
  selectedEdge: GraphEdge | null = null;
  minConfidence = 0;
  hierarchical = false;

  network: Network | null = null;
  private allEdges: GraphEdge[] = [];
  private nodesData = new DataSet<VisNode>();
  private edgesData = new DataSet<VisEdge>();
  private edgeById = new Map<string, GraphEdge>();

  constructor(private api: ApiService) {}

  ngAfterViewInit(): void {
    this.load();
  }

  ngOnChanges(): void {
    if (this.container) this.load();
  }

  ngOnDestroy(): void {
    this.network?.destroy();
  }

  onConfidenceChange(ev: Event): void {
    const v = +(ev.target as HTMLInputElement).value;
    this.minConfidence = v;
    this.applyFilter();
  }

  fitToScreen(): void {
    this.network?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
  }

  toggleHierarchical(): void {
    if (!this.network) return;
    this.hierarchical = !this.hierarchical;
    if (this.hierarchical) {
      this.network.setOptions({
        layout: {
          hierarchical: { enabled: true, direction: 'UD', sortMethod: 'directed', nodeSpacing: 200, levelSeparation: 200, edgeMinimization: true }
        },
        physics: { enabled: false }
      });
      setTimeout(() => this.network?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }), 0);
    } else {
      this.network.setOptions({
        layout: { hierarchical: { enabled: false } },
        physics: {
          enabled: true,
          barnesHut: { gravitationalConstant: -12000, centralGravity: 0.2, springLength: 110, springConstant: 0.04, damping: 0.4 },
          stabilization: { iterations: 250 }
        }
      });
      this.armFreezeOnStabilize();
      this.network.once('stabilized', () => this.network?.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }));
    }
  }

  private armFreezeOnStabilize(): void {
    this.network?.once('stabilizationIterationsDone', () => {
      if (!this.hierarchical) {
        this.network?.setOptions({ physics: { enabled: false } });
      }
    });
  }

  private load(): void {
    this.loading = true;
    this.error = null;
    this.api.getJobRelationships(this.jobId).subscribe({
      next: (g: GraphData) => {
        this.schema = g?.schema || 'public';
        this.totalTables = g?.nodes?.length || 0;
        this.totalEdges = g?.edges?.length || 0;
        this.allEdges = g?.edges || [];
        this.paintNodes(g?.nodes || []);
      },
      error: (err: Error) => {
        this.loading = false;
        this.error = err?.message || 'Failed to load relationship graph.';
      }
    });
  }

  private paintNodes(nodes: GraphNode[]): void {
    this.nodesData.clear();
    this.nodesData.add(nodes.map((n: GraphNode) => ({
      id: n.id,
      label: n.label,
      value: n.value || 1,
      title: `${n.label}\\n${n.value || 1} rows`,
      font: { color: '#ffffff', face: 'monospace', size: 13, strokeWidth: 3, strokeColor: '#0d1117' },
      shape: 'dot',
      color: { background: '#1f6feb', border: '#58a6ff' }
    })));
    this.applyFilter();
    if (!this.network) this.initNetwork();
    this.loading = false;
  }

  private applyFilter(): void {
    this.edgesData.clear();
    this.edgeById.clear();
    let visibleCount = 0;
    const visEdges: VisEdge[] = [];
    this.allEdges.forEach((e, i) => {
      const conf = e.score || e.confidence || 1.0;
      if (conf < this.minConfidence) return;
      const id = `e${i}`;
      this.edgeById.set(id, e);
      visibleCount++;
      const edge: VisEdge = {
        id,
        from: e.source || e.from,
        to: e.target || e.to,
        title: `${e.label || ''}\\nconfidence ${conf.toFixed(3)}\\ncardinality ${e.cardinality || '1:N'}`,
        color: { color: this.colorFor(conf) },
        arrows: this.arrowsFor(e.cardinality),
        smooth: false,
        width: Math.max(1, conf * 3)
      };
      const label = this.labelFor(e.cardinality);
      if (label) {
        edge.label = label;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        edge.font = { size: 12, color: '#0d1117', face: 'monospace', background: 'rgba(255,255,255,0.8)', strokeWidth: 0, align: 'middle' } as any;
      }
      visEdges.push(edge);
    });
    this.edgesData.add(visEdges);
    this.visibleEdgeCount = visibleCount;
  }

  private arrowsFor(cardinality: string | null | undefined): VisEdge['arrows'] {
    switch (cardinality) {
      case '1:1': case 'ONE_TO_ONE': return { to: { enabled: true, type: 'bar', scaleFactor: 0.9 }, from: { enabled: true, type: 'bar', scaleFactor: 0.9 } };
      case '1:N': case 'ONE_TO_MANY': return { to: { enabled: true, type: 'crow', scaleFactor: 1.1 }, from: { enabled: true, type: 'bar', scaleFactor: 0.9 } };
      case 'N:1': case 'MANY_TO_ONE': return { to: { enabled: true, type: 'bar', scaleFactor: 0.9 }, from: { enabled: true, type: 'crow', scaleFactor: 1.1 } };
      case 'N:M': case 'MANY_TO_MANY': return { to: { enabled: true, type: 'crow', scaleFactor: 1.1 }, from: { enabled: true, type: 'crow', scaleFactor: 1.1 } };
      default: return { to: { enabled: true, type: 'arrow' } };
    }
  }

  private labelFor(cardinality: string | null | undefined): string | null {
    switch (cardinality) {
      case '1:1': case 'ONE_TO_ONE': return '1:1';
      case '1:N': case 'ONE_TO_MANY': return '1:N';
      case 'N:1': case 'MANY_TO_ONE': return 'N:1';
      case 'N:M': case 'MANY_TO_MANY': return 'N:M';
      default: return null;
    }
  }

  private colorFor(c: number): string {
    if (c >= 0.95) return '#3fb950';
    if (c >= 0.85) return '#d29922';
    return '#8b949e';
  }

  private initNetwork(): void {
    const options: Options = {
      autoResize: true,
      physics: { enabled: true, barnesHut: { gravitationalConstant: -12000, centralGravity: 0.2, springLength: 110, springConstant: 0.04, damping: 0.4 }, stabilization: { iterations: 250 } },
      interaction: { hover: true, multiselect: false, tooltipDelay: 250 },
      nodes: { scaling: { min: 8, max: 28, label: { enabled: true, min: 12, max: 18 } }, borderWidth: 1 },
      edges: { arrows: { to: { enabled: true, type: 'arrow' } }, smooth: false }
    };
    this.network = new Network(this.container.nativeElement, { nodes: this.nodesData, edges: this.edgesData }, options);
    this.network.on('selectEdge', params => {
      const id = params.edges[0];
      if (id != null) this.selectedEdge = this.edgeById.get(String(id)) || null;
    });
    this.network.on('deselectEdge', () => this.selectedEdge = null);
    this.armFreezeOnStabilize();
  }
}
