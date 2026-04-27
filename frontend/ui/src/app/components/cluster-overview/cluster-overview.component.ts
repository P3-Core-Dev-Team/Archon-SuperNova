import { Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { JobService } from '../../services/job.service';
import { ClusterGraphComponent, clusterColor } from '../cluster-graph/cluster-graph.component';

/** Shape returned by GET /api/jobs/{id}/clusters (CL-3 contract). */
export interface Cluster {
  cluster_id: number;
  name: string;
  table_count: number;
  intra_edges: number;
  inter_edges: number;
  archetype_distribution: Record<string, number>;
  modularity_contribution: number;
  pii_table_count: number;
  subject_kinds: string[];
}

export interface ClustersResponse {
  schema: string;
  total_clusters: number;
  modularity: number;
  junctions_collapsed: number;
  clusters: Cluster[];
}

@Component({
  selector: 'app-cluster-overview',
  standalone: true,
  imports: [CommonModule, RouterLink, ClusterGraphComponent],
  template: `
    @if (loading()) {
      <div class="state-msg muted">Computing clusters...</div>
    }

    @if (!loading() && error()) {
      <div class="state-msg error-box">
        Clustering not yet computed for this job. Re-run with
        <code>discovery cluster --config &lt;yaml&gt;</code>
      </div>
    }

    @if (!loading() && !error() && data() && data()!.total_clusters === 0) {
      <div class="state-msg muted">
        No clusters yet. Run the pipeline with clustering enabled.
      </div>
    }

    @if (!loading() && !error() && data() && data()!.total_clusters > 0) {
      <div class="overview-header">
        <span>Schema: <strong>{{ data()!.schema }}</strong></span>
        <span class="sep">·</span>
        <span>Modularity: <strong>{{ data()!.modularity | number:'1.2-2' }}</strong></span>
        <span class="sep">·</span>
        <span>
          <strong>{{ data()!.total_clusters }}</strong> clusters
          @if (data()!.junctions_collapsed > 0) {
            · <strong>{{ data()!.junctions_collapsed }}</strong> junctions collapsed
          }
          @if (singletonCount() > 0) {
            · <strong>{{ singletonCount() }}</strong> singleton{{ singletonCount() !== 1 ? 's' : '' }}
          }
        </span>
        @if (singletonCount() > 0) {
          <label class="singleton-toggle">
            <input type="checkbox" [checked]="hideSingletons()" (change)="hideSingletons.set(!hideSingletons())" />
            Hide singleton clusters
          </label>
        }
        <span class="spacer"></span>
        <div class="view-toggle">
          <button class="seg-btn" [class.active]="view() === 'cards'" (click)="view.set('cards')">Cards</button>
          <button class="seg-btn" [class.active]="view() === 'graph'" (click)="view.set('graph')">Cluster graph</button>
        </div>
      </div>

      @if (view() === 'graph') {
        <!-- Columnar cluster graph: every table is a fixed node, one column
             per cluster, edges between columns are the "super-points". -->
        <app-cluster-graph
          [jobId]="jobId()"
          (clusterPick)="goToCluster($event)" />
      }

      @if (view() === 'cards') {
      <div class="grid">
        @for (c of sorted(); track c.cluster_id) {
          <div class="card"
               [style.borderTopColor]="colorOf(c.cluster_id)"
               [class.pii-border]="c.pii_table_count > 0"
               [class.junction-only]="isJunctionOnly(c)">
            <div class="card-name">{{ c.name }}</div>
            <div class="divider"></div>

            <div class="stats-line">
              {{ c.table_count }} table{{ c.table_count !== 1 ? 's' : '' }}
              &nbsp;·&nbsp;{{ c.intra_edges }} intra-edge{{ c.intra_edges !== 1 ? 's' : '' }}
            </div>
            <div class="inter-line">
              {{ c.inter_edges }} connect{{ c.inter_edges !== 1 ? 's' : '' }} to other clusters
            </div>

            <div class="archetype-bar-row">
              <div class="mini-bar" [attr.title]="archetypeTitle(c)">
                @for (seg of archetypeSegments(c); track seg.key) {
                  <div class="seg"
                       [style.width.%]="seg.pct"
                       [style.background]="seg.color"
                       [attr.title]="seg.key + ' ' + seg.count">
                  </div>
                }
              </div>
              <span class="archetype-labels">{{ archetypeLabel(c) }}</span>
            </div>

            @if (c.pii_table_count > 0) {
              <div class="pii-line">
                <span class="lock">&#128274;</span>
                {{ c.pii_table_count }} table{{ c.pii_table_count !== 1 ? 's' : '' }} with PII
                @if (c.subject_kinds.length > 0) {
                  <span class="pii-kinds">({{ c.subject_kinds.join(', ') }})</span>
                }
              </div>
            }

            <div class="modularity-line">
              modularity {{ c.modularity_contribution | number:'1.2-2' }}
            </div>

            <a class="open-btn"
               [routerLink]="['/jobs', jobId(), 'clusters', c.cluster_id]">
              Open cluster &rarr;
            </a>
          </div>
        }
      </div>
      }   <!-- /@if (view() === 'cards') -->
    }
  `,
  styles: [`
    :host { display: block; }

    .state-msg {
      padding: 24px;
      text-align: center;
      font-size: 14px;
    }
    .muted { color: #8b949e; }
    .error-box {
      color: #ffabab;
      background: #3a0d0d;
      border: 1px solid #f85149;
      border-radius: 6px;
      padding: 16px 20px;
      margin: 8px 0;
    }
    .error-box code {
      background: rgba(255,255,255,0.08);
      padding: 1px 6px;
      border-radius: 4px;
    }

    .overview-header {
      display: flex;
      flex-wrap: wrap;
      gap: 6px 10px;
      align-items: center;
      font-size: 13px;
      color: #8b949e;
      margin-bottom: 20px;
      padding: 10px 14px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
    }
    .overview-header strong { color: #e6edf3; }
    .sep { color: #444c56; }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 16px;
    }

    .card {
      background: #161b22;
      border: 1px solid #30363d;
      /* Top edge stripe is the cluster's color (matches the macro graph). */
      border-top: 4px solid #30363d;
      border-radius: 8px;
      padding: 12px 18px 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      transition: box-shadow 0.15s, border-color 0.15s, transform 0.12s;
      position: relative;
    }
    .overview-header .spacer { flex: 1; }
    .overview-header .view-toggle {
      display: inline-flex;
      border: 1px solid #30363d;
      border-radius: 6px;
      overflow: hidden;
      background: #0d1117;
    }
    .overview-header .view-toggle .seg-btn {
      background: transparent;
      color: #8b949e;
      border: none;
      padding: 5px 12px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }
    .overview-header .view-toggle .seg-btn:hover { color: #e6edf3; background: #161b22; }
    .overview-header .view-toggle .seg-btn.active {
      background: #1f6feb;
      color: white;
    }
    .card:hover {
      transform: translateY(-1px);
      border-color: #58a6ff;
      box-shadow: 0 4px 16px rgba(0,0,0,0.35);
    }

    /* PII indicator: red left border */
    .card.pii-border {
      border-left: 3px solid #f85149;
    }

    /* Junction-only cluster: dashed border */
    .card.junction-only {
      border-style: dashed;
    }
    .card.junction-only:hover {
      border-style: dashed;
    }

    .card-name {
      font-size: 15px;
      font-weight: 700;
      color: #e6edf3;
      word-break: break-word;
    }

    .divider {
      height: 1px;
      background: #30363d;
      margin: 2px 0 4px;
    }

    .stats-line {
      font-size: 13px;
      color: #c9d1d9;
    }
    .inter-line {
      font-size: 12px;
      color: #8b949e;
    }

    .archetype-bar-row {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .mini-bar {
      flex: 0 0 80px;
      height: 8px;
      border-radius: 4px;
      overflow: hidden;
      background: #21262d;
      display: flex;
    }
    .seg { height: 100%; transition: width 0.3s; }
    .archetype-labels {
      font-size: 11px;
      color: #8b949e;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 160px;
    }

    .pii-line {
      font-size: 12px;
      color: #ffa198;
      display: flex;
      align-items: baseline;
      gap: 4px;
      flex-wrap: wrap;
    }
    .pii-kinds {
      color: #8b949e;
      font-size: 11px;
    }

    .modularity-line {
      font-size: 11px;
      color: #6e7681;
      margin-top: 2px;
    }

    .open-btn {
      display: inline-block;
      margin-top: 6px;
      font-size: 12px;
      color: #58a6ff;
      text-decoration: none;
      border: 1px solid #1f6feb;
      border-radius: 4px;
      padding: 4px 10px;
      align-self: flex-start;
      transition: background 0.12s;
    }
    .open-btn:hover {
      background: #1f6feb22;
      text-decoration: none;
    }
  `],
})
export class ClusterOverviewComponent implements OnInit {
  /** Job ID passed in from JobDetail. */
  jobId = input.required<string>();

  private jobsSvc = inject(JobService);
  private router = inject(Router);

  data = signal<ClustersResponse | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);

  /** Toggle between Cards (grid) and Cluster graph (vis-network bubbles). */
  view = signal<'cards' | 'graph'>('cards');

  /** Stable per-cluster color (shared with the cluster-graph component). */
  colorOf(clusterId: number): string {
    return clusterColor(clusterId);
  }

  /** Bubble click → drill into cluster detail. */
  goToCluster(clusterId: number): void {
    this.router.navigate(['/jobs', this.jobId(), 'clusters', clusterId]);
  }

  /** When true, clusters with table_count == 1 are hidden from the grid. */
  hideSingletons = signal<boolean>(true);

  /** Number of singleton clusters in the current data. */
  singletonCount = computed<number>(() => {
    const d = this.data();
    if (!d) return 0;
    return d.clusters.filter(c => c.table_count <= 1).length;
  });

  /** Clusters sorted by table_count DESC; optionally singleton-filtered. */
  sorted = computed<Cluster[]>(() => {
    const d = this.data();
    if (!d) return [];
    const filtered = this.hideSingletons()
      ? d.clusters.filter(c => c.table_count > 1)
      : [...d.clusters];
    return filtered.sort((a, b) => b.table_count - a.table_count);
  });

  ngOnInit(): void {
    // jobsSvc.clusters() is implemented by CL-3.
    (this.jobsSvc as any).clusters(this.jobId()).subscribe({
      next: (r: ClustersResponse) => {
        this.data.set(r);
        this.loading.set(false);
      },
      error: (err: any) => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load clusters.',
        );
      },
    });
  }

  /** Returns true when every table in the cluster is a JUNCTION archetype. */
  isJunctionOnly(c: Cluster): boolean {
    const junctionCount = c.archetype_distribution['JUNCTION'] ?? 0;
    return c.table_count > 0 && junctionCount === c.table_count;
  }

  private static readonly ARCHETYPE_COLORS: Record<string, string> = {
    FACT:      '#1f6feb',
    DIMENSION: '#3fb950',
    JUNCTION:  '#d29922',
    BRIDGE:    '#a371f7',
    REFERENCE: '#58a6ff',
    UNKNOWN:   '#6e7681',
  };

  archetypeSegments(c: Cluster): Array<{ key: string; count: number; pct: number; color: string }> {
    const dist = c.archetype_distribution;
    const total = Object.values(dist).reduce((s, v) => s + v, 0) || 1;
    return Object.entries(dist)
      .filter(([, v]) => v > 0)
      .map(([key, count]) => ({
        key,
        count,
        pct: (count / total) * 100,
        color: ClusterOverviewComponent.ARCHETYPE_COLORS[key] ?? '#6e7681',
      }));
  }

  archetypeLabel(c: Cluster): string {
    return Object.entries(c.archetype_distribution)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => {
        const abbr: Record<string, string> = {
          FACT: 'FACT', DIMENSION: 'DIM', JUNCTION: 'JN',
          BRIDGE: 'BR', REFERENCE: 'REF', UNKNOWN: '?',
        };
        return `${abbr[k] ?? k} ${v}`;
      })
      .join(' ');
  }

  archetypeTitle(c: Cluster): string {
    return Object.entries(c.archetype_distribution)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${k}: ${v}`)
      .join(', ');
  }
}
