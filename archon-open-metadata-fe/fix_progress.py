import os
import re

# 1. Update app.component.ts to poll for jobs
app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    content = f.read()

poll_logic = """  ngOnInit() {
    this.fetchSystemProperties();
    setInterval(() => {
      if (this.activeMenu === 'dashboard' || this.activeMenu === 'settings-job') {
        this.fetchJobs();
      }
    }, 3000);
  }"""

if 'setInterval' not in content:
    content = content.replace('ngOnInit() {\n    this.fetchSystemProperties();\n  }', poll_logic)
    with open(app_ts, 'w') as f:
        f.write(content)

# 2. Update dashboard.component.html to add View button
dashboard_html = 'src/app/features/dashboard/dashboard.component.html'
with open(dashboard_html, 'r') as f:
    d_html = f.read()

# Fix the duplicate jobs empty state in datasources table
d_html = re.sub(r'\s*<tr \*ngIf="jobs\.length === 0">\s*<td colspan="4" style="text-align:center;padding:40px 20px">\s*<div style="font-size:14px;font-weight:500;color:var\(--color-text-secondary\)">No recent jobs</div>\s*</td>\s*</tr>(?=\s*</table>\s*</div>\s*<div>\s*<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">\s*<div style="font-size: 14px; font-weight: 500; color: var\(--color-text-secondary\);">Table type distribution</div>)', '', d_html)

# Add View column to jobs table
d_html = d_html.replace('<th>Status</th>', '<th>Status</th><th></th>')
job_row_old = """          <td>{{ job.jobName || 'Unlabeled Job' }}</td>
          <td>{{ job.source || 'N/A' }}</td>
          <td>{{ job.stage || 'Pending' }}</td>
          <td>{{ job.status || 'N/A' }}</td>
        </tr>"""
job_row_new = """          <td>{{ job.jobName || 'Unlabeled Job' }}</td>
          <td>{{ job.datasourceProfile?.profileName || 'N/A' }}</td>
          <td>
            <div style="width: 80px; height: 6px; background: rgba(0,0,0,0.05); border-radius: 3px; overflow: hidden; margin-top: 6px;">
              <div style="height: 100%; border-radius: 3px; transition: width 1s ease-in-out;" 
                   [style.width]="job.status === 'Done' ? '100%' : (job.status === 'Running' ? '50%' : '10%')"
                   [style.background]="job.status === 'Done' ? '#10b981' : (job.status === 'Running' ? '#3b82f6' : '#9ca3af')"></div>
            </div>
          </td>
          <td><span class="badge" [ngClass]="{'b-green': job.status === 'Done', 'b-blue': job.status === 'Running', 'b-gray': job.status === 'Pending'}">{{ job.status || 'Pending' }}</span></td>
          <td style="text-align:right"><span style="color:#2563eb;cursor:pointer;font-size:12px;font-weight:500" (click)="viewJob(job.id)">View &rarr;</span></td>
        </tr>"""
d_html = d_html.replace(job_row_old, job_row_new)
with open(dashboard_html, 'w') as f:
    f.write(d_html)

# 3. Update dashboard.component.ts to emit viewJob
dashboard_ts = 'src/app/features/dashboard/dashboard.component.ts'
with open(dashboard_ts, 'r') as f:
    d_ts = f.read()

if 'viewJob(' not in d_ts:
    d_ts = d_ts.replace('import { Component, Input } from \'@angular/core\';', 'import { Component, Input, Output, EventEmitter } from \'@angular/core\';')
    d_ts = d_ts.replace('export class DashboardComponent {', 'export class DashboardComponent {\n  @Output() viewJobEvt = new EventEmitter<string>();\n\n  viewJob(id: string) {\n    this.viewJobEvt.emit(id);\n  }')
    with open(dashboard_ts, 'w') as f:
        f.write(d_ts)

# 4. Update job-profile.component.html to support expanding logs
job_html = 'src/app/features/job-profile/job-profile.component.html'
with open(job_html, 'r') as f:
    j_html = f.read()

j_row_old = """    <tr *ngFor="let job of jobs">
      <td>{{ job.jobName || 'Unnamed Job' }}</td>
      <td>{{ job.datasourceProfile?.profileName }}</td>
      <td>{{ job.jobTemplateProfile?.name }}</td>
      <td><span class="badge b-blue">{{ job.status || 'Pending' }}</span></td>
      <td>
        <div style="display:flex;gap:4px">
          <button class="tb-btn" style="padding:3px 8px;color:var(--color-text-danger);border-color:var(--color-text-danger)" (click)="deleteJob(job.id)">Delete</button>
        </div>
      </td>
    </tr>"""
j_row_new = """    <ng-container *ngFor="let job of jobs">
      <tr>
        <td>{{ job.jobName || 'Unnamed Job' }}</td>
        <td>{{ job.datasourceProfile?.profileName }}</td>
        <td>{{ job.jobTemplateProfile?.name }}</td>
        <td>
          <div style="display:flex; align-items:center; gap: 8px;">
            <span class="badge" [ngClass]="{'b-green': job.status === 'Done', 'b-blue': job.status === 'Running', 'b-gray': job.status === 'Pending'}">{{ job.status || 'Pending' }}</span>
            <div *ngIf="job.status === 'Running'" class="spinner" style="width:12px;height:12px;border:2px solid rgba(0,0,0,0.1);border-top:2px solid #3b82f6;border-radius:50%;animation:spin 1s linear infinite"></div>
          </div>
        </td>
        <td>
          <div style="display:flex;gap:4px">
            <button class="tb-btn" style="padding:3px 8px" (click)="job.expanded = !job.expanded">View Logs &rarr;</button>
            <button class="tb-btn" style="padding:3px 8px;color:var(--color-text-danger);border-color:var(--color-text-danger)" (click)="deleteJob(job.id)">Delete</button>
          </div>
        </td>
      </tr>
      <tr *ngIf="job.expanded">
        <td colspan="5" style="background: #0f172a; padding: 16px;">
          <div style="font-family: monospace; font-size: 12px; color: #10b981; white-space: pre-wrap; line-height: 1.5; min-height: 60px;">{{ job.auditlogs || '> Initializing job sequence...\n> Waiting for agent allocation...' }}</div>
        </td>
      </tr>
    </ng-container>"""
j_html = j_html.replace(j_row_old, j_row_new)
with open(job_html, 'w') as f:
    f.write(j_html)

# Add spinner CSS if not exists
css_path = 'src/styles.css'
with open(css_path, 'a') as f:
    f.write('\n@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }\n')

