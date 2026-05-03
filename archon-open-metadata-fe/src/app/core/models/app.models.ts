export interface ConnectionProfile {
  id?: string;
  profileName?: string;
  url?: string;
  user?: string;
  pass?: string;
  listOfSchemas?: string;
  createdOn?: string;
  updatedOn?: string;
  createdBy?: string;
  updatedBy?: string;
  dbType?: string;
  host?: string;
  port?: number | string;
  databaseName?: string;
}

export interface JobTemplate {
  id?: string;
  name?: string;
}

export interface Job {
  id?: string;
  jobName?: string;
  jobDescription?: string;
  status?: string;
  auditlogs?: string;
  expanded?: boolean;
  createdOn?: string;
  updatedOn?: string;
  datasourceProfile?: {
    profileName?: string;
  };
  jobTemplateProfile?: {
    name?: string;
  };
}

export interface DatasourceForm {
  id?: string;
  profileName?: string;
  dbType: string;
  host?: string;
  port: number;
  databaseName?: string;
  username?: string;
  password?: string;
  listOfSchemas?: string;
}

export interface User {
  id?: string;
  username?: string;
  authType?: string;
  groups?: Group[];
}

export interface Group {
  id?: string;
  groupName?: string;
  roles?: any[];
}

export interface ApiResponse<T> {
  _embedded?: {
    connectionProfileDtoList?: T[];
    jobDtoList?: T[];
    jobTemplateProfileDtoList?: T[];
    userDtoList?: T[];
    groupDtoList?: T[];
    [key: string]: any;
  };
  status?: string;
  auditlogs?: string;
  expanded?: boolean;
  message?: string;
}
