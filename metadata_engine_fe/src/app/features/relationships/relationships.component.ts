import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AnalysisService } from '../../core/services/analysis.service';
import { ActivatedRoute } from '@angular/router';

@Component({
  selector: 'app-relationships',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './relationships.component.html',
  styleUrls: ['./relationships.component.css']
})
export class RelationshipsComponent implements OnInit {
  activeFilter = 'all';
  expandedRow = -1;

  public rels_raw: any[] = [];
  public rels: any[] = [];
  public jobId: number | null = null;
  public totalPages: number = 0;
  public currentPage: number = 0;

  constructor(private analysisService: AnalysisService, private route: ActivatedRoute) {}

  ngOnInit() {
    this.route.queryParams.subscribe(params => {
      if (params['job']) {
        this.jobId = +params['job'];
        this.loadPage(0);
      }
    });
  }

  loadPage(pageIndex: number) {
    if (!this.jobId) return;
    this.analysisService.getRelationships(this.jobId, pageIndex).subscribe({
      next: (res) => {
        const data = res._embedded ? res._embedded.relationshipList : [];
        this.totalPages = res.page ? res.page.totalPages : 0;
        this.currentPage = res.page ? res.page.number : 0;

        this.rels_raw = data.map((dbRes: any) => ({
          a: dbRes.sourceTable?.tableName || dbRes.sourceTableName || 'Unknown',
          ca: dbRes.sourceColumn,
          b: dbRes.targetTable?.tableName || dbRes.targetTableName || 'Unknown',
          cb: dbRes.targetColumn,
          score: dbRes.score,
          card: dbRes.cardinality || 'N:A',
          domain: 'Core Data', 
          lib: 'Engine Orchestrator'
        }));
        this.setFilter(this.activeFilter);
      },
      error: (err) => console.error('Failed to load relationships from real database:', err)
    });
  }

  nextPage() {
    if (this.currentPage < this.totalPages - 1) this.loadPage(this.currentPage + 1);
  }
  
  prevPage() {
    if (this.currentPage > 0) this.loadPage(this.currentPage - 1);
  }

  getConfTag(r: any) {
    if (r.score >= 0.8) return { cls: 'tag-high', txt: 'High' };
    if (r.score >= 0.6) return { cls: 'tag-med', txt: 'Medium' };
    return { cls: 'tag-low', txt: 'Low' };
  }

  getScoreColor(score: number): string {
    if (score >= 0.8) return 'var(--color-text-success)';
    if (score >= 0.6) return 'var(--color-text-warning)';
    return 'var(--color-text-secondary)';
  }

  setFilter(f: string) {
    this.activeFilter = f;
    this.rels = this.rels_raw.filter(r => {
      if (f === 'high') return r.score >= 0.80;
      if (f === 'med')  return r.score >= 0.60 && r.score < 0.80;
      if (f === '1:N' || f === 'N:1' || f === '1:1') return r.card === f;
      return true;
    });
  }

  toggleRow(index: number) {
    this.expandedRow = this.expandedRow === index ? -1 : index;
  }
}
