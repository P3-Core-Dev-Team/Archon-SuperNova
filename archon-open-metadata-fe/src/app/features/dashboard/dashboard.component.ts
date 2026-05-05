import { Component, OnInit, Output, EventEmitter } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-dashboard',
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit {
  datasources: any[] = [];
  jobs: any[] = [];
  tablesProfiled: number = 0;
  relationshipsCount: number = 0;
  sensitiveDataCount: number = 0;
  recentActivity: any[] = [];
  private baseUrl = 'http://localhost:8080/api/v1';

  @Output() viewJobEvt = new EventEmitter<string>();

  constructor(private http: HttpClient) {}

  ngOnInit() {
    this.fetchDatasources();
    this.fetchJobs();
    this.fetchAudits();
    setInterval(() => { this.fetchJobs(); }, 3000);
  }

  fetchDatasources() {
    this.http.get<any>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });
  }
  fetchJobs() {
    this.http.get<any>(`${this.baseUrl}/jobs`).subscribe(res => { this.jobs = res._embedded?.jobDtoList || []; });
  }
  fetchAudits() {
    this.http.get<any[]>('http://localhost:8080/api/audits').subscribe(res => { this.recentActivity = res || []; });
  }

  viewJob(id: string) {
    this.viewJobEvt.emit(id);
  }
}
