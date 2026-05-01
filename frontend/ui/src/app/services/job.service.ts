import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError, shareReplay, switchMap } from 'rxjs/operators';
import {
  ClusterDetail,
  ClusterList,
  DataQualityResponse,
  Job,
  JobColumns,
  JobRequest,
  JobSummary,
  PiiTable,
  RelationshipGraph,
  SchemaInsights,
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

/**
 * Discriminated union for SSE frames pushed by GET /api/jobs/{id}/events.
 * The backend emits exactly five event names; this type pairs each name
 * with its payload shape so consumers get exhaustive narrowing.
 */
export type JobEvent =
  | { type: 'snapshot'; data: { status: Job; run_log: { entries: RunLogEntry[] }; log: string } }
  | { type: 'status'; data: Job }
  | { type: 'run_log'; data: { entries: RunLogEntry[] } }
  | { type: 'log'; data: { log: string } }
  | { type: 'done'; data: { status?: string; reason?: string } };

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

  /**
   * Subscribe to a Server-Sent Events stream for one job.  Replaces the
   * legacy polling pattern of GET /jobs/:id + /log + /run_log every
   * 2-3 s with a single push connection.  Backend cadence: 750 ms diff
   * loop; emits ``snapshot`` once on open, then ``status`` / ``run_log``
   * / ``log`` deltas as state changes, and ``done`` on terminal status.
   *
   * Each emission carries an event ``type`` and the parsed JSON payload.
   * Unsubscribing closes the underlying EventSource — call sites should
   * always store the Subscription and unsubscribe in ``ngOnDestroy``.
   *
   * Failure handling: EventSource auto-reconnects on transient drops
   * (the spec says so), so we don't surface those as errors.  Only a
   * 404 from the initial open propagates as Observable error.
   */
  events(jobId: string): Observable<JobEvent> {
    return new Observable<JobEvent>(subscriber => {
      const url = `${this.base}/jobs/${jobId}/events`;
      const es = new EventSource(url);

      const onMessage = (kind: JobEvent['type']) =>
        (ev: MessageEvent) => {
          try {
            subscriber.next({ type: kind, data: JSON.parse(ev.data) });
          } catch (err) {
            // Don't tear down the stream on a single malformed frame —
            // it's almost always a proxy buffering glitch.
            console.warn(`[JobService.events] malformed ${kind} frame`, err);
          }
        };

      es.addEventListener('snapshot', onMessage('snapshot'));
      es.addEventListener('status', onMessage('status'));
      es.addEventListener('run_log', onMessage('run_log'));
      es.addEventListener('log', onMessage('log'));
      es.addEventListener('done', (ev: MessageEvent) => {
        try {
          subscriber.next({ type: 'done', data: JSON.parse(ev.data) });
        } catch { /* ignore */ }
        subscriber.complete();
        es.close();
      });

      es.onerror = () => {
        // EventSource auto-reconnects (readyState 0 = CONNECTING) on its
        // own; only surface a hard error if it's permanently CLOSED.
        if (es.readyState === EventSource.CLOSED) {
          subscriber.error(new Error('EventSource closed'));
        }
      };

      // Teardown — fires on subscription.unsubscribe().
      return () => {
        es.close();
      };
    });
  }

  relationships(jobId: string, limit = 500): Observable<RelationshipGraph> {
    return this.http.get<RelationshipGraph>(
      `${this.base}/jobs/${jobId}/relationships?limit=${limit}`,
    );
  }

  pii(jobId: string): Observable<PiiTable> {
    return this.http.get<PiiTable>(`${this.base}/jobs/${jobId}/pii`);
  }

  /** Data-quality findings for a job — null density, duplicate PKs,
   * whitespace, empty strings, mixed case, low cardinality.  Returned
   * sorted by severity (HIGH > MEDIUM > LOW) then table, column. */
  dataQuality(jobId: string): Observable<DataQualityResponse> {
    return this.http.get<DataQualityResponse>(
      `${this.base}/jobs/${jobId}/data_quality`,
    );
  }

  /** Schema-design insights — known-schema fingerprint, temporal /
   * CDC pattern, surrogate-key prevalence, bridge tables, subtype
   * roots.  Returns ``null`` for any sub-section the pipeline
   * couldn't compute (e.g. no edges → no bridge detection). */
  insights(jobId: string): Observable<SchemaInsights> {
    return this.http.get<SchemaInsights>(
      `${this.base}/jobs/${jobId}/insights`,
    );
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
