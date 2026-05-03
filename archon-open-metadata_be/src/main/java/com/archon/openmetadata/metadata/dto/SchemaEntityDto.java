package com.archon.openmetadata.metadata.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.job.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class SchemaEntityDto extends AuditModelDto {
  private UUID id;
  private JobDto job;
  private String schemaName;
  private Long schemaSize;
  private String schemaType;
  private String datasourceName;
}
