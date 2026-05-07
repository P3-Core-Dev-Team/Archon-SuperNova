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
@Table(name = "job_template_profiles", indexes = { @Index(name = "idx_jtp_name", columnList = "name") })
@Data
@EqualsAndHashCode(callSuper = true, exclude = "options")
@ToString(exclude = "options")
public class JobTemplateProfile extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  private String name;
  private String description;

  private String description;

  @OneToMany(mappedBy = "jobTemplateProfile", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.EAGER)
  private List<JobTemplateOptionRule> options;
}
