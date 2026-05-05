package com.archon.openmetadata.job.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.metadata.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class JobDto extends AuditModelDto {
  private UUID id;
  private String jobName;
  private ConnectionProfileDto datasourceProfile;
  private String listOfSchemas;
  private String status;
  private java.time.LocalDateTime startTime;
  private java.time.LocalDateTime endTime;
  private Long elapsedTime;
  private String auditlogs;
  private JobTemplateProfileDto jobTemplateProfile;
}
