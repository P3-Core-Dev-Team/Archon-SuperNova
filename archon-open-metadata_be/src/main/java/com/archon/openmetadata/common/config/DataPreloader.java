package com.archon.openmetadata.common.config;

import com.archon.openmetadata.iam.models.Action;
import com.archon.openmetadata.iam.models.Group;
import com.archon.openmetadata.iam.models.Role;
import com.archon.openmetadata.iam.models.User;
import com.archon.openmetadata.iam.repositories.ActionRepository;
import com.archon.openmetadata.iam.repositories.GroupRepository;
import com.archon.openmetadata.iam.repositories.RoleRepository;
import com.archon.openmetadata.iam.repositories.UserRepository;
import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.models.JobTemplateOptionRule;
import com.archon.openmetadata.job.models.JobTemplateProfile;
import com.archon.openmetadata.job.repositories.ConnectionProfileRepository;
import com.archon.openmetadata.job.repositories.JobTemplateOptionRuleRepository;
import com.archon.openmetadata.job.repositories.JobTemplateProfileRepository;
import java.time.LocalDateTime;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.CommandLineRunner;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@RequiredArgsConstructor
@Slf4j
public class DataPreloader implements CommandLineRunner {

  private final UserRepository userRepository;
  private final GroupRepository groupRepository;
  private final RoleRepository roleRepository;
  private final ActionRepository actionRepository;
  private final ConnectionProfileRepository connectionProfileRepository;
  private final JobTemplateProfileRepository jobTemplateProfileRepository;
  private final JobTemplateOptionRuleRepository ruleRepository;
  private final com.archon.openmetadata.common.utils.CryptoUtils cryptoUtils;
  
