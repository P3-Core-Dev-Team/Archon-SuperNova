import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { JobTemplate, JobTemplateOption, ApiResponse } from '../../core/models/app.models';

@Component({
  selector: 'app-job-template-manager',
  templateUrl: './job-template-manager.component.html',
  styleUrls: ['./job-template-manager.component.css']
})
export class JobTemplateManagerComponent implements OnInit {
  jobTemplates: JobTemplate[] = [];
  showForm = false;
  isEditing = false;
  currentTplId: string | null = null;
  newTpl: JobTemplate = { name: '', description: '', options: [] };
  
  pipelineStages = [
    { operationName: 'SCHEMA_EXTRACTION', displayName: '1. Schema Extraction (JDBC)', locked: true },
    { operationName: 'CANDIDATE_FUZZY_MATCHING', displayName: '1.5 ML Candidate Matching', locked: false },
    { operationName: 'SEMANTIC_ANALYSIS', displayName: '2. Context/Semantic Scoring', locked: false },
    { operationName: 'CARDINALITY_DETECTION_SOURCE_COUNT', displayName: '3. Data Cardinality Verification', locked: false },
    { operationName: 'SENSITIVE_ANALYSIS_TABLE_DATA', displayName: '4. SpaCy Deep PII Classification', locked: false },
    { operationName: 'TABLE_DOMAIN_GROUPING', displayName: '5. Domain Vector Aggregation', locked: false },
    { operationName: 'GRAPH_BUILDING_DETECTION', displayName: '6. ERD Graph Context Generation', locked: true },
    { operationName: 'DATA_CLASSIFICATION_TABLE_TYPE', displayName: '7. Entity Classification', locked: false }
  ];

  private baseUrl = 'http://localhost:8080/api/v1';

  constructor(private http: HttpClient) {}

  ngOnInit() { this.fetchJobTemplates(); }

  fetchJobTemplates() {
    this.http.get<ApiResponse<JobTemplate>>(`${this.baseUrl}/job-template-profiles`).subscribe(res => { this.jobTemplates = res._embedded?.jobTemplateProfileDtoList || []; });
  }

  createNew() {
    this.isEditing = false;
    this.currentTplId = null;
    this.newTpl = {
      name: '',
      description: '',
      options: this.pipelineStages.map(s => ({
        operationName: s.operationName,
        displayName: s.displayName,
        locked: s.locked,
        enabled: s.locked, // Pre-enable locked ones
        minValue: 0.0,
        maxValue: 1.0
      }))
    };
    this.showForm = true;
  }

  saveTemplate() {
    const payload: JobTemplate = {
      name: this.newTpl.name,
      description: this.newTpl.description,
      // Only send enabled options to backend
      options: (this.newTpl.options || [])
        .filter((o: JobTemplateOption) => o.enabled)
        .map((o: JobTemplateOption) => ({
          operationName: o.operationName,
          minValue: o.minValue,
          maxValue: o.maxValue
        }))
    };

    if (this.isEditing && this.currentTplId) {
      this.http.put<JobTemplate>(`${this.baseUrl}/job-template-profiles/${this.currentTplId}`, payload).subscribe(() => { this.fetchJobTemplates(); this.showForm = false; });
    } else {
      this.http.post<JobTemplate>(`${this.baseUrl}/job-template-profiles`, payload).subscribe(() => { this.fetchJobTemplates(); this.showForm = false; });
    }
  }

  deleteTemplate(id: string | undefined) {
    if (!id) return;
    this.http.delete<void>(`${this.baseUrl}/job-template-profiles/${id}`).subscribe(() => this.fetchJobTemplates());
  }

  editTemplate(tpl: JobTemplate) {
    this.isEditing = true;
    this.currentTplId = tpl.id || null;
    
    // Map existing backend options to our UI layout
    this.newTpl = {
      name: tpl.name,
      description: tpl.description || '',
      options: this.pipelineStages.map(s => {
        const existing = tpl.options?.find((o: JobTemplateOption) => o.operationName === s.operationName);
        return {
          operationName: s.operationName,
          displayName: s.displayName,
          locked: s.locked,
          enabled: s.locked || !!existing,
          minValue: existing ? existing.minValue : 0.0,
          maxValue: existing ? existing.maxValue : 1.0
        };
      })
    };
    this.showForm = true;
  }
}
