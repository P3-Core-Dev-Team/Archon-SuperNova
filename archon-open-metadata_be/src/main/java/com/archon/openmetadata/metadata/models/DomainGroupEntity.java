package com.archon.openmetadata.metadata.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.archon.openmetadata.job.models.Job;
import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.hibernate.annotations.GenericGenerator;

@Entity
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id")
@Table(
    name = "domain_groups",
    indexes = {@Index(name = "idx_dg_job", columnList = "job_id")})
@Data
@EqualsAndHashCode(callSuper = true)
public class DomainGroupEntity extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "job_id")
  private Job job;

  @Column(name = "group_name")
  private String groupName;

  @Column(name = "table_count")
  private Integer tableCount;

  @Column(name = "description", length = 1000)
  private String description;
}
