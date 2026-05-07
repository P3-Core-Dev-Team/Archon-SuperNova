import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ApiService } from '../../core/api.service';
import { TranslateService } from '@ngx-translate/core';
import { SystemProperty } from '../../core/models/app.models';

@Component({
  selector: 'app-settings',
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.css']
})
export class SettingsComponent implements OnInit {
  javaApiUrl = '';
  pythonApiUrl = '';
  currentLanguage = 'en';
  dataCleanupDays = 30;

  constructor(private api: ApiService, private translate: TranslateService, private http: HttpClient) {}

  ngOnInit() {
    this.javaApiUrl = this.api.baseUrl;
    this.pythonApiUrl = this.api.pythonApiUrl;
    this.currentLanguage = localStorage.getItem('language') || 'en';
    this.fetchSystemProperties();
  }

  fetchSystemProperties() {
    this.http.get<SystemProperty[]>('http://localhost:8080/api/system-properties').subscribe(
      res => {
        const prop = res.find((p: SystemProperty) => p.propKey === 'dataCleanupDays');
        if (prop) {
          this.dataCleanupDays = parseInt(prop.propValue, 10);
        }
      },
      err => console.error('Failed to load system properties')
    );
  }

  saveSystemProperties() {
    this.http.post<void>('http://localhost:8080/api/system-properties', {
      propKey: 'dataCleanupDays',
      propValue: this.dataCleanupDays.toString()
    } as SystemProperty).subscribe(
      () => alert('System configuration saved. Cleanup set to ' + this.dataCleanupDays + ' days.'),
      () => alert('Failed to save system configuration')
    );
  }

  saveUrls() {
    this.api.setBaseUrl(this.javaApiUrl);
    this.api.setPythonApiUrl(this.pythonApiUrl);
    alert('API Endpoints saved successfully!');
  }

  changeLanguage(lang: string) {
    this.currentLanguage = lang;
    this.translate.use(lang);
    localStorage.setItem('language', lang);
  }
}
