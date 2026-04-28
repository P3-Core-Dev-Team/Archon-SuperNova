import { Component, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ClusterDetailComponent } from './cluster-detail.component';

@Component({
  selector: 'app-cluster-detail-page',
  standalone: true,
  imports: [ClusterDetailComponent, RouterLink],
  template: `
    <a class="back" [routerLink]="['/jobs', jobId]">← Back to job</a>
    <app-cluster-detail [jobId]="jobId" [clusterId]="clusterId" />
  `,
  styles: [`
    .back {
      display: inline-block;
      color: #0969da;
      font-size: 13px;
      padding: 6px 0 12px;
      text-decoration: none;
    }
    .back:hover { text-decoration: underline; }
  `],
})
export class ClusterDetailPageComponent {
  private route = inject(ActivatedRoute);
  jobId     = this.route.snapshot.paramMap.get('id') ?? '';
  clusterId = Number(this.route.snapshot.paramMap.get('cluster_id') ?? 0);
}
