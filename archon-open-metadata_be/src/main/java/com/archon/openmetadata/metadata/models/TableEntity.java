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
    name = "tables",
    indexes = {
      @Index(name = "idx_table_job", columnList = "job_id"),
      @Index(name = "idx_table_schema", columnList = "schema_id")
    })
@Data
@EqualsAndHashCode(callSuper = true, exclude = "columns")
@ToString(exclude = "columns")
public class TableEntity extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "job_id")
  private Job job;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "schema_id")
  private SchemaEntity schema;

  @Column(name = "table_name")
  private String tableName;

  @Column(name = "table_size")
  private Long tableSize;

  @Column(name = "table_type")
  private String tableType;

  @Column(name = "schema_name")
  private String schemaName;

  @Column(name = "datasource_name")
  private String datasourceName;

  @OneToMany(mappedBy = "table")
  private List<ColumnEntity> columns;
}
