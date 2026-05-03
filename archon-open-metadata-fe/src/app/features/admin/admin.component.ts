import { ConnectionProfile, Job, DatasourceForm, User, Group } from '../../core/models/app.models';
import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-admin',
  templateUrl: './admin.component.html',
  styleUrls: ['./admin.component.css']
})
export class AdminComponent {
  @Input() users: User[] = [];
  @Input() groups: Group[] = [];


}
