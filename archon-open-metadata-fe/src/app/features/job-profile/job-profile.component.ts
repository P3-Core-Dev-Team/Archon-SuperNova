import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-job-profile',
  templateUrl: './job-profile.component.html',
  styleUrls: ['./job-profile.component.css']
})
export class JobProfileComponent implements OnInit {
  jobs: any[] = [];
  datasources: any[] = [];
  jobTemplates: any[] = [];
  showForm = false;
  newJob: any = {};
  private baseUrl = 'http://localhost:8080/api/v1';

  constructor(private http: HttpClient) {}

  ngOnInit() {
    this.fetchJobs();
    this.fetchDatasources();
    this.fetchJobTemplates();
    setInterval(() => this.fetchJobs(), 3000);
  }

  fetchJobs() {
    this.http.get<any>(`${this.baseUrl}/jobs`).subscribe(res => { this.jobs = res._embedded?.jobDtoList || []; });
  }
  fetchDatasources() {
    this.http.get<any>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });
  }
  fetchJobTemplates() {
    this.http.get<any>(`${this.baseUrl}/job-template-profiles`).subscribe(res => { this.jobTemplates = res._embedded?.jobTemplateProfileDtoList || []; });
  }
  createJob() {
    const payload = { jobName: this.newJob.jobName, datasourceProfile: { id: this.newJob.datasourceId }, jobTemplateProfile: { id: this.newJob.templateId }, status: 'Pending' };
    this.http.post(`${this.baseUrl}/jobs`, payload).subscribe(() => { this.fetchJobs(); this.showForm = false; });
  }
  deleteJob(id: string) {
    this.http.delete(`${this.baseUrl}/jobs/${id}`).subscribe(() => this.fetchJobs());
  }
}
