import { Component, OnInit, OnDestroy, Output, EventEmitter, ElementRef, ViewChild } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Chart, registerables } from 'chart.js';
import { ConnectionProfile, Job, DashboardMetrics, AuditLog } from '../../core/models/app.models';

Chart.register(...registerables);

@Component({
  selector: 'app-dashboard',
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit, OnDestroy {
  datasources: ConnectionProfile[] = [];
  jobs: Job[] = [];
  tablesProfiled: number = 0;
  relationshipsCount: number = 0;
  sensitiveDataCount: number = 0;
  recentActivity: AuditLog[] = [];
  tableTypeDistribution: { [key: string]: number } = {};
  
  private pollInterval: ReturnType<typeof setInterval> | null = null;
  private chartInstance: Chart | null = null;
  
  @ViewChild('typeChart') chartCanvas!: ElementRef;

  constructor(private http: HttpClient, private router: Router) {}

  ngOnInit() {
    this.fetchDashboardData();
    this.pollInterval = setInterval(() => {
      this.fetchDashboardData();
    }, 5000); // realtime poll every 5s
  }

  ngOnDestroy() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
    }
    if (this.chartInstance) {
      this.chartInstance.destroy();
    }
  }

  fetchDashboardData() {
    this.http.get<DashboardMetrics>('http://localhost:8080/api/v1/dashboard/metrics').subscribe((res: DashboardMetrics) => {
      this.datasources = res.datasources || [];
      this.jobs = res.jobs || [];
      this.recentActivity = res.recentActivity || [];
      this.tablesProfiled = res.tablesProfiled || 0;
      this.relationshipsCount = res.relationshipsCount || 0;
      this.sensitiveDataCount = res.sensitiveDataCount || 0;
      this.tableTypeDistribution = res.tableTypeDistribution || {};
      
      this.updateChart();
    });
  }

  updateChart() {
    if (!this.chartCanvas) return;
    
    const labels = Object.keys(this.tableTypeDistribution);
    const data = Object.values(this.tableTypeDistribution);
    
    if (this.chartInstance) {
      this.chartInstance.data.labels = labels;
      this.chartInstance.data.datasets[0].data = data as number[];
      this.chartInstance.update();
      return;
    }

    const ctx = this.chartCanvas.nativeElement.getContext('2d');
    this.chartInstance = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: data as number[],
          backgroundColor: ['#2563eb', '#0d9488', '#8b5cf6', '#059669'],
          borderWidth: 0,
          hoverOffset: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '75%',
        plugins: {
          legend: {
            position: 'right',
            labels: {
              usePointStyle: true,
              boxWidth: 8,
              font: { size: 12, family: 'Inter' },
              color: '#64748b'
            }
          }
        }
      }
    });
  }

  viewJob(id: string) {
    this.router.navigate(['/job-profile']);
  }
}
