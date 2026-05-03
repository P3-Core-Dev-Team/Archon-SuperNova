import { Component } from '@angular/core';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-job-template-manager',
  templateUrl: './job-template-manager.component.html',
  styleUrls: ['./job-template-manager.component.css']
})
export class JobTemplateManagerComponent {
  templateName = '';
  stages = [
    { name: 'STAGE_1_DETECTION', displayName: '1 DETECTION', enabled: true, minScore: 0.8, maxScore: 1.0 },
    { name: 'STAGE_2_SCORING', displayName: '2 SCORING', enabled: true, minScore: 0.5, maxScore: 1.0 },
    { name: 'STAGE_3_CARDINALITY', displayName: '3 CARDINALITY', enabled: false, minScore: 0.0, maxScore: 0.0 },
    { name: 'STAGE_4_PII_SCAN', displayName: '4 PII SCAN', enabled: true, minScore: 0.9, maxScore: 1.0 },
    { name: 'STAGE_5_GROUPING', displayName: '5 GROUPING', enabled: false, minScore: 0.7, maxScore: 1.0 },
    { name: 'STAGE_6_GRAPH', displayName: '6 GRAPH', enabled: true, minScore: 0.0, maxScore: 0.0 },
    { name: 'STAGE_7_CLASSIFICATION', displayName: '7 CLASSIFICATION', enabled: false, minScore: 0.85, maxScore: 1.0 }
  ];

  constructor(private api: ApiService) {}

  saveTemplate() {
    const options = this.stages
      .filter(s => s.enabled)
      .map(s => ({
        operationName: s.name,
        minValue: s.minScore,
        maxValue: s.maxScore
      }));

    const payload = {
      name: this.templateName,
      options: options
    };

    this.api.createJobTemplate(payload).subscribe({
      next: (res) => {
        alert('Job Template Created Successfully!');
        this.templateName = '';
      },
      error: (err) => console.error(err)
    });
  }
}
