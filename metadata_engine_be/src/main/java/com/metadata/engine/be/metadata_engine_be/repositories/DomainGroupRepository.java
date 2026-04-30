package com.metadata.engine.be.metadata_engine_be.repositories;

import com.metadata.engine.be.metadata_engine_be.models.DomainGroup;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DomainGroupRepository extends JpaRepository<DomainGroup, Long> {
    @org.springframework.data.jpa.repository.Modifying
    @org.springframework.transaction.annotation.Transactional
    void deleteByJobId(Long jobId);
    org.springframework.data.domain.Page<DomainGroup> findByJobId(Long jobId, org.springframework.data.domain.Pageable pageable);
}
