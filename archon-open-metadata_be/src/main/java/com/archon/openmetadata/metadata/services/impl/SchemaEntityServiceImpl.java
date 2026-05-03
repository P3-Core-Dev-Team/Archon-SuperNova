package com.archon.openmetadata.metadata.services.impl;

import com.archon.openmetadata.metadata.models.SchemaEntity;
import com.archon.openmetadata.metadata.repositories.SchemaEntityRepository;
import com.archon.openmetadata.metadata.services.SchemaEntityService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class SchemaEntityServiceImpl implements SchemaEntityService {
  private final SchemaEntityRepository repository;

  @Override
  public SchemaEntity save(SchemaEntity entity) {
    return repository.save(entity);
  }

  @Override
  public SchemaEntity findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<SchemaEntity> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<SchemaEntity> findAll(Specification<SchemaEntity> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
