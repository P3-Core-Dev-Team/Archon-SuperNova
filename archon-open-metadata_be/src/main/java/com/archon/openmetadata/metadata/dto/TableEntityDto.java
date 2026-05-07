package com.archon.openmetadata.metadata.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.job.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class TableEntityDto extends AuditModelDto {
  private UUID id;
  private JobDto job;
  private SchemaEntityDto schema;
  private String tableName;
  private Long tableSize;
  private String tableType;
  private String schemaName;
  private String datasourceName;
  private java.util.List<ColumnEntityDto> columns;
}
