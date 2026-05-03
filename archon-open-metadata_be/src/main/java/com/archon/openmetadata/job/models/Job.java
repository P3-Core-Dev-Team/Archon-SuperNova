package com.archon.openmetadata.job.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import java.time.LocalDateTime;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.hibernate.annotations.GenericGenerator;

@Entity
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id")
@Table(
    name = "jobs",
    indexes = {
      @Index(name = "idx_job_status", columnList = "status"),
      @Index(name = "idx_job_name", columnList = "job_name")
    })
@Data
@EqualsAndHashCode(callSuper = true)
public class Job extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @Column(name = "job_name")
  private String jobName;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "datasource_profile_id")
  private ConnectionProfile datasourceProfile;

  @Column(name = "list_of_schemas", columnDefinition = "TEXT")
  private String listOfSchemas;

  private String status;

  @Column(name = "start_time")
  private LocalDateTime startTime;

  @Column(name = "end_time")
  private LocalDateTime endTime;

  @Column(name = "elapsed_time_ms")
  private Long elapsedTime;

  @Column(columnDefinition = "TEXT")
  private String auditlogs;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "job_template_id")
  private JobTemplateProfile jobTemplateProfile;
}
