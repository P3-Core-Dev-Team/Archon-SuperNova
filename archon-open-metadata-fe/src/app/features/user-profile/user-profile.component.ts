import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { UserProfileResponse, User, UserPreferences } from '../../core/models/app.models';

@Component({
  selector: 'app-user-profile',
  templateUrl: './user-profile.component.html',
  styleUrls: ['./user-profile.component.css']
})
export class UserProfileComponent implements OnInit {
  user: User = {};
  preferences: UserPreferences = {};
  initials: string = '';

  constructor(private http: HttpClient) {}

  ngOnInit() {
    this.fetchProfile();
  }

  fetchProfile() {
    this.http.get<UserProfileResponse>('http://localhost:8080/api/v1/profile').subscribe((res: UserProfileResponse) => {
      this.user = res.user || {};
      this.preferences = res.preferences || {};
      this.generateInitials();
    });
  }

  generateInitials() {
    if (this.user?.username) {
      this.initials = this.user.username.substring(0, 2).toUpperCase();
    } else {
      this.initials = 'U';
    }
  }

  saveDetails() {
    this.http.put<void>('http://localhost:8080/api/v1/profile/details', { username: this.user.username })
      .subscribe(() => {
        alert('Details saved successfully');
        this.fetchProfile();
      });
  }

  savePreferences() {
    this.http.put<void>('http://localhost:8080/api/v1/profile/preferences', this.preferences)
      .subscribe(() => {
        alert('Preferences saved successfully');
        this.fetchProfile();
      });
  }
}
