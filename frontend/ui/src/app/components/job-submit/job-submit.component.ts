import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { JobService, ConnectionTestResult, SourceDbType } from '../../services/job.service';

const DEFAULT_PORTS: Record<SourceDbType, number> = {
  postgres:  5432,
  mysql:     3306,
  sqlserver: 1433,
  oracle:    1521,
};

@Component({
  selector: 'app-job-submit',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  template: `
    <h2>Submit a new discovery run</h2>
    <p class="muted">Fill in the source database connection details, then <strong>Test connection</strong>. The Run button enables once the test succeeds.</p>

    <form [formGroup]="form" (ngSubmit)="onSubmit()" class="card">
      <div class="row">
        <div>
          <label>Label</label>
          <input formControlName="label" placeholder="e.g. AdventureWorks" autocomplete="off" />
        </div>
        <div>
          <label for="db_type">DB type</label>
          <select id="db_type" formControlName="db_type" (change)="onDbTypeChange()">
            <option value="postgres">PostgreSQL</option>
            <option value="mysql">MySQL</option>
            <option value="sqlserver">SQL Server</option>
            <option value="oracle">Oracle</option>
          </select>
        </div>
        <div>
          <label>{{ schemaLabel() }}</label>
          <input formControlName="schema" [placeholder]="schemaPlaceholder()" autocomplete="off" />
        </div>
      </div>

      <div class="row">
        <div class="grow">
          <label>Host</label>
          <input formControlName="host" placeholder="localhost" autocomplete="off" />
        </div>
        <div>
          <label>Port</label>
          <input formControlName="port" type="number" />
        </div>
        <div class="grow">
          <label>{{ databaseLabel() }}</label>
          <input formControlName="database" [placeholder]="databasePlaceholder()" autocomplete="off" />
        </div>
      </div>

      <div class="row">
        <div class="grow">
          <label>User</label>
          <input formControlName="user" autocomplete="off" />
        </div>
        <div class="grow">
          <label>Password</label>
          <input formControlName="password" type="password" autocomplete="off" />
        </div>
      </div>

      @if (error()) {
        <div class="error">{{ error() }}</div>
      }

      @if (testResult(); as t) {
        @if (t.ok) {
          <div class="success">
            ✓ Connected — server: {{ t.server_version }} · user: <code>{{ t.current_user }}</code>
            · schema <code>{{ t.schema }}</code> has <strong>{{ t.table_count }}</strong> tables.
          </div>
        } @else {
          <div class="error">
            ✗ Connection failed: {{ t.error }}
          </div>
        }
      }

      <div class="actions">
        <button type="button" class="secondary"
                [disabled]="!canTest() || testing()"
                (click)="onTest()">
          {{ testing() ? 'Testing…' : 'Test connection' }}
        </button>
        <button type="submit" class="primary"
                [disabled]="!canRun()"
                [title]="canRun() ? '' : 'Test the connection successfully before running'">
          {{ submitting() ? 'Submitting…' : 'Run discovery' }}
        </button>
        <button type="button" (click)="reset()">Reset</button>
      </div>
    </form>
  `,
  styles: [`
    :host { display: block; max-width: 1100px; margin: 0 auto; }
    h2 { margin: 0 0 6px; }
    .row {
      display: flex;
      gap: 14px;
      margin-bottom: 14px;
    }
    .row > div { flex: 0 0 auto; min-width: 140px; }
    .row > div.grow { flex: 1 1 0; }
    .row input { width: 100%; }
    .actions {
      display: flex;
      gap: 8px;
      margin-top: 8px;
    }
    .error {
      background: #3a0d0d;
      border: 1px solid #f85149;
      color: #ffabab;
      padding: 8px 12px;
      border-radius: 6px;
      margin-bottom: 12px;
    }
    .success {
      background: #0e2e1d;
      border: 1px solid #2ea043;
      color: #aaf0c1;
      padding: 8px 12px;
      border-radius: 6px;
      margin-bottom: 12px;
    }
    button.secondary {
      background: #21262d;
      color: #c9d1d9;
      border: 1px solid #30363d;
    }
    button.secondary:hover:not(:disabled) {
      background: #30363d;
    }
    button.primary:disabled, button.secondary:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
  `],
})
export class JobSubmitComponent {
  private fb = inject(FormBuilder);
  private jobs = inject(JobService);
  private router = inject(Router);

