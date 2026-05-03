package com.archon.openmetadata.job.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.metadata.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class JobTemplateOptionRuleDto extends AuditModelDto {
  private UUID id;
  private JobTemplateProfileDto jobTemplateProfile;
  private String operationName;
  private Float minValue;
  private Float maxValue;
}
