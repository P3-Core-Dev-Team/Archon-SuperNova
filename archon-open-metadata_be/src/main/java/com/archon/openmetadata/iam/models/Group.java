package com.archon.openmetadata.iam.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import java.util.List;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.hibernate.annotations.GenericGenerator;

@Entity
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id")
@Table(
    name = "user_groups",
    indexes = {@Index(name = "idx_group_name", columnList = "group_name", unique = true)})
@Data
@EqualsAndHashCode(callSuper = true)
public class Group extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @Column(name = "group_name")
  private String groupName;

  @Column(name = "description")
  private String description;

  @ManyToMany
  @JoinTable(
      name = "group_roles",
      joinColumns = @JoinColumn(name = "group_id"),
      inverseJoinColumns = @JoinColumn(name = "role_id"))
  private List<Role> roles;

  @ManyToMany(mappedBy = "groups")
  @com.fasterxml.jackson.annotation.JsonIgnoreProperties("groups")
  private List<User> users;
}
