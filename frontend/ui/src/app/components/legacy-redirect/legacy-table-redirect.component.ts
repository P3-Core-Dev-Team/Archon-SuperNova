import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

/**
 * Backward-compat redirector for the old /jobs/:id/tables/:table_name route.
 *
 * The per-table TABLE / MAP views moved into the Relationships tab on the
 * job-detail page (commit a17ba44 + follow-ups).  Old deep links still
 * land here; we translate them to the consolidated URL:
 *
 *   /jobs/:id/tables/:name           → /jobs/:id?tab=relationships&table=:name&view=table
 *   /jobs/:id/tables/:name?view=map  → /jobs/:id?tab=relationships&table=:name&view=map
 *
 * No UI is rendered — Router.navigate fires once, then the user sees the
 * job-detail page in the right state.
 */
@Component({
  selector: 'app-legacy-table-redirect',
  standalone: true,
  template: '<p class="muted">Redirecting…</p>',
})
export class LegacyTableRedirectComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    const tbl = this.route.snapshot.paramMap.get('table_name');
    const view = this.route.snapshot.queryParamMap.get('view');
    const finalView = view === 'map' ? 'map' : 'table';
    if (id && tbl) {
      this.router.navigate(['/jobs', id], {
        queryParams: { tab: 'relationships', table: tbl, view: finalView },
        replaceUrl: true,
      });
    } else if (id) {
      this.router.navigate(['/jobs', id], { replaceUrl: true });
    } else {
      this.router.navigate(['/jobs'], { replaceUrl: true });
    }
  }
}
