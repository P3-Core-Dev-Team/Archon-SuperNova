import { Component, OnInit } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-settings',
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.css']
})
export class SettingsComponent implements OnInit {
  javaApiUrl = '';
  pythonApiUrl = '';
  currentLanguage = 'en';

  constructor(private api: ApiService, private translate: TranslateService) {}

  ngOnInit() {
    this.javaApiUrl = this.api.baseUrl;
    this.pythonApiUrl = this.api.pythonApiUrl;
    this.currentLanguage = localStorage.getItem('language') || 'en';
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
