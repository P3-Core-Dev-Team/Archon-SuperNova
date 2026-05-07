package com.archon.openmetadata.metadata.services;

import com.archon.openmetadata.metadata.models.RelationshipEntity;
import java.util.List;
import java.util.UUID;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface RelationshipService {
  RelationshipEntity save(RelationshipEntity entity);

  RelationshipEntity findById(UUID id);

  List<RelationshipEntity> findAll();

  Page<RelationshipEntity> findAll(Specification<RelationshipEntity> spec, Pageable pageable);

  void deleteById(UUID id);
}
