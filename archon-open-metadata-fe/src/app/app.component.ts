import { Component, OnInit } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { ConnectionProfile, Job, DatasourceForm, ApiResponse, User, Group } from './core/models/app.models';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: []
})
export class AppComponent implements OnInit {
  private baseUrl = 'http://localhost:8080/api';
  datasources: ConnectionProfile[] = [];
  jobs: Job[] = [];
  sidebarCollapsed: boolean = false;
  sensitiveDataCount: number = 0;
  audits: any[] = [];
  dataCleanupDays: number = 30;
  jobTemplates: any[] = [];
  users: User[] = [];
  groups: Group[] = [];
  newDs: DatasourceForm = {
    dbType: 'PostgreSQL',
    port: 5432,
    host: '127.0.0.1',
    username: 'adsuser',
    password: 'AdS@3421',

  };

  get isDsFormValid(): boolean {
    return !!(this.newDs.profileName && this.newDs.host && this.newDs.port && this.newDs.databaseName && this.newDs.username && this.newDs.password);
  }
  testResult: string = '';
  isDarkTheme: boolean = false;
  toasts: { id: number, message: string, type: string }[] = [];
  private toastIdCounter = 0;

  constructor(private http: HttpClient) { }

  ngOnInit() {
    this.isDarkTheme = localStorage.getItem('theme') === 'dark';
    if (this.isDarkTheme) {
      document.body.classList.add('dark-theme');
    }


  }

  showToast(message: string, type: 'success' | 'error' = 'error') {
    const id = this.toastIdCounter++;
    this.toasts.push({ id, message, type });
    setTimeout(() => {
      this.toasts = this.toasts.filter(t => t.id !== id);
    }, 4000);
  }

  removeToast(id: number) {
    this.toasts = this.toasts.filter(t => t.id !== id);
  }

  private extractError(err: HttpErrorResponse | any): string {
    if (err && err.error && err.error.message) {
      return err.error.message;
    } else if (err && err.message) {
      return err.message;
    }
    return 'An unknown server error occurred.';
  }




  fetchUsers() {
    this.http.get<ApiResponse<User>>(`${this.baseUrl}/v1/users`).subscribe(
      res => this.users = res._embedded?.userDtoList || [],
      err => this.showToast('Failed to load users: ' + this.extractError(err), 'error')
    );
  }

  fetchSystemProperties() {
    this.http.get<any[]>(`${this.baseUrl}/system-properties`).subscribe(
      res => {
        const prop = res.find((p: any) => p.propKey === 'dataCleanupDays');
        if (prop) {
          this.dataCleanupDays = parseInt(prop.propValue, 10);
        }
      },
      err => console.error('Failed to load system properties')
    );
  }

  fetchAudits() {
    this.http.get<any[]>(`${this.baseUrl}/audits`).subscribe(
      res => this.audits = res || [],
      err => this.showToast('Failed to load audits', 'error')
    );
  }

  saveSystemConfig() {
    this.http.post(`${this.baseUrl}/system-properties`, {
      propKey: 'dataCleanupDays',
      propValue: this.dataCleanupDays.toString()
    }).subscribe(
      () => this.showToast('System configuration saved. Cleanup set to ' + this.dataCleanupDays + ' days.', 'success'),
      () => this.showToast('Failed to save system configuration', 'error')
    );
  }

  fetchGroups() {
    this.http.get<ApiResponse<Group>>(`${this.baseUrl}/v1/groups`).subscribe(
      res => this.groups = res._embedded?.groupDtoList || [],
      err => this.showToast('Failed to load groups: ' + this.extractError(err), 'error')
    );
  }

  fetchData() {
    // Keep for backward compatibility with inner component triggers if any
    if (document.getElementById('p-dashboard')?.classList.contains('on')) { }
    else if (document.getElementById('p-ds-list')?.classList.contains('on')) { }
    else if (document.getElementById('p-jobs-all')?.classList.contains('on')) { }
    else if (document.getElementById('p-settings-tpl')?.classList.contains('on')) { }
    else if (document.getElementById('p-admin-users')?.classList.contains('on')) { this.fetchUsers(); this.fetchGroups(); }
    else if (document.getElementById('p-admin-groups')?.classList.contains('on')) { this.fetchGroups(); }
  }

  go(id: string, navEl: any, section: string, screen: string) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('on'));
    const p = document.getElementById('p-' + id);
    if (p) p.classList.add('on');

    if (navEl) {
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      navEl.classList.add('active');
    }
    const breadcrumb = document.getElementById('breadcrumb');
    if (breadcrumb) {
      breadcrumb.innerHTML = `<span>${section}</span><span class="breadcrumb-sep">›</span><span class="breadcrumb-cur">${screen}</span>`;
    }

    // Trigger specific APIs based on screen selection
    if (id === 'dashboard') {


    } else if (id === 'ds-list') {

    } else if (id === 'jobs-all') {



    } else if (id === 'settings-tpl') {

    } else if (id === 'settings-system') {
      this.fetchSystemProperties();
    } else if (id === 'system-audit') {
      this.fetchAudits();
    } else if (id === 'admin-users') {
      this.fetchUsers();
      this.fetchGroups();
    } else if (id === 'admin-groups') {
      this.fetchGroups();
    }
  }

  toggle(subId: string, navEl: any) {
    const sub = document.getElementById(subId);
    if (!sub) return;
    const expId = subId.replace('-sub', '-exp');
    const exp = document.getElementById(expId);
    const isOpen = sub.classList.contains('open');
    sub.classList.toggle('open');
    if (exp && exp.parentElement) exp.parentElement.classList.toggle('expanded', !isOpen);
  }

  toggleTheme() {
    this.isDarkTheme = !this.isDarkTheme;
    if (this.isDarkTheme) {
      document.body.classList.add('dark-theme');
      localStorage.setItem('theme', 'dark');
    } else {
      document.body.classList.remove('dark-theme');
      localStorage.setItem('theme', 'light');
    }
  }

  createJob(payload: any) {
    this.http.post(`${this.baseUrl}/v1/jobs`, payload).subscribe(
      res => {
        this.showToast('Job created successfully.', 'success');
        this.fetchData();
        this.go('jobs-all', null, 'Job profiles', 'All jobs');
      },
      err => this.showToast('Failed to create job: ' + this.extractError(err), 'error')
    );
  }


  saveJobTemplate(payload: any) {
    if (payload.id) {
      this.http.put(`${this.baseUrl}/v1/job-template-profiles/${payload.id}`, payload).subscribe(
        res => {
          this.showToast('Job template updated successfully.', 'success');
          this.fetchData();
        },
        err => this.showToast('Failed to update job template: ' + this.extractError(err), 'error')
      );
    } else {
      this.http.post(`${this.baseUrl}/v1/job-template-profiles`, payload).subscribe(
        res => {
          this.showToast('Job template created successfully.', 'success');
          this.fetchData();
        },
        err => this.showToast('Failed to create job template: ' + this.extractError(err), 'error')
      );
    }
  }

  editDatasource(ds: ConnectionProfile) {
    this.newDs = {
      id: ds.id,
      profileName: ds.profileName,
      dbType: ds.dbType || 'PostgreSQL',
      host: ds.host,
      port: typeof ds.port === 'string' ? parseInt(ds.port, 10) : (ds.port || 5432),
      databaseName: ds.databaseName,
      listOfSchemas: ds.listOfSchemas,
      username: ds.user,
      password: ds.pass
    };
    this.testResult = '';
    this.go('ds-new', null, 'Datasources', 'Edit profile');
  }
}
