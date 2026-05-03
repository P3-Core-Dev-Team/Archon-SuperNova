package com.archon.openmetadata.metadata.services.impl;

import com.archon.openmetadata.metadata.models.ColumnEntity;
import com.archon.openmetadata.metadata.repositories.ColumnEntityRepository;
import com.archon.openmetadata.metadata.services.ColumnEntityService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ColumnEntityServiceImpl implements ColumnEntityService {
  private final ColumnEntityRepository repository;

  @Override
  public ColumnEntity save(ColumnEntity entity) {
    return repository.save(entity);
  }

  @Override
  public ColumnEntity findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<ColumnEntity> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<ColumnEntity> findAll(Specification<ColumnEntity> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
