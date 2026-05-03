import { Component } from '@angular/core';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-datasource-manager',
  templateUrl: './datasource-manager.component.html',
  styleUrls: ['./datasource-manager.component.css']
})
export class DatasourceManagerComponent {
  profile = {
    profileName: '',
    url: '',
    user: '',
    pass: '',
    listOfSchemas: ''
  };

  constructor(private api: ApiService) {}

  saveDatasource() {
    this.api.createDatasource(this.profile).subscribe({
      next: (res) => {
        alert('Datasource Created Successfully!');
        this.profile = { profileName: '', url: '', user: '', pass: '', listOfSchemas: '' };
      },
      error: (err) => console.error(err)
    });
  }
}
