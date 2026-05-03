package com.archon.openmetadata.common.dto;

import java.time.LocalDateTime;
import lombok.Data;

@Data
public abstract class AuditModelDto {
  private LocalDateTime createdAt;
  private LocalDateTime updatedAt;
  private String modifiedDetails;
  private String auditUser;
}
