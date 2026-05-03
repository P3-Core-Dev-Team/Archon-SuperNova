import { Component } from '@angular/core';
import { TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-root',
  template: `
    <div class="root">
      <div class="topbar">
        <div class="logo-dot"></div>
        <span class="logo-text">Archon Metadata</span>
        <span class="logo-sub">Core Engine</span>
        <div class="topbar-tabs">
          <a class="ttab" routerLink="/dashboard" routerLinkActive="active">Dashboard</a>
          <a class="ttab" routerLink="/job-profile" routerLinkActive="active">Jobs</a>
          <a class="ttab" routerLink="/datasource" routerLinkActive="active">Profiles</a>
        </div>
        <div class="topbar-status"><div class="pulse"></div>Engine active</div>
      </div>
      <div class="main-layout">
        <app-sidebar></app-sidebar>
        <div class="content">
          <router-outlet></router-outlet>
        </div>
      </div>
    </div>
  `,
  styles: []
})
export class AppComponent {
  constructor(private translate: TranslateService) {
    const savedLang = localStorage.getItem('language') || 'en';
    translate.setDefaultLang('en');
    translate.use(savedLang);
  }
}
