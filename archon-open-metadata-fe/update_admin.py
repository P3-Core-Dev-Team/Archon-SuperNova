import re

file_path = 'src/app/features/admin/admin.component.ts'
with open(file_path, 'r') as f:
    content = f.read()

content = content.replace("import { Component, Input } from '@angular/core';", "import { Component, OnInit } from '@angular/core';\nimport { HttpClient } from '@angular/common/http';")
content = content.replace("@Input() users: User[] = [];\n  @Input() groups: Group[] = [];", "users: User[] = [];\n  groups: Group[] = [];\n\n  constructor(private http: HttpClient) {}\n\n  ngOnInit() {\n    this.http.get<any>('http://localhost:8080/api/v1/users').subscribe(res => this.users = res._embedded?.userModelList || []);\n    this.http.get<any>('http://localhost:8080/api/v1/groups').subscribe(res => this.groups = res._embedded?.groupModelList || []);\n  }")

with open(file_path, 'w') as f:
    f.write(content)
