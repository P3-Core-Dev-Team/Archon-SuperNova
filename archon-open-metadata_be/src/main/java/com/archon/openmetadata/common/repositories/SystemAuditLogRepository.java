package com.archon.openmetadata.common.repositories;

import com.archon.openmetadata.common.models.SystemAuditLog;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface SystemAuditLogRepository extends JpaRepository<SystemAuditLog, UUID> {
}
