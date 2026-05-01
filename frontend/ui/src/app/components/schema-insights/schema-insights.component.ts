import { Component, OnInit, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { JobService } from '../../services/job.service';
import { SchemaInsights } from '../../models/job.model';

/**
 * "Schema insights" panel — five high-level pattern detections that
 * surface as a digest at the top of the Clusters tab.
 *
 *   1. known-schema fingerprint (AdventureWorks / Northwind / Saleor / ...)
 *   2. temporal-tracking / CDC support
 *   3. surrogate-key prevalence
 *   4. bridge / junction tables
 *   5. subtype / supertype (polymorphic root) patterns
 *
 * All five are computed on-the-fly by the API endpoint
 * ``GET /api/jobs/{id}/insights``; this component is a presentation-
 * only consumer with no derivation logic of its own.
 */
@Component({
  selector: 'app-schema-insights',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    @if (loading()) { <div class="loading muted small">Loading schema insights…</div> }
    @if (error()) {
      <div class="error small">{{ error() }}</div>
    }
    @if (data(); as d) {
      <div class="insights-grid">

        <!-- 1. Known-schema fingerprint -->
        <div class="insight-card">
          <div class="insight-title">Schema fingerprint</div>
          @if (d.schema_match; as m) {
            <div class="match-name">{{ m.name }}</div>
            <div class="match-meta muted small">
              {{ (m.confidence * 100) | number:'1.0-0' }}% match ·
              {{ m.matched.length }} of {{ m.anchor_size }} anchor tables
              @if (m.missing.length > 0) {
                · {{ m.missing.length }} missing
              }
            </div>
            @if (m.missing.length > 0) {
              <details class="diff">
                <summary>show missing / extra</summary>
                <div class="diff-cols">
                  <div>
                    <div class="diff-head">Missing ({{ m.missing.length }})</div>
                    <div class="diff-list mono small">
                      @for (t of m.missing.slice(0, 12); track t) {
                        <span class="chip miss">{{ t }}</span>
                      }
                      @if (m.missing.length > 12) {
                        <span class="muted small">+{{ m.missing.length - 12 }} more</span>
                      }
                    </div>
                  </div>
                  <div>
                    <div class="diff-head">Extra ({{ m.extra_count }})</div>
                    <div class="diff-list mono small">
                      @for (t of m.extra_sample.slice(0, 12); track t) {
                        <span class="chip extra">{{ t }}</span>
                      }
                      @if (m.extra_count > 12) {
                        <span class="muted small">+{{ m.extra_count - 12 }} more</span>
                      }
                    </div>
                  </div>
                </div>
              </details>
            }
          } @else {
            <div class="match-none muted small">
              No close match against the known-schema dictionary
              (AdventureWorks / Northwind / Saleor / DVDRental /
              WordPress / Drupal / Magento).
            </div>
          }
        </div>

        <!-- 2. Temporal / CDC -->
        <div class="insight-card">
          <div class="insight-title">Temporal tracking</div>
          @if (d.temporal; as t) {
            <div class="big" [class.good]="t.supports_cdc">
              {{ (t.fraction * 100) | number:'1.0-0' }}%
            </div>
            <div class="muted small">
              {{ t.tracked_tables }} of {{ t.total_tables }} tables carry a
              <code>modified_date</code> / <code>updated_at</code> column
            </div>
            @if (t.supports_cdc) {
              <div class="callout success small">
                ✓ Schema supports CDC / incremental processing —
                temporal columns ≥ 75%.
              </div>
            } @else {
              <div class="callout caution small">
                Temporal coverage below 75% — incremental processing
                will need application-level change tracking.
              </div>
            }
          } @else {
            <div class="muted small">No temporal data available.</div>
          }
        </div>

        <!-- 3. Surrogate-key prevalence -->
        <div class="insight-card">
          <div class="insight-title">Surrogate keys</div>
          @if (d.surrogate_keys; as s) {
            <div class="big">
              {{ (s.surrogate_pct * 100) | number:'1.0-0' }}%
            </div>
            <div class="muted small">
              {{ s.surrogate_count }} of {{ s.tables_with_pk }} tables use
              <code>*_id</code>-shaped PKs
              · {{ (s.integer_pct * 100) | number:'1.0-0' }}% are integer-typed
            </div>
          } @else {
            <div class="muted small">No PK columns found.</div>
          }
        </div>

        <!-- 4. Bridge tables -->
        <div class="insight-card">
          <div class="insight-title">
            Bridge tables
            <span class="count">{{ d.bridge_tables.length }}</span>
          </div>
          @if (d.bridge_tables.length === 0) {
            <div class="muted small">No M:N bridge tables detected.</div>
          } @else {
            <ul class="ent-list">
              @for (b of d.bridge_tables.slice(0, 6); track b.table) {
                <li>
                  <a class="tlink mono" [routerLink]="['/jobs', jobId(), 'tables', b.table]">{{ b.table }}</a>
                  <span class="muted small">
                    → {{ b.parents.join(' + ') }}
                  </span>
                </li>
              }
              @if (d.bridge_tables.length > 6) {
                <li class="muted small">+{{ d.bridge_tables.length - 6 }} more</li>
              }
            </ul>
          }
        </div>

        <!-- 5. Supertype / subtype -->
        <div class="insight-card wide">
          <div class="insight-title">
            Polymorphic roots
            <span class="count">{{ d.subtype_supertype.length }}</span>
          </div>
          @if (d.subtype_supertype.length === 0) {
            <div class="muted small">No supertype/subtype patterns detected.</div>
          } @else {
            <ul class="ent-list">
              @for (s of d.subtype_supertype; track s.supertype + s.fk_column) {
                <li>
                  <a class="tlink mono" [routerLink]="['/jobs', jobId(), 'tables', s.supertype]">{{ s.supertype }}</a>
                  <span class="muted small">via</span>
                  <code class="mono">{{ s.fk_column }}</code>
                  <span class="arrow muted">←</span>
                  @for (sub of s.subtypes; track sub; let last = $last) {
                    <a class="tlink mono"
                       [routerLink]="['/jobs', jobId(), 'tables', sub]">{{ sub }}</a>{{ last ? '' : ', ' }}
                  }
                </li>
              }
            </ul>
          }
        </div>

      </div>
    }
  `,
  styles: [`
    :host { display: block; margin-bottom: 18px; }
    .loading, .error { padding: 8px 0; }
    .error { color: #ffabab; }

    .insights-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 14px;
    }
    .insight-card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .insight-card.wide { grid-column: 1 / -1; }

    .insight-title {
      font-size: 11px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #8b949e;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .insight-title .count {
      background: #21262d;
      color: #c9d1d9;
      border: 1px solid #30363d;
      padding: 1px 6px;
      border-radius: 8px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0;
    }

    .match-name {
      font-size: 18px;
      font-weight: 700;
      color: #e6edf3;
    }
    .match-meta { margin-top: -2px; }
    .match-none { line-height: 1.4; }

    .big {
      font-size: 24px;
      font-weight: 700;
      color: #e6edf3;
      font-variant-numeric: tabular-nums;
    }
    .big.good { color: #56d364; }

    .callout {
      margin-top: 4px;
      padding: 6px 10px;
      border-radius: 4px;
      line-height: 1.4;
    }
    .callout.success { color: #aff5c4; background: rgba(86, 211, 100, 0.10); border: 1px solid rgba(86, 211, 100, 0.3); }
    .callout.caution { color: #e3b341; background: rgba(210, 153, 34, 0.10); border: 1px solid rgba(210, 153, 34, 0.3); }

    .diff { margin-top: 6px; }
    .diff summary {
      cursor: pointer;
      color: #58a6ff;
      font-size: 12px;
      list-style: none;
    }
    .diff summary::-webkit-details-marker { display: none; }
    .diff-cols {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 8px;
    }
    .diff-head {
      font-size: 10px;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      color: #8b949e;
      margin-bottom: 4px;
    }
    .diff-list { display: flex; gap: 4px; flex-wrap: wrap; }
    .chip {
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 10px;
    }
    .chip.miss  { background: rgba(248, 81, 73, 0.10); color: #ffabab; border: 1px solid rgba(248, 81, 73, 0.3); }
    .chip.extra { background: rgba(86, 211, 100, 0.10); color: #aff5c4; border: 1px solid rgba(86, 211, 100, 0.3); }

    .ent-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
    .ent-list li {
      font-size: 12px;
      line-height: 1.5;
      display: flex;
      align-items: baseline;
      gap: 6px;
      flex-wrap: wrap;
    }
    .arrow { font-size: 11px; }

    .tlink { color: #58a6ff; text-decoration: none; }
    .tlink:hover { text-decoration: underline; }
    .muted { color: #8b949e; }
    .small { font-size: 12px; }
    .mono  { font-family: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace; }
    code.mono {
      background: rgba(110, 118, 129, 0.10);
      padding: 1px 4px;
      border-radius: 3px;
    }
  `],
})
export class SchemaInsightsComponent implements OnInit {
  jobId = input.required<string>();

  private jobsSvc = inject(JobService);

  data = signal<SchemaInsights | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);

  ngOnInit(): void {
    this.jobsSvc.insights(this.jobId()).subscribe({
      next: r => { this.data.set(r); this.loading.set(false); },
      error: err => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load schema insights.',
        );
      },
    });
  }
}
