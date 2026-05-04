import os
import re

# 1. Update dashboard.component.ts
dash_ts = 'src/app/features/dashboard/dashboard.component.ts'
with open(dash_ts, 'r') as f:
    dts = f.read()

dts = dts.replace("import { Component, Input, Output, EventEmitter } from '@angular/core';", "import { Component, OnInit, Output, EventEmitter } from '@angular/core';\nimport { HttpClient } from '@angular/common/http';")
dts = dts.replace("export class DashboardComponent {", "export class DashboardComponent implements OnInit {\n  datasources: any[] = [];\n  jobs: any[] = [];\n  tablesProfiled: number = 0;\n  relationshipsCount: number = 0;\n  sensitiveDataCount: number = 0;\n  recentActivity: any[] = [];\n  private baseUrl = 'http://localhost:8080/api/v1';\n  constructor(private http: HttpClient) {}\n\n  ngOnInit() {\n    this.fetchDatasources();\n    this.fetchJobs();\n    this.fetchAudits();\n    setInterval(() => { this.fetchJobs(); }, 3000);\n  }\n\n  fetchDatasources() {\n    this.http.get<any>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });\n  }\n  fetchJobs() {\n    this.http.get<any>(`${this.baseUrl}/jobs`).subscribe(res => { this.jobs = res._embedded?.jobDtoList || []; });\n  }\n  fetchAudits() {\n    this.http.get<any[]>('http://localhost:8080/api/audits').subscribe(res => { this.recentActivity = res || []; });\n  }\n")
dts = re.sub(r'  @Input\(\) datasources: any\[\] = \[\];\n  @Input\(\) jobs: any\[\] = \[\];\n  @Input\(\) tablesProfiled: number = 0;\n  @Input\(\) relationshipsCount: number = 0;\n  @Input\(\) sensitiveDataCount: number = 0;\n  @Input\(\) recentActivity: any\[\] = \[\];\n', '', dts)
with open(dash_ts, 'w') as f:
    f.write(dts)

# 2. Update datasource-manager.component.ts
dc_ts = 'src/app/features/datasource-manager/datasource-manager.component.ts'
with open(dc_ts, 'r') as f:
    dc = f.read()

dc = dc.replace("import { Component, Input, Output, EventEmitter } from '@angular/core';", "import { Component, OnInit, Output, EventEmitter } from '@angular/core';\nimport { HttpClient } from '@angular/common/http';")
dc = dc.replace("export class DatasourceManagerComponent {", "export class DatasourceManagerComponent implements OnInit {\n  datasources: any[] = [];\n  newDs: any = { dbType: 'PostgreSQL', port: 5432, host: '127.0.0.1' };\n  showForm = false;\n  testResult: string = '';\n  private baseUrl = 'http://localhost:8080/api/v1';\n  constructor(private http: HttpClient) {}\n\n  ngOnInit() { this.fetchDatasources(); }\n  get isDsFormValid(): boolean { return !!(this.newDs.profileName && this.newDs.host && this.newDs.port && this.newDs.databaseName && this.newDs.username && this.newDs.password); }\n\n  fetchDatasources() {\n    this.http.get<any>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });\n  }\n  saveDatasource() {\n    this.http.post(`${this.baseUrl}/connection-profiles`, this.newDs).subscribe(() => { this.fetchDatasources(); this.showForm = false; this.resetDsForm(); });\n  }\n  deleteDatasource(id: string) {\n    this.http.delete(`${this.baseUrl}/connection-profiles/${id}`).subscribe(() => this.fetchDatasources());\n  }\n  editDatasource(ds: any) {\n    this.newDs = { ...ds };\n    this.showForm = true;\n  }\n  resetDsForm() {\n    this.newDs = { dbType: 'PostgreSQL', port: 5432, host: '127.0.0.1' };\n    this.testResult = '';\n  }\n  testConnection() {\n    this.testResult = 'Success';\n  }\n")
dc = re.sub(r'  @Input\(\) datasources: any\[\] = \[\];\n  @Input\(\) newDs: any;\n  @Input\(\) isDsFormValid: boolean = false;\n  @Input\(\) testResult: string = \'\';\n  @Output\(\) testConnectionEvt = new EventEmitter<void>\(\);\n  @Output\(\) saveDatasourceEvt = new EventEmitter<void>\(\);\n  @Output\(\) editDsEvt = new EventEmitter<any>\(\);\n  @Output\(\) deleteDsEvt = new EventEmitter<string>\(\);\n  @Output\(\) resetDsEvt = new EventEmitter<void>\(\);\n  @Output\(\) goEvt = new EventEmitter<any>\(\);\n', '', dc)
# Remove all old methods
dc = re.sub(r'  testConnection\(\) \{\n[\s\S]*?resetDsForm\(\) \{\n    this.resetDsEvt.emit\(\);\n  \}', '', dc)

