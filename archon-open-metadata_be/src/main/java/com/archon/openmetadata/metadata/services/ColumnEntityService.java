package com.archon.openmetadata.metadata.services;

import com.archon.openmetadata.metadata.models.ColumnEntity;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface ColumnEntityService {
  ColumnEntity save(ColumnEntity entity);

  ColumnEntity findById(UUID id);

  List<ColumnEntity> findAll();

  Page<ColumnEntity> findAll(Specification<ColumnEntity> spec, Pageable pageable);

  void deleteById(UUID id);
}
