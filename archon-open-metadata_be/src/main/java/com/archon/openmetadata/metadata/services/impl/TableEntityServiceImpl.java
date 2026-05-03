package com.archon.openmetadata.metadata.services.impl;

import com.archon.openmetadata.metadata.models.TableEntity;
import com.archon.openmetadata.metadata.repositories.TableEntityRepository;
import com.archon.openmetadata.metadata.services.TableEntityService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class TableEntityServiceImpl implements TableEntityService {
  private final TableEntityRepository repository;

  @Override
  public TableEntity save(TableEntity entity) {
    return repository.save(entity);
  }

  @Override
  public TableEntity findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<TableEntity> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<TableEntity> findAll(Specification<TableEntity> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
