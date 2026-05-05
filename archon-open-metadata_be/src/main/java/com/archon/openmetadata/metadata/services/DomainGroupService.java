package com.archon.openmetadata.metadata.services;

import com.archon.openmetadata.metadata.models.DomainGroupEntity;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface DomainGroupService {
  DomainGroupEntity save(DomainGroupEntity entity);
  DomainGroupEntity findById(UUID id);
  Page<DomainGroupEntity> findAll(Specification<DomainGroupEntity> spec, Pageable pageable);
  void deleteById(UUID id);
}
