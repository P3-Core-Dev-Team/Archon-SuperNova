package com.archon.openmetadata.metadata.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.job.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class RelationshipDto extends AuditModelDto {
  private UUID id;
  private JobDto job;
  private TableEntityDto sourceTable;
  private TableEntityDto targetTable;
  private ColumnEntityDto sourceColumn;
  private ColumnEntityDto targetColumn;
  private String cardinality;
  private Float score;

  // Flattened fields for UI integration
  private String sourceTableName;
  private String targetTableName;
  private String sourceColumnName;
  private String targetColumnName;
}
