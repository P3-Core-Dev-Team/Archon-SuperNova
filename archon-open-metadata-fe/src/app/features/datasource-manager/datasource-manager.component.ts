import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ConnectionProfile, DatasourceForm, ApiResponse } from '../../core/models/app.models';

@Component({
  selector: 'app-datasource-manager',
  templateUrl: './datasource-manager.component.html',
  styleUrls: ['./datasource-manager.component.css']
})
export class DatasourceManagerComponent implements OnInit {
  datasources: ConnectionProfile[] = [];
  newDs: DatasourceForm = { dbType: 'POSTGRESQL', port: 5432, host: '127.0.0.1' };
  showForm = false;
  testResult: string = '';
  private baseUrl = 'http://localhost:8080/api/v1';

  constructor(private http: HttpClient) { }

  ngOnInit() { this.fetchDatasources(); }

  get isDsFormValid(): boolean {
    return !!(this.newDs.profileName && this.newDs.host && this.newDs.port && this.newDs.databaseName && this.newDs.username && this.newDs.password);
  }

  fetchDatasources() {
    this.http.get<ApiResponse<ConnectionProfile>>(`${this.baseUrl}/connection-profiles`).subscribe(res => {
      const list = res._embedded?.connectionProfileDtoList || [];
      this.datasources = list.map((ds: ConnectionProfile) => {
        let dbType = ds.dbType || ''; 
        let host = ds.host || ''; 
        let port: number | string = ds.port || ''; 
        let databaseName = ds.databaseName || '';
        return { ...ds, dbType, host, port, databaseName };
      });
    });
  }

  saveDatasource() {
    const payload: any = {
      profileName: this.newDs.profileName,
      dbType: this.newDs.dbType || 'POSTGRESQL',
      host: this.newDs.host,
      port: Number(this.newDs.port),
      databaseName: this.newDs.databaseName,
      user: this.newDs.username,
      pass: btoa(this.newDs.password || ''),
      listOfSchemas: this.newDs.listOfSchemas
    };

    const req = this.newDs.id ?
      this.http.put<ConnectionProfile>(`${this.baseUrl}/connection-profiles/${this.newDs.id}`, payload) :
      this.http.post<ConnectionProfile>(`${this.baseUrl}/connection-profiles`, payload);

    req.subscribe(() => {
      this.fetchDatasources();
      this.showForm = false;
    });
  }

  deleteDatasource(id: string) {
    this.http.delete<void>(`${this.baseUrl}/connection-profiles/${id}`).subscribe(() => this.fetchDatasources());
  }

  editDatasource(ds: ConnectionProfile) {
    let decodedPass = ds.pass;
    if (ds.pass) {
      try { decodedPass = atob(ds.pass); } catch (e) { decodedPass = ds.pass; }
    }

    this.newDs = {
      id: ds.id,
      profileName: ds.profileName,
      dbType: ds.dbType || 'POSTGRESQL',
      host: ds.host || '127.0.0.1',
      port: typeof ds.port === 'number' ? ds.port : parseInt(ds.port as string) || 5432,
      databaseName: ds.databaseName || '',
      username: ds.user || '',
      password: decodedPass || '',
      listOfSchemas: ds.listOfSchemas || ''
    };
    this.testResult = '';
    this.showForm = true;
  }

  resetDsForm() {
    this.newDs = { dbType: 'POSTGRESQL', port: 5432, host: '127.0.0.1' };
    this.testResult = '';
  }

  testConnection() {
    this.testResult = 'Testing...';
    const payload: any = {
      dbType: this.newDs.dbType || 'POSTGRESQL',
      host: this.newDs.host,
      port: Number(this.newDs.port),
      databaseName: this.newDs.databaseName,
      user: this.newDs.username,
      pass: btoa(this.newDs.password || '')
    };

    this.http.post<any>(`${this.baseUrl}/connection-profiles/test`, payload).subscribe({
      next: (res) => { this.testResult = res.success ? 'Connection successful!' : 'Connection failed!'; },
      error: (err) => { this.testResult = 'Connection failed!'; }
    });
  }
}
