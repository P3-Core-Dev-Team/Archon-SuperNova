package com.archon.openmetadata.iam.services;

import com.archon.openmetadata.iam.models.Group;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface GroupService {
  Group save(Group entity);

  Group findById(UUID id);

  List<Group> findAll();

  Page<Group> findAll(Specification<Group> spec, Pageable pageable);

  void deleteById(UUID id);
}
