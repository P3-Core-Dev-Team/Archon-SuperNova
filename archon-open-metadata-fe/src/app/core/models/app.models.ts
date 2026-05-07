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
  lastCrawl?: string;
  status?: string;
  tables?: number;
}

export interface JobTemplateOption {
  operationName?: string;
  minValue?: number;
  maxValue?: number;
  enabled?: boolean;
  displayName?: string;
  locked?: boolean;
}

export interface JobTemplate {
  id?: string;
  name?: string;
  description?: string;
  options?: JobTemplateOption[];
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
  source?: string;
  stage?: string;
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

export interface Role {
  id?: string;
  roleName?: string;
  actions?: any[];
}

export interface User {
  id?: string;
  username?: string;
  authType?: string;
  groups?: Group[];
  email?: string;
  role?: string;
  lastLogin?: string;
  status?: string;

  // UI Mapped fields
  name?: string;
  initials?: string;
  color?: string;
  group?: string;
}

export interface Group {
  id?: string;
  groupName?: string;
  description?: string;
  roles?: Role[];
  users?: User[];

  // UI Mapped fields
  name?: string;
  usersCount?: number;
  isSystem?: boolean;
}

export interface DashboardMetrics {
  datasources?: ConnectionProfile[];
  jobs?: Job[];
  recentActivity?: AuditLog[];
  tablesProfiled?: number;
  relationshipsCount?: number;
  sensitiveDataCount?: number;
  tableTypeDistribution?: { [key: string]: number };
}

export interface SystemProperty {
  propKey: string;
  propValue: string;
}

export interface AuditLog {
  id?: string;
  timestamp?: string;
  action?: string;
  user?: string;
  username?: string;
  details?: string;
}

export interface UserPreferences {
  theme?: string;
  notifications?: boolean;
  defaultGraphView?: string;
  timezone?: string;
  dateFormat?: string;
}

export interface UserProfileResponse {
  user?: User;
  preferences?: UserPreferences;
}

export interface ApiResponse<T> {
  _embedded?: {
    connectionProfileDtoList?: T[];
    jobDtoList?: T[];
    jobTemplateProfileDtoList?: T[];
    userDtoList?: T[];
    groupDtoList?: T[];
    auditLogDtoList?: T[];
    [key: string]: any;
  };
  status?: string;
  auditlogs?: string;
  expanded?: boolean;
  message?: string;
}
