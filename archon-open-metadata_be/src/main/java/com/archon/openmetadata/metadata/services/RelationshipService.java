package com.archon.openmetadata.metadata.services;

import com.archon.openmetadata.metadata.models.Relationship;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface RelationshipService {
  Relationship save(Relationship entity);

  Relationship findById(UUID id);

  List<Relationship> findAll();

  Page<Relationship> findAll(Specification<Relationship> spec, Pageable pageable);

  void deleteById(UUID id);
}
