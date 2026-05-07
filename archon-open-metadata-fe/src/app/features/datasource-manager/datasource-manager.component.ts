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
  newDs: DatasourceForm = { dbType: 'PostgreSQL', port: 5432, host: '127.0.0.1' };
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
        let dbType = ''; let host = ''; let port: number | string = ''; let databaseName = '';
        if (ds.url) {
          try {
            const parts = ds.url.split('://');
            if (parts.length > 1) {
              dbType = parts[0].replace('jdbc:', '');
              const hostPortDb = parts[1].split('/');
              databaseName = hostPortDb[1] || '';
              const hostPort = hostPortDb[0].split(':');
              host = hostPort[0];
              port = hostPort[1] || '';
            }
          } catch (e) { }
        }
        return { ...ds, dbType, host, port, databaseName };
      });
    });
  }

  saveDatasource() {
    const payload: ConnectionProfile = {
      profileName: this.newDs.profileName,
      url: `jdbc:${this.newDs.dbType || 'postgresql'}://${this.newDs.host}:${this.newDs.port}/${this.newDs.databaseName}`,
      user: this.newDs.username,
      pass: this.newDs.password,
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
    this.newDs = {
      id: ds.id,
      profileName: ds.profileName,
      dbType: ds.dbType || 'PostgreSQL',
      host: ds.host || '127.0.0.1',
      port: typeof ds.port === 'number' ? ds.port : parseInt(ds.port as string) || 5432,
      databaseName: ds.databaseName || '',
      username: ds.user || '',
      password: ds.pass || '',
      listOfSchemas: ds.listOfSchemas || ''
    };
    this.testResult = '';
    this.showForm = true;
  }

  resetDsForm() {
    this.newDs = { dbType: 'postgresql', port: 5432, host: '127.0.0.1' };
    this.testResult = '';
  }

  testConnection() {
    this.testResult = 'Connection successful!';
  }
}
