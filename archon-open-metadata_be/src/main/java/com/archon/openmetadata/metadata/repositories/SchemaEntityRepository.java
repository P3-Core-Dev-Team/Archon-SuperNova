package com.archon.openmetadata.metadata.repositories;

import com.archon.openmetadata.metadata.models.SchemaEntity;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

@Repository
public interface SchemaEntityRepository
    extends JpaRepository<SchemaEntity, UUID>, JpaSpecificationExecutor<SchemaEntity> {
    
    @Modifying
    @Transactional
    @Query("DELETE FROM SchemaEntity s WHERE s.job.id = :jobId")
    void deleteByJobId(@Param("jobId") UUID jobId);
}
