import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AnalysisService } from '../../core/services/analysis.service';
import { ActivatedRoute } from '@angular/router';

import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-domains',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './domains.component.html',
  styleUrls: ['./domains.component.css']
})
export class DomainsComponent implements OnInit {
  activeView = 'grid';
  expandedRow = -1;

  public maxTables = 1;
  public totalTables = 0;
  
  public clusters: any[] = [];
  
  // Design palette
  private colors = ['#378ADD', '#1D9E75', '#BA7517', '#8b5cf6', '#888780', '#E24B4A', '#0891b2'];

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
    this.analysisService.getDomains(this.jobId, pageIndex).subscribe({
      next: (res) => {
        const data = res._embedded ? res._embedded.domainGroupList : [];
        this.totalPages = res.page ? res.page.totalPages : 0;
        this.currentPage = res.page ? res.page.number : 0;

        this.clusters = data.map((d: any, index: number) => {
          const t = d.tables ? d.tables.map((tbl: any) => tbl.tableName) : [];
          return {
            name: d.domainName,
            color: this.colors[index % this.colors.length],
            tables: t,
            eps: 0.30, 
            prev: true, 
            sensTables: [], 
            desc: 'Auto-detected behavioral domain cluster derived from schema context',
            rels: Math.floor(t.length * 0.8),
            sensCount: 0
          };
        });

        this.maxTables = Math.max(...this.clusters.map(c => c.tables.length), 1);
        this.totalTables = this.clusters.reduce((s, c) => s + c.tables.length, 0);
      },
      error: (err) => console.error('Failed to load domain clusters:', err)
    });
  }

  nextPage() {
    if (this.currentPage < this.totalPages - 1) this.loadPage(this.currentPage + 1);
  }
  
  prevPage() {
    if (this.currentPage > 0) this.loadPage(this.currentPage - 1);
  }

  setView(v: string) {
    this.activeView = v;
  }

  toggleRow(index: number) {
    this.expandedRow = this.expandedRow === index ? -1 : index;
  }
}
