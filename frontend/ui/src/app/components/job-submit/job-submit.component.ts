import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { JobService } from '../../services/job.service';

@Component({
  selector: 'app-job-submit',
  standalone: true,
  imports: [ReactiveFormsModule],
  template: `
    <h2>Submit a new discovery run</h2>
    <p class="muted">Fill in the source database connection details. The job runs in the background — this form will clear, the new job appears in the Jobs list.</p>

    <form [formGroup]="form" (ngSubmit)="onSubmit()" class="card">
      <div class="row">
        <div>
          <label>Label</label>
          <input formControlName="label" placeholder="e.g. AdventureWorks" autocomplete="off" />
        </div>
        <div>
          <label>Schema</label>
          <input formControlName="schema" placeholder="e.g. public" autocomplete="off" />
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
          <label>Database</label>
          <input formControlName="database" placeholder="test" autocomplete="off" />
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

      <div class="actions">
        <button type="submit" class="primary"
                [disabled]="form.invalid || submitting()">
          {{ submitting() ? 'Submitting…' : 'Run discovery' }}
        </button>
        <button type="button" (click)="reset()">Reset</button>
      </div>
    </form>
  `,
  styles: [`
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
  `],
})
export class JobSubmitComponent {
  private fb = inject(FormBuilder);
  private jobs = inject(JobService);
  private router = inject(Router);

  submitting = signal(false);
  error = signal<string | null>(null);

  form = this.fb.nonNullable.group({
    label: ['', Validators.required],
    schema: ['', Validators.required],
    host: ['localhost', Validators.required],
    port: [5432, [Validators.required, Validators.min(1)]],
    database: ['', Validators.required],
    user: ['', Validators.required],
    password: ['', Validators.required],
  });

  onSubmit(): void {
    if (this.form.invalid) {
      // Surface validation issues even if the user mashed Enter without focusing.
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    const payload = this.form.getRawValue();
    this.jobs.submit(payload).subscribe({
      next: job => {
        this.submitting.set(false);
        this.form.reset({
          label: '', schema: '',
          host: 'localhost', port: 5432,
          database: '', user: '', password: '',
        });
        // Spec: navigate straight to the new job's detail page.
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
      label: '', schema: '',
      host: 'localhost', port: 5432,
      database: '', user: '', password: '',
    });
    this.error.set(null);
  }
}
