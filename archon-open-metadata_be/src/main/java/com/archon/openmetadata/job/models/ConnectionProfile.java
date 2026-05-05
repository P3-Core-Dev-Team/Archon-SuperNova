package com.archon.openmetadata.job.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import java.util.List;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.ToString;
import org.hibernate.annotations.GenericGenerator;

@Entity
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id")
@Table(
    name = "connection_profiles",
    indexes = {@Index(name = "idx_cp_profile_name", columnList = "profile_name")})
@Data
@EqualsAndHashCode(callSuper = true, exclude = "dbprofileJobs")
@ToString(exclude = "dbprofileJobs")
public class ConnectionProfile extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @Column(name = "profile_name")
  private String profileName;

  private String url;

  @Column(name = "db_user")
  private String user;

  private String pass;

  @Column(name = "list_of_schemas", columnDefinition = "TEXT")
  private String listOfSchemas;

  @Lob
  private String connectionHash;

  @OneToMany(mappedBy = "datasourceProfile")
  private List<Job> dbprofileJobs;
}
