import re

file_path = 'src/app/features/dashboard/dashboard.component.ts'
with open(file_path, 'r') as f:
    content = f.read()

content = content.replace("import { HttpClient } from '@angular/common/http';", "import { HttpClient } from '@angular/common/http';\nimport { Router } from '@angular/router';")
content = content.replace("constructor(private http: HttpClient) {}", "constructor(private http: HttpClient, private router: Router) {}")
content = content.replace("this.viewJobEvt.emit(id);", "this.router.navigate(['/job-profile']);")

with open(file_path, 'w') as f:
    f.write(content)
