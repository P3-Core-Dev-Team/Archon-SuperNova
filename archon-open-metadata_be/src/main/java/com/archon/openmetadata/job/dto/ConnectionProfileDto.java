package com.archon.openmetadata.job.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.metadata.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class ConnectionProfileDto extends AuditModelDto {
  private UUID id;
  private String profileName;
  private String url;
  private String user;
  private String pass;
  private String listOfSchemas;
}
