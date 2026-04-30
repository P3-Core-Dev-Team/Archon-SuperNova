import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { AnalysisService } from './core/services/analysis.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.css']
})
export class AppComponent implements OnInit {
  title = 'metadata_engine_fe';
  connections: any[] = [];
  isLightMode = false;
  
  constructor(private analysisService: AnalysisService) {}
  
  ngOnInit() {
    this.analysisService.getConnections().subscribe(data => {
      this.connections = data;
    });
    
    // Check localStorage for saved theme preference
    const savedTheme = localStorage.getItem('meta-theme');
    if (savedTheme === 'light') {
      this.isLightMode = true;
      document.body.setAttribute('data-theme', 'light');
    }
  }

  toggleTheme() {
    this.isLightMode = !this.isLightMode;
    if (this.isLightMode) {
      document.body.setAttribute('data-theme', 'light');
      localStorage.setItem('meta-theme', 'light');
    } else {
      document.body.removeAttribute('data-theme');
      localStorage.setItem('meta-theme', 'dark');
    }
  }
}
