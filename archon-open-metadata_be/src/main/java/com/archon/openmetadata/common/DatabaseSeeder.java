package com.archon.openmetadata.common;

import com.archon.openmetadata.iam.models.Action;
import com.archon.openmetadata.iam.models.Group;
import com.archon.openmetadata.iam.models.Role;
import com.archon.openmetadata.iam.models.User;
import com.archon.openmetadata.iam.repositories.ActionRepository;
import com.archon.openmetadata.iam.repositories.GroupRepository;
import com.archon.openmetadata.iam.repositories.RoleRepository;
import com.archon.openmetadata.iam.repositories.UserRepository;
import com.archon.openmetadata.job.models.JobTemplateProfile;
import com.archon.openmetadata.job.services.JobTemplateProfileService;
import javax.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Component;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Page;
import java.util.Arrays;
import java.util.List;

@Component
@RequiredArgsConstructor
@Slf4j
public class DatabaseSeeder {

    private final JobTemplateProfileService jobTemplateProfileService;
    private final UserRepository userRepository;
    private final GroupRepository groupRepository;
    private final RoleRepository roleRepository;
    private final ActionRepository actionRepository;

    @PostConstruct
    public void seed() {
        try {
            Page<JobTemplateProfile> existingProfiles = jobTemplateProfileService.findAll(Specification.where(null), Pageable.unpaged());
            if (existingProfiles.isEmpty()) {
                log.info("No JobTemplateProfile found. Creating default 'Standard Discovery' template.");
                JobTemplateProfile defaultProfile = new JobTemplateProfile();
                defaultProfile.setName("Standard Discovery");
                jobTemplateProfileService.save(defaultProfile);
            }
            
            if (userRepository.count() == 0) {
                log.info("No Users found. Creating role-based default users.");
                
                // Create Actions
                Action fullPrivilege = createAction("FULL_PRIVILEGE");
                Action createProfile = createAction("CREATE_PROFILE");
                Action manageJobs = createAction("MANAGE_JOBS");
                Action exportData = createAction("EXPORT_DATA");
                Action viewAudits = createAction("VIEW_AUDITS");
                Action viewAnalysis = createAction("VIEW_ANALYSIS");
                Action compareJobs = createAction("COMPARE_JOBS");

                // Create Roles
                Role adminRole = createRole("AdminRole", Arrays.asList(fullPrivilege));
                Role devRole = createRole("DeveloperRole", Arrays.asList(createProfile, manageJobs, exportData));
                Role auditorRole = createRole("AuditorRole", Arrays.asList(viewAudits));
                Role analyzerRole = createRole("AnalyzerRole", Arrays.asList(viewAnalysis, compareJobs, exportData));

                // Create Groups
                Group adminGroup = createGroup("Admin", Arrays.asList(adminRole));
                Group devGroup = createGroup("Developer", Arrays.asList(devRole));
                Group auditorGroup = createGroup("Auditor", Arrays.asList(auditorRole));
                Group analyzerGroup = createGroup("Analyzer", Arrays.asList(analyzerRole));

                // Create Users
                createUser("admin", "admin123", Arrays.asList(adminGroup));
                createUser("developer", "dev123", Arrays.asList(devGroup));
                createUser("auditor", "auditor123", Arrays.asList(auditorGroup));
                createUser("analyzer", "analyzer123", Arrays.asList(analyzerGroup));
            }
        } catch (Exception e) {
            log.warn("Failed to seed database: {}", e.getMessage(), e);
        }
    }

    private Action createAction(String name) {
        Action action = new Action();
        action.setActionName(name);
        return actionRepository.save(action);
    }

    private Role createRole(String name, List<Action> actions) {
        Role role = new Role();
        role.setRoleName(name);
        role.setActions(actions);
        return roleRepository.save(role);
    }

    private Group createGroup(String name, List<Role> roles) {
        Group group = new Group();
        group.setGroupName(name);
        group.setRoles(roles);
        return groupRepository.save(group);
    }

    private User createUser(String username, String password, List<Group> groups) {
        User user = new User();
        user.setUsername(username);
        user.setPassword(password);
        user.setAuthType("LOCAL");
        user.setGroups(groups);
        return userRepository.save(user);
    }
}
