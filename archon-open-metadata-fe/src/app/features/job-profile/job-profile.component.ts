import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ConnectionProfile, Job, JobTemplate, ApiResponse } from '../../core/models/app.models';

@Component({
  selector: 'app-job-profile',
  templateUrl: './job-profile.component.html',
  styleUrls: ['./job-profile.component.css']
})
export class JobProfileComponent implements OnInit {
  jobs: Job[] = [];
  datasources: ConnectionProfile[] = [];
  jobTemplates: JobTemplate[] = [];
  showForm = false;
  newJob: Partial<Job> & { datasourceId?: string, templateId?: string } = {};
  selectedJob: Job | null = null;
  private baseUrl = 'http://localhost:8080/api/v1';

  constructor(private http: HttpClient) {}

  ngOnInit() {
    this.fetchJobs();
    this.fetchDatasources();
    this.fetchJobTemplates();
  }

  fetchJobs() {
    this.http.get<ApiResponse<Job>>(`${this.baseUrl}/jobs`).subscribe(res => { this.jobs = res._embedded?.jobDtoList || []; });
  }
  fetchDatasources() {
    this.http.get<ApiResponse<ConnectionProfile>>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });
  }
  fetchJobTemplates() {
    this.http.get<ApiResponse<JobTemplate>>(`${this.baseUrl}/job-template-profiles`).subscribe(res => { this.jobTemplates = res._embedded?.jobTemplateProfileDtoList || []; });
  }
  createJob() {
    const payload = { jobName: this.newJob.jobName, datasourceProfile: { id: this.newJob.datasourceId }, jobTemplateProfile: { id: this.newJob.templateId }, status: 'Pending' };
    this.http.post<Job>(`${this.baseUrl}/jobs`, payload).subscribe(() => { this.fetchJobs(); this.showForm = false; });
  }
  deleteJob(id: string | undefined) {
    if (!id) return;
    this.http.delete<void>(`${this.baseUrl}/jobs/${id}`).subscribe(() => this.fetchJobs());
  }
}
