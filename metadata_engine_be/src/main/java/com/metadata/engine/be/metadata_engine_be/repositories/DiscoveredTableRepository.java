package com.metadata.engine.be.metadata_engine_be.repositories;

import com.metadata.engine.be.metadata_engine_be.models.DiscoveredTable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DiscoveredTableRepository extends JpaRepository<DiscoveredTable, Long> {
    @org.springframework.data.jpa.repository.Modifying
    @org.springframework.transaction.annotation.Transactional
    void deleteByJobId(Long jobId);
    org.springframework.data.domain.Page<DiscoveredTable> findByJobId(Long jobId, org.springframework.data.domain.Pageable pageable);
    DiscoveredTable findByTableNameAndJobId(String tableName, Long jobId);
}
