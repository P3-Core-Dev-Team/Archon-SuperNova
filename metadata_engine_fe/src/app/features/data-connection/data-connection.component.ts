import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { HttpClient, HttpClientModule } from '@angular/common/http';

@Component({
  selector: 'app-data-connection',
  standalone: true,
  imports: [CommonModule, FormsModule, HttpClientModule],
  templateUrl: './data-connection.component.html',
  styleUrls: ['./data-connection.component.css']
})
export class DataConnectionComponent {
  connection = {
    url: 'jdbc:postgresql://127.0.0.1:5432/test',
    username: 'adsuser',
    password: 'AdS@3421',
    schemaName: 'adv'
  };

  isConnecting = false;
  success = false;
  errorMessage = '';

  constructor(private router: Router, private http: HttpClient) { }

  onConnect() {
    this.isConnecting = true;
    this.errorMessage = '';

    this.http.post('http://localhost:8080/api/analysis/connect', this.connection).subscribe({
      next: (res) => {
        this.isConnecting = false;
        this.success = true;
        setTimeout(() => {
          this.router.navigate(['/job'], { queryParams: { src: this.connection.schemaName } });
        }, 1500);
      },
      error: (err) => {
        this.isConnecting = false;
        this.errorMessage = "Connection Failed. Check credentials.";
      }
    });
  }
}