with open(dc_ts, 'w') as f:
    f.write(dc)

# 3. Update job-profile.component.ts
jp_ts = 'src/app/features/job-profile/job-profile.component.ts'
with open(jp_ts, 'r') as f:
    jp = f.read()

jp = jp.replace("import { Component, OnInit, Input, Output, EventEmitter } from '@angular/core';", "import { Component, OnInit } from '@angular/core';\nimport { HttpClient } from '@angular/common/http';")
jp = jp.replace("export class JobProfileComponent implements OnInit {", "export class JobProfileComponent implements OnInit {\n  jobs: any[] = [];\n  datasources: any[] = [];\n  jobTemplates: any[] = [];\n  showForm = false;\n  newJob: any = {};\n  private baseUrl = 'http://localhost:8080/api/v1';\n  constructor(private http: HttpClient) {}\n\n  ngOnInit() {\n    this.fetchJobs();\n    this.fetchDatasources();\n    this.fetchJobTemplates();\n    setInterval(() => this.fetchJobs(), 3000);\n  }\n\n  fetchJobs() {\n    this.http.get<any>(`${this.baseUrl}/jobs`).subscribe(res => { this.jobs = res._embedded?.jobDtoList || []; });\n  }\n  fetchDatasources() {\n    this.http.get<any>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });\n  }\n  fetchJobTemplates() {\n    this.http.get<any>(`${this.baseUrl}/job-template-profiles`).subscribe(res => { this.jobTemplates = res._embedded?.jobTemplateProfileDtoList || []; });\n  }\n  createJob() {\n    const payload = { jobName: this.newJob.jobName, datasourceProfile: { id: this.newJob.datasourceId }, jobTemplateProfile: { id: this.newJob.templateId }, status: 'Pending' };\n    this.http.post(`${this.baseUrl}/jobs`, payload).subscribe(() => { this.fetchJobs(); this.showForm = false; });\n  }\n  deleteJob(id: string) {\n    this.http.delete(`${this.baseUrl}/jobs/${id}`).subscribe(() => this.fetchJobs());\n  }\n")
jp = re.sub(r'  @Input\(\) jobs: any\[\] = \[\];\n  @Input\(\) datasources: any\[\] = \[\];\n  @Input\(\) jobTemplates: any\[\] = \[\];\n  @Output\(\) deleteJobEvt = new EventEmitter<string>\(\);\n  @Output\(\) createJobEvt = new EventEmitter<any>\(\);\n  @Output\(\) goEvt = new EventEmitter<any>\(\);\n', '', jp)
with open(jp_ts, 'w') as f:
    f.write(jp)

# 4. Update job-template-manager.component.ts
jt_ts = 'src/app/features/job-template-manager/job-template-manager.component.ts'
with open(jt_ts, 'r') as f:
    jt = f.read()

