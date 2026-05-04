import os

jtm_path = 'src/app/features/job-template-manager/job-template-manager.component.ts'
jtm_content = """import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-job-template-manager',
  templateUrl: './job-template-manager.component.html',
  styleUrls: ['./job-template-manager.component.css']
})
export class JobTemplateManagerComponent implements OnInit {
  jobTemplates: any[] = [];
  showForm = false;
  isEditing = false;
  currentTplId: string | null = null;
  newTpl: any = { name: '', options: [] };
  availableStages = ['schema-crawler', 'metadata-extraction', 'relationship-inference', 'data-profiling', 'pii-detection'];
  private baseUrl = 'http://localhost:8080/api/v1';

  constructor(private http: HttpClient) {}

  ngOnInit() { this.fetchJobTemplates(); }

  fetchJobTemplates() {
    this.http.get<any>(`${this.baseUrl}/job-template-profiles`).subscribe(res => { this.jobTemplates = res._embedded?.jobTemplateProfileDtoList || []; });
  }

  saveTemplate() {
    if (this.isEditing && this.currentTplId) {
      this.http.put(`${this.baseUrl}/job-template-profiles/${this.currentTplId}`, this.newTpl).subscribe(() => { this.fetchJobTemplates(); this.showForm = false; });
    } else {
      this.http.post(`${this.baseUrl}/job-template-profiles`, this.newTpl).subscribe(() => { this.fetchJobTemplates(); this.showForm = false; });
    }
  }

  deleteTemplate(id: string) {
    this.http.delete(`${this.baseUrl}/job-template-profiles/${id}`).subscribe(() => this.fetchJobTemplates());
  }

  editTemplate(tpl: any) {
    this.isEditing = true;
    this.currentTplId = tpl.id;
    this.newTpl = { ...tpl, options: tpl.options ? [...tpl.options] : [] };
    this.showForm = true;
  }

  addStage() {
    this.newTpl.options.push({ operationName: 'metadata-extraction', minValue: 0, maxValue: 100 });
  }

  removeStage(idx: number) {
    this.newTpl.options.splice(idx, 1);
  }
}
"""
with open(jtm_path, 'w') as f:
    f.write(jtm_content)

dash_path = 'src/app/features/dashboard/dashboard.component.ts'
dash_content = """import { Component, OnInit, Output, EventEmitter } from '@angular/core';
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
"""
with open(dash_path, 'w') as f:
    f.write(dash_content)

