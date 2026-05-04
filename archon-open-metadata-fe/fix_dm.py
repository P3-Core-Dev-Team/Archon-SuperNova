import os

dm_ts = 'src/app/features/datasource-manager/datasource-manager.component.ts'
content = """import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-datasource-manager',
  templateUrl: './datasource-manager.component.html',
  styleUrls: ['./datasource-manager.component.css']
})
export class DatasourceManagerComponent implements OnInit {
  datasources: any[] = [];
  newDs: any = { dbType: 'PostgreSQL', port: 5432, host: '127.0.0.1' };
  showForm = false;
  testResult: string = '';
  private baseUrl = 'http://localhost:8080/api/v1';

  constructor(private http: HttpClient) {}

  ngOnInit() { this.fetchDatasources(); }

  get isDsFormValid(): boolean { 
    return !!(this.newDs.profileName && this.newDs.host && this.newDs.port && this.newDs.databaseName && this.newDs.username && this.newDs.password); 
  }

  fetchDatasources() {
    this.http.get<any>(`${this.baseUrl}/connection-profiles`).subscribe(res => { this.datasources = res._embedded?.connectionProfileDtoList || []; });
  }

  saveDatasource() {
    this.http.post(`${this.baseUrl}/connection-profiles`, this.newDs).subscribe(() => { this.fetchDatasources(); this.showForm = false; this.resetDsForm(); });
  }

  deleteDatasource(id: string) {
    this.http.delete(`${this.baseUrl}/connection-profiles/${id}`).subscribe(() => this.fetchDatasources());
  }

  editDatasource(ds: any) {
    this.newDs = { ...ds };
    this.showForm = true;
  }

  resetDsForm() {
    this.newDs = { dbType: 'PostgreSQL', port: 5432, host: '127.0.0.1' };
    this.testResult = '';
    this.showForm = true;
  }

  testConnection() {
    this.testResult = 'Success';
  }
}
"""
with open(dm_ts, 'w') as f:
    f.write(content)
