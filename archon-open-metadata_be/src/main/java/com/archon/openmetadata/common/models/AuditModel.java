package com.archon.openmetadata.common.models;

import java.time.LocalDateTime;
import javax.persistence.*;
import lombok.Data;

@MappedSuperclass
@Data
public abstract class AuditModel {
  @Column(name = "created_at", updatable = false)
  private LocalDateTime createdAt;

  @Column(name = "updated_at")
  private LocalDateTime updatedAt;

  @Column(name = "modified_details", columnDefinition = "TEXT")
  private String modifiedDetails;

  @Column(name = "audit_user")
  private String auditUser;

  @PrePersist
  protected void onCreate() {
    createdAt = LocalDateTime.now();
    updatedAt = LocalDateTime.now();
  }

  @PreUpdate
  protected void onUpdate() {
    updatedAt = LocalDateTime.now();
  }
}
