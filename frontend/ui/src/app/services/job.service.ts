import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  ClusterDetail,
  ClusterList,
  Job,
  JobColumns,
  JobRequest,
  JobSummary,
  PiiTable,
  RelationshipGraph,
  SchemaList,
} from '../models/job.model';

// MVP-only shared secret for POST /api/jobs. This is NOT a real auth secret --
// the backend uses it solely to keep random web-origin pages from triggering
// subprocess pipeline runs. Shipping it as a constant in client code is fine.
const DISCOVERY_API_TOKEN = 'dev-secret';

@Injectable({ providedIn: 'root' })
export class JobService {
  private http = inject(HttpClient);
  private base = '/api';

  // Cross-component event bus for "table selected in graph" events.
  // The relationship-graph component (B1) calls .set(tableName) on node click;
  // the table-detail panel reacts to changes. The dropdown in the detail panel
  // also writes here, so the panel works even if B1 hasn't wired the click yet.
  selectedTable = signal<string | null>(null);

  submit(req: JobRequest): Observable<Job> {
    const headers = new HttpHeaders({
      'X-Discovery-Token': DISCOVERY_API_TOKEN,
    });
    return this.http.post<Job>(`${this.base}/jobs`, req, { headers });
  }

  list(): Observable<Job[]> {
    return this.http.get<Job[]>(`${this.base}/jobs`);
  }

  get(jobId: string): Observable<Job> {
    return this.http.get<Job>(`${this.base}/jobs/${jobId}`);
  }

  log(jobId: string, tail = 200): Observable<{ log: string }> {
    return this.http.get<{ log: string }>(
      `${this.base}/jobs/${jobId}/log?tail=${tail}`,
    );
  }

  relationships(jobId: string, limit = 500): Observable<RelationshipGraph> {
    return this.http.get<RelationshipGraph>(
      `${this.base}/jobs/${jobId}/relationships?limit=${limit}`,
    );
  }

  pii(jobId: string): Observable<PiiTable> {
    return this.http.get<PiiTable>(`${this.base}/jobs/${jobId}/pii`);
  }

  summary(jobId: string): Observable<JobSummary> {
    return this.http.get<JobSummary>(`${this.base}/jobs/${jobId}/summary`);
  }

  schemas(): Observable<SchemaList> {
    return this.http.get<SchemaList>(`${this.base}/schemas`);
  }

  // Column-level inventory for the ERD card view (B2). Returns 404 until the
  // API process picks up the new endpoint -- callers MUST handle that case
  // gracefully (fall back to inferring columns from edge labels).
  columns(jobId: string): Observable<JobColumns> {
    return this.http.get<JobColumns>(`${this.base}/jobs/${jobId}/columns`);
  }

  // Cluster-engine sprint (CL-3): cluster list and single-cluster detail.
  clusters(jobId: string): Observable<ClusterList> {
    return this.http.get<ClusterList>(`${this.base}/jobs/${jobId}/clusters`);
  }

  clusterDetail(jobId: string, clusterId: number): Observable<ClusterDetail> {
    return this.http.get<ClusterDetail>(
      `${this.base}/jobs/${jobId}/clusters/${clusterId}`,
    );
  }
}
