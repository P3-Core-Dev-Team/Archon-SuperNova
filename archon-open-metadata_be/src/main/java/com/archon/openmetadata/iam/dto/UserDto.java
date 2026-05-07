package com.archon.openmetadata.iam.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.job.dto.*;
import com.archon.openmetadata.metadata.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class UserDto extends AuditModelDto {
  private UUID id;
  private String username;
  private String password;
  private String email;
  private String status;
  private java.time.LocalDateTime lastAttemptPassword;
  private String authType;
  private java.util.List<GroupDto> groups;
}
