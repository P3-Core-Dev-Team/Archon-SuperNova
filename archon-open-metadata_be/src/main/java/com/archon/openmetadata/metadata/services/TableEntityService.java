package com.archon.openmetadata.metadata.services;

import com.archon.openmetadata.metadata.models.TableEntity;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface TableEntityService {
  TableEntity save(TableEntity entity);

  TableEntity findById(UUID id);

  List<TableEntity> findAll();

  Page<TableEntity> findAll(Specification<TableEntity> spec, Pageable pageable);

  void deleteById(UUID id);
}