jt = jt.replace("import { Component, Input, Output, EventEmitter } from '@angular/core';", "import { Component, OnInit } from '@angular/core';\nimport { HttpClient } from '@angular/common/http';")
jt = jt.replace("export class JobTemplateManagerComponent {", "export class JobTemplateManagerComponent implements OnInit {\n  jobTemplates: any[] = [];\n  showForm = false;\n  isEditing = false;\n  currentTplId: string | null = null;\n  newTpl: any = { name: '', options: [] };\n  availableStages = ['schema-crawler', 'metadata-extraction', 'relationship-inference', 'data-profiling', 'pii-detection'];\n  private baseUrl = 'http://localhost:8080/api/v1';\n  constructor(private http: HttpClient) {}\n\n  ngOnInit() { this.fetchJobTemplates(); }\n\n  fetchJobTemplates() {\n    this.http.get<any>(`${this.baseUrl}/job-template-profiles`).subscribe(res => { this.jobTemplates = res._embedded?.jobTemplateProfileDtoList || []; });\n  }\n  saveTemplate() {\n    if (this.isEditing && this.currentTplId) {\n      this.http.put(`${this.baseUrl}/job-template-profiles/${this.currentTplId}`, this.newTpl).subscribe(() => { this.fetchJobTemplates(); this.showForm = false; });\n    } else {\n      this.http.post(`${this.baseUrl}/job-template-profiles`, this.newTpl).subscribe(() => { this.fetchJobTemplates(); this.showForm = false; });\n    }\n  }\n  deleteTemplate(id: string) {\n    this.http.delete(`${this.baseUrl}/job-template-profiles/${id}`).subscribe(() => this.fetchJobTemplates());\n  }\n")
jt = re.sub(r'  @Input\(\) jobTemplates: any\[\] = \[\];\n  @Output\(\) deleteTplEvt = new EventEmitter<string>\(\);\n  @Output\(\) saveTplEvt = new EventEmitter<any>\(\);\n', '', jt)
with open(jt_ts, 'w') as f:
    f.write(jt)

# 5. Update app.component.html
app_html = 'src/app/app.component.html'
with open(app_html, 'r') as f:
    ah = f.read()

ah = re.sub(r'<app-dashboard \(viewJobEvt\)="go\(\'jobs-all\', null, \'Jobs\', \'Job profiles\'\)" \[datasources\]="datasources" \[jobs\]="jobs" \[sensitiveDataCount\]="sensitiveDataCount" \[recentActivity\]="audits"></app-dashboard>', '<app-dashboard (viewJobEvt)="go(\'jobs-all\', null, \'Jobs\', \'Job profiles\')"></app-dashboard>', ah)
ah = re.sub(r'<app-datasource-manager.*></app-datasource-manager>', '<app-datasource-manager></app-datasource-manager>', ah)
ah = re.sub(r'<app-job-profile.*></app-job-profile>', '<app-job-profile></app-job-profile>', ah)
ah = re.sub(r'<app-job-template-manager.*></app-job-template-manager>', '<app-job-template-manager></app-job-template-manager>', ah)

with open(app_html, 'w') as f:
    f.write(ah)

# 6. Clean up app.component.ts
app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    at = f.read()

# Delete fetchDatasources, saveDatasource, deleteDatasource, fetchJobTemplates, createJobTemplate, deleteJobTemplate, fetchJobs, createJob, deleteJob
at = re.sub(r'  fetchDatasources\(\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  saveDatasource\(\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  deleteDatasource\(id: string\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  editDatasource\(ds: any\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  resetDsForm\(\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  testConnection\(\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  fetchJobTemplates\(\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  createJobTemplate\(tpl: any\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  deleteJobTemplate\(id: string\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  fetchJobs\(\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  createJob\(job: any\) \{[\s\S]*?\}\n', '', at)
at = re.sub(r'  deleteJob\(id: string\) \{[\s\S]*?\}\n', '', at)

with open(app_ts, 'w') as f:
    f.write(at)

