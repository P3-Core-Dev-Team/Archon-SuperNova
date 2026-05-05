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
    name = "relationships",
    indexes = {@Index(name = "idx_rel_job", columnList = "job_id")})
@Data
@EqualsAndHashCode(callSuper = true)
public class Relationship extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "job_id")
  private Job job;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "source_table_id")
  private TableEntity sourceTable;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "target_table_id")
  private TableEntity targetTable;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "source_column_id")
  private ColumnEntity sourceColumn;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "target_column_id")
  private ColumnEntity targetColumn;

  private String cardinality;
  private Float score;
}
