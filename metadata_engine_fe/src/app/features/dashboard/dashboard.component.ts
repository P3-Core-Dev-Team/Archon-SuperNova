import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AnalysisService } from '../../core/services/analysis.service';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit {
  
  public relationships: any[] = [];
  public sensitive: any[] = [];
  public clusters: any[] = [];

  public logs = [
    { time: '09:14:02', stage: '[CRAWL]', cls: 'log-ok', txt: 'Schema extracted from real database' },
    { time: '09:14:18', stage: '[MATCH]', cls: 'log-ok', txt: 'Valentine matched structures' }
  ];

  constructor(private analysisService: AnalysisService) {}

  ngOnInit() {
    this.analysisService.getRelationships().subscribe({
      next: (res) => {
        const data = res._embedded ? res._embedded.relationshipList : [];
        this.relationships = data.map((dbRes: any) => ({
          a: dbRes.sourceTable?.tableName || dbRes.sourceTableName || 'Unknown',
          ca: dbRes.sourceColumn,
          b: dbRes.targetTable?.tableName || dbRes.targetTableName || 'Unknown',
          cb: dbRes.targetColumn,
          score: dbRes.score,
          card: dbRes.cardinality || 'N:A',
          color: dbRes.score >= 0.8 ? 'var(--accent2)' : 'var(--accent3)',
          tagClass: dbRes.score >= 0.8 ? 'high' : 'med'
        })).slice(0, 5); // top 5
      }
    });

    this.analysisService.getSensitiveColumns().subscribe({
      next: (res) => {
        const data = res._embedded ? res._embedded.sensitiveColumnList : [];
        this.sensitive = data.map((d: any) => ({
          name: `${d.table?.tableName || 'Table'}.${d.columnName}`,
          meta: `Presidio · ${d.category}`,
          cat: d.category,
          icon: d.category === 'FINANCIAL' ? '💰' : (d.category === 'PHI' ? '🏥' : '🔒'),
          iconCls: d.category === 'FINANCIAL' ? 'fin' : (d.category === 'PHI' ? 'hc' : 'pii'),
          tag: d.category
        })).slice(0, 5);
      }
    });

    this.analysisService.getDomains().subscribe({
      next: (res) => {
        const data = res._embedded ? res._embedded.domainGroupList : [];
        this.clusters = data.map((d: any, index: number) => ({
          name: d.domainName,
          color: index % 2 === 0 ? 'c-blue' : 'c-amber',
          tables: d.tables ? d.tables.map((t: any) => t.tableName) : []
        }));
      }
    });
  }
}