  submitting = signal(false);
  testing = signal(false);
  error = signal<string | null>(null);
  testResult = signal<ConnectionTestResult | null>(null);

  form = this.fb.nonNullable.group({
    label: ['', Validators.required],
    db_type: ['postgres' as SourceDbType, Validators.required],
    schema: ['', Validators.required],
    host: ['localhost', Validators.required],
    port: [5432, [Validators.required, Validators.min(1)]],
    database: ['', Validators.required],
    user: ['', Validators.required],
    password: ['', Validators.required],
  });

  // Connection-bearing fields. Any edit invalidates a prior test result so
  // the user has to re-test before Run is re-enabled.
  private readonly connFields = [
    'db_type', 'host', 'port', 'database', 'user', 'password', 'schema',
  ] as const;

  // Labels and placeholders shift slightly per DB type (Oracle uses "service
  // name" instead of "database", "schema" maps to the owner/user name, etc.).
  databaseLabel(): string {
    const t = this.form.controls.db_type.value;
    return t === 'oracle' ? 'Service name' : 'Database';
  }
  databasePlaceholder(): string {
    const t = this.form.controls.db_type.value;
    return t === 'oracle' ? 'ORCLPDB1' : 'test';
  }
  schemaLabel(): string {
    return this.form.controls.db_type.value === 'oracle' ? 'Schema (owner)' : 'Schema';
  }
  schemaPlaceholder(): string {
    const t = this.form.controls.db_type.value;
    if (t === 'mysql') return '(same as database)';
    if (t === 'oracle') return 'HR';
    if (t === 'sqlserver') return 'dbo';
    return 'public';
  }

  onDbTypeChange(): void {
    const t = this.form.controls.db_type.value as SourceDbType;
    // Update port if it currently holds a *different* dialect's default.
    const current = this.form.controls.port.value;
    const known = Object.values(DEFAULT_PORTS) as number[];
    if (known.includes(current as number) || !current) {
      this.form.controls.port.setValue(DEFAULT_PORTS[t]);
    }
  }

  // Snapshot of the connection-field values that produced the last successful
  // test. If the user edits any of those fields, the test result is invalidated.
  private testedFingerprint: string | null = null;

  constructor() {
    this.form.valueChanges.subscribe(() => {
      if (this.testResult() && this.connFingerprint() !== this.testedFingerprint) {
        this.testResult.set(null);
        this.testedFingerprint = null;
      }
    });
  }

  private connFingerprint(): string {
    const v = this.form.getRawValue();
    return [v.db_type, v.host, v.port, v.database, v.user, v.password, v.schema].join('|');
  }

  // Test enabled iff every connection field is filled and valid.
  canTest(): boolean {
    return this.connFields.every(n => {
      const c = this.form.controls[n];
      return c.valid && c.value !== null && c.value !== '';
    });
  }

  // Run enabled iff (a) the form is fully valid (incl. label) AND (b) the
  // most recent connection test succeeded for the current field values.
  canRun(): boolean {
    return this.form.valid && !this.submitting() &&
           !!this.testResult() && this.testResult()!.ok;
  }

  onTest(): void {
    if (!this.canTest()) return;
    this.testing.set(true);
    this.error.set(null);
    this.testResult.set(null);
    const v = this.form.getRawValue();
    this.jobs.testConnection({
      db_type: v.db_type,
      host: v.host, port: v.port,
      database: v.database, user: v.user, password: v.password,
      schema: v.schema,
    }).subscribe({
      next: r => {
        this.testing.set(false);
        this.testResult.set(r);
        this.testedFingerprint = this.connFingerprint();
      },
      error: err => {
        this.testing.set(false);
        this.testResult.set({
          ok: false,
          host: v.host, port: v.port, database: v.database, schema: v.schema,
          error: err?.error?.detail ?? err?.message ?? 'request failed',
        });
      },
    });
  }

  onSubmit(): void {
    if (!this.canRun()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    const payload = this.form.getRawValue();
    this.jobs.submit(payload).subscribe({
      next: job => {
        this.submitting.set(false);
        this.reset();
        this.router.navigate(['/jobs', job.job_id]);
      },
      error: err => {
        this.submitting.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Submission failed.',
        );
      },
    });
  }

  reset(): void {
    this.form.reset({
      label: '', db_type: 'postgres', schema: '',
      host: 'localhost', port: 5432,
      database: '', user: '', password: '',
    });
    this.error.set(null);
    this.testResult.set(null);
  }
}
