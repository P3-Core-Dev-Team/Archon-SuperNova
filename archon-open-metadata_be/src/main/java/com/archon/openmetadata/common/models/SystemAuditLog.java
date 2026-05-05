package com.archon.openmetadata.common.models;

import java.time.LocalDateTime;
import java.util.UUID;
import javax.persistence.*;
import lombok.Data;
import org.hibernate.annotations.GenericGenerator;

@Entity
@Table(name = "system_audit_logs")
@Data
public class SystemAuditLog {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  private LocalDateTime timestamp;
  private String username;
  private String action;
  private String details;

  @PrePersist
  protected void onCreate() {
    timestamp = LocalDateTime.now();
  }
}
