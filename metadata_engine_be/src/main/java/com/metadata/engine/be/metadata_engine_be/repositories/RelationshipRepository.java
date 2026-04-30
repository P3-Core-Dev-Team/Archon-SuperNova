package com.metadata.engine.be.metadata_engine_be.repositories;

import com.metadata.engine.be.metadata_engine_be.models.Relationship;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RelationshipRepository extends JpaRepository<Relationship, Long> {
    @org.springframework.data.jpa.repository.Modifying
    @org.springframework.transaction.annotation.Transactional
    void deleteByJobId(Long jobId);
    org.springframework.data.domain.Page<Relationship> findByJobId(Long jobId, org.springframework.data.domain.Pageable pageable);
}
