package com.archon.openmetadata.metadata.services.impl;

import com.archon.openmetadata.metadata.models.RelationshipEntity;
import com.archon.openmetadata.metadata.repositories.RelationshipRepository;
import com.archon.openmetadata.metadata.services.RelationshipService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class RelationshipServiceImpl implements RelationshipService {
  private final RelationshipRepository repository;

  @Override
  public RelationshipEntity save(RelationshipEntity entity) {
    return repository.save(entity);
  }

  @Override
  public RelationshipEntity findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<RelationshipEntity> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<RelationshipEntity> findAll(Specification<RelationshipEntity> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
