import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class ApiService {

  constructor(private http: HttpClient) { }

  get baseUrl(): string {
    return localStorage.getItem('apiBaseUrl') || 'http://localhost:8080/api/v1';
  }

  setBaseUrl(url: string) {
    localStorage.setItem('apiBaseUrl', url);
  }

  get pythonApiUrl(): string {
    return localStorage.getItem('pythonApiUrl') || 'http://localhost:8000/api';
  }

  setPythonApiUrl(url: string) {
    localStorage.setItem('pythonApiUrl', url);
  }

  // Datasource
  createDatasource(data: any): Observable<any> {
    return this.http.post(`${this.baseUrl}/connectionprofiles`, data);
  }

  getDatasources(): Observable<any> {
    return this.http.get(`${this.baseUrl}/connectionprofiles`);
  }

  // Job Template
  createJobTemplate(data: any): Observable<any> {
    return this.http.post(`${this.baseUrl}/jobtemplateprofiles`, data);
  }

  getJobTemplates(): Observable<any> {
    return this.http.get(`${this.baseUrl}/jobtemplateprofiles`);
  }

  getJobRelationships(jobId: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/relationships/search`, { jobId });
  }

  getJobSensitiveData(jobId: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/columns/search`, { jobId, isSensitive: true });
  }

  getJobDataGroups(jobId: string): Observable<any> {
    // Assuming there is a DomainClusterController or similar, or we just mock for now
    return this.http.post(`${this.baseUrl}/datagroups/search`, { jobId });
  }
}
