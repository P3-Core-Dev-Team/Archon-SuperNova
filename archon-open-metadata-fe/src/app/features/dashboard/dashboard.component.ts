import { ConnectionProfile, Job, DatasourceForm, User, Group } from '../../core/models/app.models';
import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-dashboard',
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent {
  @Input() datasources: any[] = [];
  @Input() jobs: any[] = [];

  mockDatasources = [
    { profileName: 'prod-postgres-01', dbType: 'PostgreSQL', tables: 412, lastCrawl: '2h ago', status: 'Active' },
    { profileName: 'warehouse-bq', dbType: 'BigQuery', tables: 840, lastCrawl: '6h ago', status: 'Active' },
    { profileName: 'legacy-mysql', dbType: 'MySQL', tables: 197, lastCrawl: '3d ago', status: 'Stale' },
    { profileName: 'snowflake-dwh', dbType: 'Snowflake', tables: 398, lastCrawl: '1h ago', status: 'Active' }
  ];

  mockJobs = [
    { jobId: 'JOB-0042', source: 'prod-postgres-01', stage: '6/6', status: 'Done' },
    { jobId: 'JOB-0043', source: 'warehouse-bq', stage: '3/6', status: 'Running' },
    { jobId: 'JOB-0044', source: 'snowflake-dwh', stage: '4/6', status: 'Running' },
    { jobId: 'JOB-0041', source: 'legacy-mysql', stage: '2/6', status: 'Failed' }
  ];
}
