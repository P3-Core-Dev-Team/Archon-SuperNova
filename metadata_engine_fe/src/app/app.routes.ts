import { Routes } from '@angular/router';
import { DataConnectionComponent } from './features/data-connection/data-connection.component';
import { JobAnalysisComponent } from './features/job-analysis/job-analysis.component';
import { ResultsComponent } from './features/results/results.component';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { RelationshipsComponent } from './features/relationships/relationships.component';
import { DomainsComponent } from './features/domains/domains.component';
import { SensitiveComponent } from './features/sensitive/sensitive.component';

export const routes: Routes = [
  { path: '', redirectTo: '/connection', pathMatch: 'full' },
  { path: 'dashboard', component: DashboardComponent },
  { path: 'connection', component: DataConnectionComponent },
  { path: 'job', component: JobAnalysisComponent },
  { path: 'results', component: ResultsComponent }, 
  { path: 'relationships', component: RelationshipsComponent },
  { path: 'domains', component: DomainsComponent },
  { path: 'sensitive', component: SensitiveComponent }
];
