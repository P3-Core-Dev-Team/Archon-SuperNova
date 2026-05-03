package com.archon.openmetadata.metadata.repositories;

import com.archon.openmetadata.metadata.models.TableEntity;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface TableEntityRepository
    extends JpaRepository<TableEntity, UUID>, JpaSpecificationExecutor<TableEntity> {}
