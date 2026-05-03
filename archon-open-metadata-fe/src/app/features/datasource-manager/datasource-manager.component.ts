import { ConnectionProfile, DatasourceForm } from '../../core/models/app.models';
import { Component, Input, Output, EventEmitter } from '@angular/core';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-datasource-manager',
  templateUrl: './datasource-manager.component.html',
  styleUrls: ['./datasource-manager.component.css']
})
export class DatasourceManagerComponent {
  @Input() datasources: ConnectionProfile[] = [];
  @Input() newDs: any = {};
  @Input() isDsFormValid: boolean = false;
  @Input() testResult: string = '';
  
  @Output() testConnection = new EventEmitter<void>();
  @Output() saveDatasource = new EventEmitter<void>();
  @Output() editDs = new EventEmitter<ConnectionProfile>();
  @Output() deleteDs = new EventEmitter<string>();
  @Output() resetDs = new EventEmitter<void>();
  @Output() goEvt = new EventEmitter<any>();

  resetDsForm() { this.resetDs.emit(); }
  editDatasource(ds: any) { this.editDs.emit(ds); }
  deleteDatasource(id: string | undefined) { if(id) this.deleteDs.emit(id); }
  go(id: string, target: any, section: string, screen: string) { 
    this.goEvt.emit({id, target, section, screen}); 
  }
}
