import os
import re

# 1. Update app.component.html for viewJobEvt
app_html = 'src/app/app.component.html'
with open(app_html, 'r') as f:
    a_html = f.read()

if '(viewJobEvt)' not in a_html:
    a_html = a_html.replace('<app-dashboard [datasources]="datasources"', '<app-dashboard (viewJobEvt)="activeMenu=\'settings-job\'; fetchJobs()" [datasources]="datasources"')
    with open(app_html, 'w') as f:
        f.write(a_html)

# 2. Add SystemAuditLog saving to ConnectionProfileController, JobTemplateProfileController, JobController
# To avoid missing injection or compiler errors, I'll just write a centralized AuditAspect or modify the controllers carefully!
# Better yet, modify the service implementations. Wait, I only have JobService, no other interfaces for the other entities.
# I'll modify the controllers carefully.

def inject_audit(file_path, event_type, entity_var_name):
    with open(file_path, 'r') as f:
        c = f.read()
    
    if 'SystemAuditLogRepository' not in c:
        c = c.replace('import org.springframework.web.bind.annotation.*;', 'import org.springframework.web.bind.annotation.*;\nimport com.archon.openmetadata.common.repositories.SystemAuditLogRepository;\nimport com.archon.openmetadata.common.models.SystemAuditLog;\nimport java.time.LocalDateTime;')
        
        # Inject repo
        c = c.replace('private final ModelMapper modelMapper;', 'private final ModelMapper modelMapper;\n  private final SystemAuditLogRepository auditRepo;')
        
        # Update constructor if @RequiredArgsConstructor isn't used
        # Wait, they all use @RequiredArgsConstructor.
        
        # Inject POST audit
        post_audit = f"""
    SystemAuditLog audit = new SystemAuditLog();
    audit.setEventType("{event_type}");
    audit.setDetails("{event_type} created: " + {entity_var_name}.getName());
    audit.setTimestamp(LocalDateTime.now());
    audit.setUsername("system");
    auditRepo.save(audit);
"""
        # We need to replace the POST logic. 
        # In ConnectionProfileController: 
        if event_type == 'Datasource':
            c = re.sub(r'(ConnectionProfile saved = service\.save\([^\)]+\);)', r'\1' + post_audit.replace('.getName()', '.getProfileName()'), c)
        elif event_type == 'JobTemplate':
            c = re.sub(r'(JobTemplateProfile saved = service\.save\([^\)]+\);)', r'\1' + post_audit, c)
        elif event_type == 'Job':
            c = re.sub(r'(Job saved = service\.save\([^\)]+\);)', r'\1' + post_audit.replace('.getName()', '.getJobName()'), c)
        
        with open(file_path, 'w') as f:
            f.write(c)

inject_audit('../archon-open-metadata_be/src/main/java/com/archon/openmetadata/job/controllers/ConnectionProfileController.java', 'Datasource', 'saved')
inject_audit('../archon-open-metadata_be/src/main/java/com/archon/openmetadata/job/controllers/JobTemplateProfileController.java', 'JobTemplate', 'saved')
inject_audit('../archon-open-metadata_be/src/main/java/com/archon/openmetadata/job/controllers/JobController.java', 'Job', 'saved')

