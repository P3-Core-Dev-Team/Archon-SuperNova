package com.metadata.engine.be.metadata_engine_be.repositories;

import com.metadata.engine.be.metadata_engine_be.models.SensitiveColumn;
import org.springframework.data.jpa.repository.JpaRepository;

public interface SensitiveColumnRepository extends JpaRepository<SensitiveColumn, Long> {
    @org.springframework.data.jpa.repository.Modifying
    @org.springframework.transaction.annotation.Transactional
    void deleteByJobId(Long jobId);
    org.springframework.data.domain.Page<SensitiveColumn> findByJobId(Long jobId, org.springframework.data.domain.Pageable pageable);
}
