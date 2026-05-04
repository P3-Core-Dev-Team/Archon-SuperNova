import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { AuditLog, ApiResponse } from '../../core/models/app.models';

@Component({
  selector: 'app-audit-tracking',
  templateUrl: './audit-tracking.component.html',
  styleUrls: ['./audit-tracking.component.css']
})
export class AuditTrackingComponent implements OnInit {
  logs: AuditLog[] = [];

  constructor(private http: HttpClient) {}

  ngOnInit() {
    this.http.get<ApiResponse<AuditLog>>('http://localhost:8080/api/v1/audits').subscribe(
      res => {
        this.logs = res._embedded?.auditLogDtoList || ([] as any);
      },
      err => console.error('Failed to load audit logs', err)
    );
  }
}
