package com.archon.openmetadata.metadata.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.archon.openmetadata.job.models.Job;
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
    name = "schemas",
    indexes = {@Index(name = "idx_schema_job", columnList = "job_id")})
@Data
@EqualsAndHashCode(callSuper = true, exclude = "tables")
@ToString(exclude = "tables")
public class SchemaEntity extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "job_id")
  private Job job;

  @Column(name = "schema_name")
  private String schemaName;

  @Column(name = "schema_size")
  private Long schemaSize;

  @Column(name = "schema_type")
  private String schemaType;

  @Column(name = "datasource_name")
  private String datasourceName;

  @OneToMany(mappedBy = "schema")
  private List<TableEntity> tables;
}
