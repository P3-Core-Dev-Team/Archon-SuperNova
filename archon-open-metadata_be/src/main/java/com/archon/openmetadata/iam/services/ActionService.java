package com.archon.openmetadata.iam.services;

import com.archon.openmetadata.iam.models.Action;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface ActionService {
  Action save(Action entity);

  Action findById(UUID id);

  List<Action> findAll();

  Page<Action> findAll(Specification<Action> spec, Pageable pageable);

  void deleteById(UUID id);
}
