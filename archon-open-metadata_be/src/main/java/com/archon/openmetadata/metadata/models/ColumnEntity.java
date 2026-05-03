package com.archon.openmetadata.metadata.models;

import com.archon.openmetadata.common.models.AuditModel;
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
    name = "columns",
    indexes = {@Index(name = "idx_column_table", columnList = "table_id")})
@Data
@EqualsAndHashCode(callSuper = true)
public class ColumnEntity extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "table_id")
  private TableEntity table;

  @Column(name = "column_name")
  private String columnName;

  @Column(name = "column_type")
  private String columnType;

  @Column(name = "column_length")
  private Integer columnLength;

  private Integer precision;
  private Integer scale;

  @Column(name = "is_primary")
  private Boolean primary;

  @Column(name = "is_index")
  private Boolean indexColumn;
}
