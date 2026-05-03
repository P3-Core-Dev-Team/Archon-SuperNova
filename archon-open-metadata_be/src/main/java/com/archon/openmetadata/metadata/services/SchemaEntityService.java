package com.archon.openmetadata.metadata.services;

import com.archon.openmetadata.metadata.models.SchemaEntity;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface SchemaEntityService {
  SchemaEntity save(SchemaEntity entity);

  SchemaEntity findById(UUID id);

  List<SchemaEntity> findAll();

  Page<SchemaEntity> findAll(Specification<SchemaEntity> spec, Pageable pageable);

  void deleteById(UUID id);
}
