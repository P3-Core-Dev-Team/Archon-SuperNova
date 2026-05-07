package com.archon.openmetadata.metadata.repositories;

import com.archon.openmetadata.metadata.models.TableEntity;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

@Repository
public interface TableEntityRepository
    extends JpaRepository<TableEntity, UUID>, JpaSpecificationExecutor<TableEntity> {
    
    @Modifying
    @Transactional
    @Query("DELETE FROM TableEntity t WHERE t.job.id = :jobId")
    void deleteByJobId(@Param("jobId") UUID jobId);
}