  @Override
  @Transactional
  public void run(String... args) throws Exception {
    PasswordEncoder encoder = new BCryptPasswordEncoder();

    // 1. Seed Actions
    if (actionRepository.count() == 0) {
      log.info("Preloading default actions...");
      saveAction("FULL_ACCESS");
      saveAction("MANAGE_DATASOURCE");
      saveAction("MANAGE_JOB");
      saveAction("VIEW_AUDIT");
      saveAction("VIEW_OUTCOME");
      saveAction("EXPORT_DATA");
    }

    // 2. Seed Roles
    if (roleRepository.count() == 0) {
      log.info("Preloading default roles...");
      saveRole("Role_Admin", List.of(getAction("FULL_ACCESS")));
      saveRole("Role_Developer", List.of(getAction("MANAGE_DATASOURCE"), getAction("MANAGE_JOB")));
      saveRole("Role_Auditor", List.of(getAction("VIEW_AUDIT")));
      saveRole("Role_Analyzer", List.of(getAction("VIEW_OUTCOME"), getAction("EXPORT_DATA")));
    }

    // 3. Seed Groups
    if (groupRepository.count() == 0) {
      log.info("Preloading default groups...");
      saveGroup("ARCHON_OPEN_METADATA_ADMIN", "Full system administration and configuration rights.", List.of(getRole("Role_Admin")));
      saveGroup("ARCHON_OPEN_METADATA_DEVELOPER", "Manage datasource connections and orchestrate profiling jobs.", List.of(getRole("Role_Developer")));
      saveGroup("ARCHON_OPEN_METADATA_AUDITOR", "View system audits and security logs.", List.of(getRole("Role_Auditor")));
      saveGroup("ARCHON_OPEN_METADATA_ANALYZER", "View job outcomes and export metadata results.", List.of(getRole("Role_Analyzer")));
    } else {
      log.info("Backfilling missing descriptions for existing groups...");
      List<Group> existingGroups = groupRepository.findAll();
      for (Group g : existingGroups) {
        if (g.getDescription() == null || g.getDescription().isEmpty()) {
          switch(g.getGroupName()) {
            case "Admin":
              g.setDescription("Full system administration and configuration rights.");
              break;
            case "Developer":
              g.setDescription("Manage datasource connections and orchestrate profiling jobs.");
              break;
            case "Auditor":
              g.setDescription("View system audits and security logs.");
              break;
            case "Analyzer":
              g.setDescription("View job outcomes and export metadata results.");
              break;
          }
          groupRepository.save(g);
        }
      }
    }

    // 4. Seed Users
    if (userRepository.count() == 0) {
      log.info("Preloading default users...");
      saveUser("Admin", "admin@archon.co", "admin123", "Admin", getGroup("ARCHON_OPEN_METADATA_ADMIN"), encoder);
      saveUser("Developer", "dev@archon.co", "dev123", "Developer", getGroup("ARCHON_OPEN_METADATA_DEVELOPER"), encoder);
      saveUser("Auditor", "audit@archon.co", "audit123", "Auditor", getGroup("ARCHON_OPEN_METADATA_AUDITOR"), encoder);
      saveUser("Analyzer", "analyzer@archon.co", "analyzer123", "Analyzer", getGroup("ARCHON_OPEN_METADATA_ANALYZER"), encoder);
    } else {
      log.info("Backfilling missing emails and passwords for existing users...");
      List<User> existingUsers = userRepository.findAll();
      for (User u : existingUsers) {
        boolean modified = false;
        if (u.getEmail() == null || u.getEmail().isEmpty()) {
          u.setEmail(u.getUsername().toLowerCase() + "@archon.co");
          modified = true;
        }
        if (u.getPassword() != null && !u.getPassword().startsWith("$2a$")) {
          u.setPassword(encoder.encode(u.getPassword()));
          modified = true;
        }
        if (u.getStatus() == null) {
          u.setStatus("Active");
          modified = true;
        }
        if (modified) {
          userRepository.save(u);
        }
      }
    }

    // 5. Seed Connection Profiles
    if (connectionProfileRepository.count() == 0) {
      log.info("Preloading default connection profiles...");
      ConnectionProfile cp = new ConnectionProfile();
      cp.setProfileName("Production Postgres");
      cp.setUrl("jdbc:postgresql://db.archon.internal:5432/prod_db");
      cp.setUser("db_admin");
      cp.setPass(cryptoUtils.encrypt("db_secret_pass", null));
      
      String detailsToHash = cp.getUrl() + "|" + cp.getUser();
      String hash = org.springframework.util.DigestUtils.md5DigestAsHex(detailsToHash.getBytes());
      cp.setConnectionHash(hash);
      
      connectionProfileRepository.save(cp);
    }

    // 6. Seed Job Template Profiles & Rules
    if (jobTemplateProfileRepository.count() == 0) {
      log.info("Preloading default job templates and rules...");
      
      JobTemplateProfile profile = new JobTemplateProfile();
      profile.setName("Standard PII Extraction");
      profile.setDescription("Default template for discovering PII and generating ERDs");
      profile = jobTemplateProfileRepository.save(profile);

      JobTemplateOptionRule r1 = new JobTemplateOptionRule();
      r1.setOperationName("SENSITIVE_DETECTION");
      r1.setMinValue(0.8f);
      r1.setMaxValue(1.0f);
      r1.setJobTemplateProfile(profile);
      ruleRepository.save(r1);

      JobTemplateOptionRule r2 = new JobTemplateOptionRule();
      r2.setOperationName("ERD_GENERATION");
      r2.setMinValue(0.0f);
      r2.setMaxValue(1.0f);
      r2.setJobTemplateProfile(profile);
      ruleRepository.save(r2);
    }
  }

  // --- Helper Methods ---

  private Action saveAction(String name) {
    Action action = new Action();
    action.setActionName(name);
    return actionRepository.save(action);
  }

  private Action getAction(String name) {
    return actionRepository.findAll().stream().filter(a -> a.getActionName().equals(name)).findFirst().orElse(null);
  }

  private Role saveRole(String name, List<Action> actions) {
    Role role = new Role();
    role.setRoleName(name);
    role.setActions(actions);
    return roleRepository.save(role);
  }

  private Role getRole(String name) {
    return roleRepository.findAll().stream().filter(r -> r.getRoleName().equals(name)).findFirst().orElse(null);
  }

  private Group saveGroup(String name, String description, List<Role> roles) {
    Group group = new Group();
    group.setGroupName(name);
    group.setDescription(description);
    group.setRoles(roles);
    return groupRepository.save(group);
  }

  private Group getGroup(String name) {
    return groupRepository.findAll().stream().filter(g -> g.getGroupName().equals(name)).findFirst().orElse(null);
  }

  private void saveUser(String username, String email, String password, String role, Group group, PasswordEncoder encoder) {
    User user = new User();
    user.setUsername(username);
    user.setEmail(email);
    user.setPassword(encoder.encode(password));
    user.setAuthType("LOCAL");
    user.setRole(role);
    user.setStatus("Active");
    user.setLastLogin(LocalDateTime.now());
    if (group != null) {
      user.setGroups(List.of(group));
    }
    userRepository.save(user);
  }
}
