import { Component, OnInit } from '@angular/core';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-job-profile',
  templateUrl: './job-profile.component.html',
  styleUrls: ['./job-profile.component.css']
})
export class JobProfileComponent implements OnInit {
  viewMode: 'grid' | 'graph' = 'grid';
  activeFilter = 'all';
  searchQuery = '';
  sortKey = 'score';
  sortDir = -1;
  openRowIndex = -1;

  relationships: any[] = [];
  jobId: string = 'test-job'; // default or from route params

  constructor(private api: ApiService) {}

  setViewMode(mode: 'grid' | 'graph') {
    this.viewMode = mode;
  }

  ngOnInit() {
    this.api.getJobRelationships(this.jobId).subscribe({
      next: (res: any) => {
        if (res && res.edges) {
          // Map backend edges to UI expected format
          this.relationships = res.edges.map((e: any) => ({
            a: e.source || 'Unknown',
            ca: e.source_column || 'id',
            b: e.target || 'Unknown',
            cb: e.target_column || 'id',
            score: e.score || 0.85,
            card: e.cardinality || '1:N',
            domain: e.domain || 'Data',
            lib: e.library || 'Pipeline'
          }));
        }
      },
      error: (err) => {
        console.error('Failed to load relationships from API:', err);
      }
    });
  }

  get filteredRels() {
    return this.relationships.filter(r => {
      const txt = (r.a + r.ca + r.b + r.cb).toLowerCase();
      if (this.searchQuery && !txt.includes(this.searchQuery.toLowerCase())) return false;
      if (this.activeFilter === 'high') return r.score >= 0.80;
      if (this.activeFilter === 'med') return r.score >= 0.60 && r.score < 0.80;
      if (this.activeFilter === '1:N') return r.card === '1:N';
      if (this.activeFilter === 'N:1') return r.card === 'N:1';
      if (this.activeFilter === '1:1') return r.card === '1:1';
      return true;
    }).sort((a, b) => {
      if (this.sortKey === 'score') return this.sortDir * (a.score - b.score);
      if (this.sortKey === 'pair') return this.sortDir * (a.a + a.b).localeCompare(b.a + b.b);
      if (this.sortKey === 'card') return this.sortDir * a.card.localeCompare(b.card);
      return 0;
    });
  }

  setFilter(f: string) {
    this.activeFilter = f;
  }

  sortBy(k: string) {
    if (this.sortKey === k) {
      this.sortDir *= -1;
    } else {
      this.sortKey = k;
      this.sortDir = -1;
    }
  }

  toggleDetail(index: number) {
    this.openRowIndex = this.openRowIndex === index ? -1 : index;
  }
}
