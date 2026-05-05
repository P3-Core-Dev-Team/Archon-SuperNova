package com.archon.openmetadata.metadata.repositories;

import com.archon.openmetadata.metadata.models.ColumnEntity;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface ColumnEntityRepository
    extends JpaRepository<ColumnEntity, UUID>, JpaSpecificationExecutor<ColumnEntity> {}
