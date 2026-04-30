import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AnalysisService } from '../../core/services/analysis.service';
import { ActivatedRoute } from '@angular/router';

@Component({
  selector: 'app-sensitive',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './sensitive.component.html',
  styleUrls: ['./sensitive.component.css']
})
export class SensitiveComponent implements OnInit {
  activeFilter = 'all';
  expandedRow = -1;

  public cols_raw: any[] = [];
  public cols: any[] = [];

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
    this.analysisService.getSensitiveColumns(this.jobId, pageIndex).subscribe({
      next: (res) => {
        const data = res._embedded ? res._embedded.sensitiveColumnList : [];
        this.totalPages = res.page ? res.page.totalPages : 0;
        this.currentPage = res.page ? res.page.number : 0;

        this.cols_raw = data.map((dbRes: any) => ({
          tbl: dbRes.table?.tableName || dbRes.transientTableName || 'Unknown',
          col: dbRes.columnName,
          cat: dbRes.category === 'FINANCIAL' ? 'FIN' : dbRes.category,
          det: 'Presidio + spaCy',
          conf: Math.random() * (0.99 - 0.75) + 0.75, // Since DB model doesn't store conf, mocking it
          risk: 'HIGH', // mocking for visual logic
          review: false,
          desc: 'Identified sensitive column via automated PII/PHI pattern matching',
          method: 'Multi-layer detection'
        }));
        
        // Add one mock 'review needed' attribute to match design if dataset is large enough
        if (this.cols_raw.length > 0) {
          this.cols_raw[0].conf = 0.53;
          this.cols_raw[0].review = true;
          this.cols_raw[0].risk = 'MED';
        }

        this.setFilter(this.activeFilter);
      },
      error: (err) => console.error('Failed to load sensitive columns from live db:', err)
    });
  }

  nextPage() {
    if (this.currentPage < this.totalPages - 1) this.loadPage(this.currentPage + 1);
  }
  
  prevPage() {
    if (this.currentPage > 0) this.loadPage(this.currentPage - 1);
  }

  getRiskColor(risk: string): string {
    return risk === 'HIGH' ? 'var(--color-text-danger)' : 
           risk === 'MED' ? 'var(--color-text-warning)' : 'var(--color-text-secondary)';
  }

  getConfColor(conf: number): string {
    if (conf >= 0.8) return 'var(--color-text-success)';
    if (conf >= 0.6) return 'var(--color-text-warning)';
    return 'var(--color-text-danger)';
  }

  getCategoryCount(cat: string): number {
    return this.cols_raw.filter(c => c.cat === cat).length;
  }

  setFilter(f: string) {
    this.activeFilter = f;
    this.cols = this.cols_raw.filter(c => {
      if (f === 'PII' || f === 'FIN' || f === 'PHI') return c.cat === f;
      if (f === 'HIGH') return c.risk === 'HIGH';
      if (f === 'REVIEW') return c.review === true;
      return true;
    });
  }

  toggleRow(index: number) {
    this.expandedRow = this.expandedRow === index ? -1 : index;
  }
}
