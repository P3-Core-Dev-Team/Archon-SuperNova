import { ConnectionProfile, Job, DatasourceForm, User, Group, ApiResponse, Role } from '../../core/models/app.models';
import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-admin',
  templateUrl: './admin.component.html',
  styleUrls: ['./admin.component.css']
})
export class AdminComponent implements OnInit {
  users: User[] = [];
  groups: Group[] = [];
  Math = Math;
  activeTab: string = 'users';

  // Pagination
  currentPage = 1;
  pageSize = 5;
  totalItems = 0;



  constructor(private http: HttpClient) { }

  ngOnInit() {
    this.fetchData();
    this.fetchGroups();
  }

  fetchData() {
    this.http.get<ApiResponse<User>>('http://localhost:8080/api/v1/users').subscribe(
      res => {
        const beUsers = res._embedded?.userDtoList || [];
        this.users = beUsers.map((u: User, i: number) => ({
          ...u,
          name: u.username,
          initials: u.username ? u.username.substring(0, 2).toUpperCase() : 'U',
          color: ['#1f6feb', '#10b981', '#d97706', '#6b7280'][i % 4],
          email: u.email || '-',
          group: u.groups && u.groups.length > 0 ? u.groups.map((g: Group) => g.groupName).join(', ') : '-',
          role: u.role || '-',
          lastLogin: u.lastLogin || '-',
          status: u.status || 'Active'
        }));
        this.totalItems = this.users.length;
      },
      err => {
        this.users = [];
        this.totalItems = 0;
      }
    );
  }

  fetchGroups() {
    this.http.get<ApiResponse<Group>>('http://localhost:8080/api/v1/groups').subscribe(
      res => {
        const beGroups = res._embedded?.groupDtoList || [];
        this.groups = beGroups.map((g: Group) => ({
          ...g,
          name: g.groupName,
          isSystem: g.groupName ? ['Admin', 'Developer', 'Auditor', 'Analyzer', 'ARCHON_OPEN_METADATA_ADMIN', 'ARCHON_OPEN_METADATA_DEVELOPER', 'ARCHON_OPEN_METADATA_AUDITOR', 'ARCHON_OPEN_METADATA_ANALYZER'].includes(g.groupName) : false,
          description: g.description || 'n/A',
          usersCount: g.users ? g.users.length : 0,
          roles: g.roles ? g.roles.map((r: Role) => r.roleName).join(', ') : ([] as any)
        }));
      },
      err => {
        this.groups = [];
      }
    );
  }

  setTab(tab: string) {
    this.activeTab = tab;
  }

  get paginatedUsers() {
    const start = (this.currentPage - 1) * this.pageSize;
    return this.users.slice(start, start + this.pageSize);
  }

  get totalPages() {
    return Math.ceil(this.totalItems / this.pageSize);
  }

  nextPage() {
    if (this.currentPage < this.totalPages) this.currentPage++;
  }

  prevPage() {
    if (this.currentPage > 1) this.currentPage--;
  }

  setPage(page: number) {
    this.currentPage = page;
  }
}
