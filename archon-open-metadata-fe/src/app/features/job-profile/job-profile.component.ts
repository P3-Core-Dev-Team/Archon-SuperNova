import { Job } from '../../core/models/app.models';
import { Component, OnInit, Input, Output, EventEmitter } from '@angular/core';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-job-profile',
  templateUrl: './job-profile.component.html',
  styleUrls: ['./job-profile.component.css']
})
export class JobProfileComponent  {
  @Input() jobs: Job[] = [];
  @Input() datasources: any[] = [];
  @Input() jobTemplates: any[] = [];
  @Output() deleteJobEvt = new EventEmitter<string>();
  @Output() createJobEvt = new EventEmitter<any>();
  @Output() goEvt = new EventEmitter<any>();
  
  showForm = false;
  newJob: any = {};

  deleteJob(id: string | undefined) {
    if(id) this.deleteJobEvt.emit(id);
  }
  
  createJob() {
    const payload = {
      jobName: this.newJob.jobName,
      datasourceProfile: { id: this.newJob.datasourceId },
      jobTemplateProfile: { id: this.newJob.templateId },
      status: 'Pending'
    };
    this.createJobEvt.emit(payload);
    this.showForm = false;
  }
}
