package com.archon.openmetadata.common.repositories;
import com.archon.openmetadata.common.models.SystemProperty;
import org.springframework.data.jpa.repository.JpaRepository;

public interface SystemPropertyRepository extends JpaRepository<SystemProperty, String> {
}
