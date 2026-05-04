package com.archon.openmetadata.metadata.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class DomainGroupDto extends AuditModelDto {
  private UUID id;
  private String groupName;
  private Integer tableCount;
  private String description;
}
