import re

file_path = 'src/app/app.component.ts'
with open(file_path, 'r') as f:
    content = f.read()

# Add imports
imports = """import { Component, OnInit, ViewChild } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { ConnectionProfile, Job, DatasourceForm, ApiResponse, User, Group } from './core/models/app.models';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { DatasourceManagerComponent } from './features/datasource-manager/datasource-manager.component';
import { JobProfileComponent } from './features/job-profile/job-profile.component';
import { JobTemplateManagerComponent } from './features/job-template-manager/job-template-manager.component';
"""
content = re.sub(
    r"import \{ Component, OnInit \} from '@angular/core';[\s\S]*?import \{ ConnectionProfile, Job, DatasourceForm, ApiResponse, User, Group \} from '\./core/models/app\.models';",
    imports.strip(),
    content
)

# Add ViewChild declarations
view_childs = """
  @ViewChild(DashboardComponent) dashboardComp!: DashboardComponent;
  @ViewChild(DatasourceManagerComponent) dsComp!: DatasourceManagerComponent;
  @ViewChild(JobProfileComponent) jpComp!: JobProfileComponent;
  @ViewChild(JobTemplateManagerComponent) tplComp!: JobTemplateManagerComponent;
"""
content = re.sub(r'export class AppComponent implements OnInit \{', 'export class AppComponent implements OnInit {' + view_childs, content)

# Update fetchData
fetch_data = """  fetchData() {
    if (document.getElementById('p-dashboard')?.classList.contains('on')) { this.dashboardComp?.ngOnInit(); }
    else if (document.getElementById('p-ds-list')?.classList.contains('on')) { this.dsComp?.ngOnInit(); }
    else if (document.getElementById('p-jobs-all')?.classList.contains('on')) { this.jpComp?.ngOnInit(); }
    else if (document.getElementById('p-settings-tpl')?.classList.contains('on')) { this.tplComp?.ngOnInit(); }
    else if (document.getElementById('p-admin-users')?.classList.contains('on')) { this.fetchUsers(); this.fetchGroups(); }
    else if (document.getElementById('p-admin-groups')?.classList.contains('on')) this.fetchGroups();
  }"""
content = re.sub(r'  fetchData\(\) \{[\s\S]*?  \}', fetch_data, content)

with open(file_path, 'w') as f:
    f.write(content)

