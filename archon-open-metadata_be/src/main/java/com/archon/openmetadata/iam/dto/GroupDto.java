package com.archon.openmetadata.iam.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.job.dto.*;
import com.archon.openmetadata.metadata.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class GroupDto extends AuditModelDto {
  private UUID id;
  private String groupName;
  private String description;
  private java.util.List<RoleDto> roles;
  private java.util.List<UserDto> users;
}
