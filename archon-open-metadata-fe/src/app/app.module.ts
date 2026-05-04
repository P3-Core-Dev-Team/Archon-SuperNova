import { NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { HttpClientModule, HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { RouterModule, Routes } from '@angular/router';
import { TranslateModule, TranslateLoader } from '@ngx-translate/core';
import { TranslateHttpLoader, provideTranslateHttpLoader } from '@ngx-translate/http-loader';

import { AppComponent } from './app.component';
import { SidebarComponent } from './layout/sidebar/sidebar.component';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { DatasourceManagerComponent } from './features/datasource-manager/datasource-manager.component';
import { JobProfileComponent } from './features/job-profile/job-profile.component';
import { AuditTrackingComponent } from './features/audit-tracking/audit-tracking.component';
import { SettingsComponent } from './features/settings/settings.component';
import { AdminComponent } from './features/admin/admin.component';
import { UserProfileComponent } from './features/user-profile/user-profile.component';
import { JobTemplateManagerComponent } from './features/job-template-manager/job-template-manager.component';
import { RelationshipGraphComponent } from './features/job-profile/relationship-graph.component';
import { JobDetailComponent } from './features/job-detail/job-detail.component';
import { PageHeaderComponent } from './shared/components/page-header/page-header.component';
import { PaginationComponent } from './shared/components/pagination/pagination.component';

const routes: Routes = [
  { path: '', redirectTo: '/dashboard', pathMatch: 'full' },
  { path: 'dashboard', component: DashboardComponent },
  { path: 'datasource', component: DatasourceManagerComponent },
  { path: 'job-profile', component: JobProfileComponent },
  { path: 'audit', component: AuditTrackingComponent },
  { path: 'settings', component: SettingsComponent },
  { path: 'settings/job-templates', component: JobTemplateManagerComponent },
  { path: 'admin', component: AdminComponent },
  { path: 'user', component: UserProfileComponent }
];

export function HttpLoaderFactory() {
  return new TranslateHttpLoader();
}

@NgModule({
  declarations: [
    AppComponent,
    SidebarComponent,
    DashboardComponent,
    DatasourceManagerComponent,
    JobProfileComponent,
    AuditTrackingComponent,
    SettingsComponent,
    AdminComponent,
    UserProfileComponent,
    JobTemplateManagerComponent,
    RelationshipGraphComponent,
    JobDetailComponent,
    PageHeaderComponent,
    PaginationComponent
  ],
  imports: [
    BrowserModule,
    HttpClientModule,
    FormsModule,
    RouterModule.forRoot(routes),
    TranslateModule.forRoot({
      loader: {
        provide: TranslateLoader,
        useFactory: HttpLoaderFactory
      }
    })
  ],
  providers: [provideTranslateHttpLoader()],
  bootstrap: [AppComponent]
})
export class AppModule { }
