import { Routes } from '@angular/router';
import { DashboardComponent } from './components/dashboard/dashboard.component';
import { JobSubmitComponent } from './components/job-submit/job-submit.component';
import { JobListComponent } from './components/job-list/job-list.component';
import { JobDetailComponent } from './components/job-detail/job-detail.component';
import { ErdCardPageComponent } from './components/erd-card/erd-card-page.component';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    component: DashboardComponent,
  },
  {
    path: 'jobs',
    component: JobListComponent,
  },
  {
    path: 'submit',
    component: JobSubmitComponent,
  },
  {
    path: 'jobs/:id/erd',
    component: ErdCardPageComponent,
  },
  {
    path: 'jobs/:id/clusters/:cluster_id',
    loadComponent: () =>
      import('./components/cluster-detail/cluster-detail-page.component').then(
        m => m.ClusterDetailPageComponent,
      ),
  },
  {
    // Legacy deep-link: redirects to /jobs/:id?tab=relationships&table=...&view=...
    // The TABLE / MAP per-table views now live as embedded modes inside
    // the Relationships tab; see commit consolidating the two-mode flow.
    path: 'jobs/:id/tables/:table_name',
    loadComponent: () =>
      import('./components/legacy-redirect/legacy-table-redirect.component').then(
        m => m.LegacyTableRedirectComponent,
      ),
  },
  {
    path: 'jobs/:id',
    component: JobDetailComponent,
  },
  { path: '**', redirectTo: '' },
];
