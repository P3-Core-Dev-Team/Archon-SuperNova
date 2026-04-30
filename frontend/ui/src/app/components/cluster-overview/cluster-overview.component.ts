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
               [class.pii-border]="c.pii_table_count > 0"
               [class.junction-only]="isJunctionOnly(c)">
            <!-- Header row: small colored dot + "Cluster N" replaces the
                 old top stripe.  The dot still carries cluster colour but
                 with much less visual weight. -->
            <div class="card-head">
              <span class="cluster-dot"
                    [style.background]="colorOf(c.cluster_id)"
                    [title]="'Cluster ' + c.cluster_id"></span>
              <span class="cluster-index">Cluster {{ c.cluster_id }}</span>
              @if (c.pii_table_count > 0) {
                <span class="pii-flag" title="Contains PII findings">&#128274;</span>
              }
            </div>

            <div class="card-name">{{ c.name }}</div>

            <div class="stats-line">
              {{ c.table_count }} table{{ c.table_count !== 1 ? 's' : '' }}
              · {{ c.intra_edges }} intra-edge{{ c.intra_edges !== 1 ? 's' : '' }}
              · {{ c.inter_edges }} ext
            </div>

            <!-- Archetype proportion bar — full card width.  Inline
                 middot-separated labels render BELOW (not overlaid) so
                 nothing crams into a thin stacked bar. -->
            <div class="arch-bar" [attr.title]="archetypeTitle(c)">
              @for (seg of archetypeSegments(c); track seg.key) {
                <div class="seg"
                     [style.width.%]="seg.pct"
                     [style.background]="seg.color"
                     [attr.title]="seg.key + ' ' + seg.count">
                </div>
              }
            </div>
            <div class="arch-legend mono">
              @for (seg of archetypeSegments(c); track seg.key; let last = $last) {
                <span class="arch-label" [style.color]="seg.color">{{ archetypeAbbrev(seg.key) }}&nbsp;{{ seg.count }}</span>
                @if (!last) { <span class="dot-sep">·</span> }
              }
            </div>

            @if (c.pii_table_count > 0) {
              <div class="pii-line"
                   [attr.title]="c.subject_kinds.length > 2 ? c.subject_kinds.join(', ') : null">
                {{ c.pii_table_count }} table{{ c.pii_table_count !== 1 ? 's' : '' }} with PII
                @if (c.subject_kinds.length > 0) {
                  <span class="pii-kinds">
                    (<span class="pii-list">{{ visiblePiiTags(c.subject_kinds).join(', ') }}</span>@if (c.subject_kinds.length > 2) {<span class="more">&nbsp;+{{ c.subject_kinds.length - 2 }} more</span>})
                  </span>
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
      /* Larger min track on wide screens — auto-fill flows up to 4-5
       * cards per row before reflowing.  Each card stays comfortably
       * sized at narrow widths thanks to the 320px floor. */
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
    }

    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 14px 16px 16px;
      display: flex;
      flex-direction: column;
      /* Consistent 8px vertical rhythm across every row in the card. */
      gap: 8px;
      transition: box-shadow 0.15s, border-color 0.15s, transform 0.12s;
      position: relative;
    }
    /* Header row: cluster colour now lives in a small dot, not a chunky
       stripe — same identity, much less visual weight. */
    .card-head {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 11px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #8b949e;
    }
    .cluster-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      flex: 0 0 auto;
      box-shadow: 0 0 0 2px rgba(13, 17, 23, 0.9), 0 0 0 3px rgba(255, 255, 255, 0.04);
    }
    .cluster-index {
      font-weight: 600;
      color: #c9d1d9;
    }
    .pii-flag {
      margin-left: auto;
      font-size: 11px;
      color: #d29922;
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

    .stats-line {
      font-size: 12.5px;
      color: #c9d1d9;
    }

    /* Full-width archetype bar.  The label row sits below it so type
       names never overlap a thin stacked segment. */
    .arch-bar {
      width: 100%;
      height: 8px;
      border-radius: 4px;
      overflow: hidden;
      background: #21262d;
      display: flex;
    }
    .seg { height: 100%; transition: width 0.3s; }
    .arch-legend {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 6px;
      font-size: 11px;
      letter-spacing: 0.4px;
      color: #8b949e;
    }
    .arch-legend .arch-label { font-weight: 600; }
    .arch-legend .dot-sep { color: #444c56; }

    .pii-line {
      font-size: 12px;
      color: #ffa198;
      display: flex;
      align-items: baseline;
      gap: 4px;
      flex-wrap: wrap;
      cursor: help;
    }
    .pii-line .pii-kinds {
      color: #8b949e;
      font-size: 11px;
    }
    .pii-line .pii-list { color: #c9d1d9; }
    .pii-line .more { color: #8b949e; }

    .modularity-line {
      font-size: 11px;
      color: #6e7681;
      margin-top: -2px;
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

  // Unified palette matching cluster-detail.component.ts so the cluster
  // ERD bar and the cluster-card bar speak the same colour vocabulary.
  // FACT = coral-red / DIM = sky-blue / LOOKUP = green / BRIDGE = lavender.
  private static readonly ARCHETYPE_COLORS: Record<string, string> = {
    FACT:      '#f78166',
    DIMENSION: '#79c0ff',
    LOOKUP:    '#56d364',
    JUNCTION:  '#d29922',
    BRIDGE:    '#d2a8ff',
    REFERENCE: '#58a6ff',
    UNKNOWN:   '#8b949e',
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
        color: ClusterOverviewComponent.ARCHETYPE_COLORS[key] ?? '#8b949e',
      }));
  }

  /** Short, readable abbreviation for the inline middot-separated label
   * row beneath the archetype bar.  Matches cluster-detail's label
   * column ("FACT 7 · DIM 5 · LOOKUP 5"). */
  archetypeAbbrev(key: string): string {
    const abbr: Record<string, string> = {
      FACT: 'FACT', DIMENSION: 'DIM', LOOKUP: 'LOOKUP',
      JUNCTION: 'JN', BRIDGE: 'BR', REFERENCE: 'REF', UNKNOWN: '?',
    };
    return abbr[key] ?? key;
  }

  archetypeTitle(c: Cluster): string {
    return Object.entries(c.archetype_distribution)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${k}: ${v}`)
      .join(', ');
  }

  /** First two PII tag types — full list rendered in the row's title
   * tooltip when there are ≥3.  Avoids the awkward many-tag wrap
   * the previous design produced. */
  visiblePiiTags(tags: string[]): string[] {
    return tags.slice(0, 2);
  }
}
