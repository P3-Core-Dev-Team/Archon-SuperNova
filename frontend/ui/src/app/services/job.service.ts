import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError, shareReplay, switchMap } from 'rxjs/operators';
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
  SourceDbType,
} from '../models/job.model';

// Re-export so components can import SourceDbType from this service barrel.
export type { SourceDbType } from '../models/job.model';

export interface RunLogEntry {
  phase: string;
  scope_type: string;
  scope_id: number;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  error_message: string | null;
  sub_total?: number;
  sub_failed?: number;
}

export interface ConnectionTestRequest {
  db_type: SourceDbType;
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
  schema: string;
}

export interface ConnectionTestResult {
  ok: boolean;
  db_type?: SourceDbType;
  host: string;
  port: number;
  database: string;
  schema: string;
  server_version?: string;
  current_user?: string;
  table_count?: number;
  error?: string;
  error_kind?: 'connect' | 'schema_missing' | 'probe';
}

@Injectable({ providedIn: 'root' })
export class JobService {
  private http = inject(HttpClient);
  private base = '/api';

  // Cross-component event bus for "table selected in graph" events.
  // The relationship-graph component (B1) calls .set(tableName) on node click;
  // the table-detail panel reacts to changes. The dropdown in the detail panel
  // also writes here, so the panel works even if B1 hasn't wired the click yet.
  selectedTable = signal<string | null>(null);

  // Fetch the API token from /api/auth/token exactly once at construction.
  // shareReplay(1) means any number of callers subscribing BEFORE or AFTER
  // the request completes will each receive the resolved value, so Submit /
  // Test-connection clicks that arrive while the token is still in-flight are
  // transparently queued and retried once the token is known rather than
  // sending an empty-token POST that would 401.
  private readonly tokenReady$ = this.http
    .get<{ token: string }>(`${this.base}/auth/token`)
    .pipe(
      catchError(err => {
        console.error('[JobService] Failed to fetch auth token:', err);
        return of({ token: '' });
      }),
      shareReplay(1),
    );

  private withAuth<T>(req$: (headers: HttpHeaders) => Observable<T>): Observable<T> {
    return this.tokenReady$.pipe(
      switchMap(r => req$(new HttpHeaders({ 'X-Discovery-Token': r.token }))),
    );
  }

  submit(req: JobRequest): Observable<Job> {
    return this.withAuth(headers =>
      this.http.post<Job>(`${this.base}/jobs`, req, { headers }),
    );
  }

  testConnection(req: ConnectionTestRequest): Observable<ConnectionTestResult> {
    return this.withAuth(headers =>
      this.http.post<ConnectionTestResult>(`${this.base}/test_connection`, req, { headers }),
    );
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

  runLog(jobId: string): Observable<{ entries: RunLogEntry[] }> {
    return this.http.get<{ entries: RunLogEntry[] }>(
      `${this.base}/jobs/${jobId}/run_log`,
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
