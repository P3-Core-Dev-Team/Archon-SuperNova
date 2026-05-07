package com.archon.openmetadata.iam.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.hibernate.annotations.GenericGenerator;

@Entity
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id")
@Table(
    name = "users",
    indexes = {@Index(name = "idx_user_username", columnList = "username", unique = true)})
@Data
@EqualsAndHashCode(callSuper = true)
public class User extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  private String username;
  private String password;
  private String email;
  private String role;
  private String status;
  private LocalDateTime lastLogin;

  @Column(name = "last_attempt_password")
  private LocalDateTime lastAttemptPassword;

  @Column(name = "auth_type")
  private String authType;

  @ManyToMany
  @JoinTable(
      name = "user_group_mappings",
      joinColumns = @JoinColumn(name = "user_id"),
      inverseJoinColumns = @JoinColumn(name = "group_id"))
  private List<Group> groups;
}
