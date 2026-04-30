import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class AnalysisService {

  private baseUrl = 'http://localhost:8080/api/analysis';

  constructor(private http: HttpClient) { }

  getRelationships(jobId: number = 1, page: number = 0, size: number = 20): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/relationships/${jobId}?page=${page}&size=${size}`);
  }

  getTables(jobId: number = 1, page: number = 0, size: number = 1000): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/tables/${jobId}?page=${page}&size=${size}`);
  }

  getDomains(jobId: number = 1, page: number = 0, size: number = 20): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/domains/${jobId}?page=${page}&size=${size}`);
  }

  getSensitiveColumns(jobId: number = 1, page: number = 0, size: number = 20): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/sensitive/${jobId}?page=${page}&size=${size}`);
  }

  getConnections(): Observable<any[]> {
    return this.http.get<any[]>(`${this.baseUrl}/connections`);
  }

  triggerAnalysis(schema: string): Observable<any> {
    return this.http.post<any>(`${this.baseUrl}/trigger?schema=${encodeURIComponent(schema)}`, {});
  }

  getJobs(): Observable<any[]> {
    return this.http.get<any[]>(`${this.baseUrl}/jobs`);
  }

  deleteJob(id: number): Observable<any> {
    return this.http.delete(`${this.baseUrl}/job/${id}`);
  }

  streamAnalysis(jobId: number): EventSource {
    return new EventSource(`${this.baseUrl}/job/${jobId}/stream`);
  }

  completeAnalysis(id: number, logs: string): Observable<any> {
    return this.http.put<any>(`${this.baseUrl}/job/${id}/complete`, logs);
  }
}
