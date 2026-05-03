package com.archon.openmetadata.metadata.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.job.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class ColumnEntityDto extends AuditModelDto {
  private UUID id;
  private TableEntityDto table;
  private String columnName;
  private String columnType;
  private Integer columnLength;
  private Integer precision;
  private Integer scale;
  private Boolean primary;
  private Boolean indexColumn;
}
